"""E7 again (Wednesday): long sessions that span a prefix-cache clear, for the ghost count.

Tuesday's E7 found no ghost to count: the replay sessions are short, so no session sent a request both before and
after the clear, and the LMCache tier gave the cleared prefixes back at once. This probe fixes both points:
  - Each session has its own long system prompt (real corpus text and the session id). It sends one turn every
    `interval` seconds for `duration` seconds, and its history grows, so the prefix of a session stays warm on
    the pod that the router picks for it.
  - The run uses layout A with no KV connector (two whole pods, no CPU tier), so the router has a real choice of
    pod, and a cleared prefix does not come back from a tier.
The runbook clears the prefix cache of pod B (vllm-decode) at `--clear-at` seconds. tools/ghosts.py then counts the
ghosts in the Envoy log: requests of warm sessions that the router still sends to pod B, and that miss the cache.

Usage (a Job in the cluster, through cluster/loadgen.sh, which adds --out /data/runs/<run-id>):
  bash cluster/loadgen.sh e7b-precise tool -m tools.ghost_probe --target <gateway>/v1 --capture $CAP
It writes <out>/ghost-probe.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from tools.corpus import corpus_text, long_text


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 4)


async def turn(client: httpx.AsyncClient, session: str, tenant: str, messages: list[dict[str, str]],
               t0: float) -> dict[str, Any]:
    headers = {"X-Tenant-Id": tenant, "X-Request-Class": "interactive", "X-Session-Id": session,
               "X-Step": "ghost", "X-Request-Id": uuid.uuid4().hex}
    body = {"model": "companion", "messages": messages, "max_tokens": 32, "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}}
    start = time.monotonic()
    rec: dict[str, Any] = {"session": session, "t_s": round(start - t0, 3), "ttft_s": None, "text": ""}
    try:
        async with client.stream("POST", "/chat/completions", json=body, headers=headers) as resp:
            rec["status"] = resp.status_code
            if resp.status_code != 200:
                rec["reason"] = (await resp.aread())[:120].decode(errors="replace")
                return rec
            async for line in resp.aiter_lines():
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                data = json.loads(line[5:])
                delta = ((data.get("choices") or [{}])[0].get("delta") or {})
                piece = delta.get("content") or delta.get("reasoning_content") or ""
                if rec["ttft_s"] is None and (piece or delta.get("tool_calls")):
                    rec["ttft_s"] = round(time.monotonic() - start, 4)
                rec["text"] += delta.get("content") or ""
                if data.get("usage"):
                    u = data["usage"]
                    rec["prompt_tokens"] = u.get("prompt_tokens")
                    rec["cached_tokens"] = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    except httpx.HTTPError as exc:
        rec["status"], rec["reason"] = 599, type(exc).__name__
    return rec


async def session_loop(client: httpx.AsyncClient, i: int, text: str, prefix_tokens: int, interval: float,
                       duration: float, start_delay: float, t0: float, out: list[dict[str, Any]]) -> None:
    session = f"ghost-{i:03d}"
    tenant = f"load-{i % 32 + 1}"
    notes = long_text(prefix_tokens, 900 + i, text)
    system = f"Session {session}. Answer from these notes in one short line.\n\n{notes}"
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    await asyncio.sleep(start_delay)
    k = 0
    while time.monotonic() - t0 < duration:
        k += 1
        messages.append({"role": "user", "content": f"Turn {k}: give one more fact from the notes."})
        rec = await turn(client, session, tenant, list(messages), t0)
        rec["turn"] = k
        out.append({key: v for key, v in rec.items() if key != "text"})
        messages.append({"role": "assistant", "content": rec.get("text") or "(no answer)"})
        await asyncio.sleep(interval)


def summarize(records: list[dict[str, Any]], clear_at: float | None) -> dict[str, Any]:
    def phase(rs: list[dict[str, Any]]) -> dict[str, Any]:
        ok = [r for r in rs if r.get("status") == 200]
        ttfts = [r["ttft_s"] for r in ok if r.get("ttft_s") is not None]
        prompt = sum(r.get("prompt_tokens") or 0 for r in ok)
        cached = sum(r.get("cached_tokens") or 0 for r in ok)
        return {"requests": len(rs), "ok": len(ok), "ttft_p50_s": pct(ttfts, 0.5), "ttft_p95_s": pct(ttfts, 0.95),
                "token_hit_ratio": round(cached / prompt, 3) if prompt else None}
    out: dict[str, Any] = {"all": phase(records)}
    if clear_at is not None:
        out["before_clear"] = phase([r for r in records if r["t_s"] < clear_at])
        out["first_60s_after"] = phase([r for r in records if clear_at <= r["t_s"] < clear_at + 60])
        out["after_clear"] = phase([r for r in records if r["t_s"] >= clear_at])
    sessions = {r["session"] for r in records}
    spanning = {s for s in sessions
                if clear_at is not None and any(r["t_s"] < clear_at for r in records if r["session"] == s)
                and any(r["t_s"] >= clear_at for r in records if r["session"] == s)}
    out["sessions"] = len(sessions)
    out["sessions_spanning_the_clear"] = len(spanning)
    turns = [sum(1 for r in records if r["session"] == s) for s in sessions]
    out["turns_per_session_median"] = statistics.median(turns) if turns else 0
    return out


async def run(target: str, sessions: int, interval: float, duration: float, prefix_tokens: int,
              capture: str | None, clear_at: float | None = None,
              transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    text = corpus_text(capture)
    records: list[dict[str, Any]] = []
    t0 = time.monotonic()
    async with httpx.AsyncClient(base_url=target.rstrip("/"), timeout=httpx.Timeout(300.0, connect=10.0),
                                 transport=transport) as client:
        spread = min(interval, 30.0)  # the sessions start over the first interval
        await asyncio.gather(*(session_loop(client, i, text, prefix_tokens, interval, duration,
                                            spread * i / max(1, sessions), t0, records) for i in range(sessions)))
    records.sort(key=lambda r: r["t_s"])
    return {"config": {"sessions": sessions, "interval_s": interval, "duration_s": duration,
                       "prefix_tokens": prefix_tokens, "clear_at_s": clear_at},
            "records": records, "summary": summarize(records, clear_at)}


def main() -> int:
    ap = argparse.ArgumentParser(description="E7 again: long sessions that span a prefix-cache clear")
    ap.add_argument("--target", default="http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1")
    ap.add_argument("--sessions", type=int, default=24)
    ap.add_argument("--interval", type=float, default=12.0)
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--prefix-tokens", type=int, default=3000)
    ap.add_argument("--clear-at", type=float, default=150.0, help="when the runbook clears pod B (for the summary)")
    ap.add_argument("--capture")
    ap.add_argument("--out", required=True, help="the run folder")
    a = ap.parse_args()
    result = asyncio.run(run(a.target, a.sessions, a.interval, a.duration, a.prefix_tokens, a.capture, a.clear_at))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "ghost-probe.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
