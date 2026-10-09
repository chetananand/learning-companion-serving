"""E4 hop proof and E5 split threshold (06-experiments.md): direct requests to the P/D pods.

Both run as a Job in the cluster (cluster/loadgen.sh ... tool -m tools.pd_probe ...). The Job sends its
requests to the pods, not to edge, because the probes must set the split exactly:
  split     POST to the routing sidecar of the decode pod (:8000) with x-prefiller-host-port: <prefill>:8000
  no split  POST to the same sidecar without the header: the decode pod computes the whole prompt
  seed      POST straight to vLLM on the decode pod (:8200), so that its prefix cache holds a known prefix

hop (E4):   seed the decode pod with a known prefix of 4K tokens. Then send split requests that share the
            prefix (the decode pod has it) and split requests that do not. The moved bytes of each group come
            from the LMCache server (the base hop, --lmcache) or from the NIXL counters (preset nixl-hop).
            The notebook compares them with the formula bytes for the full prompt and the uncached part.
split (E5): keep 16 decode streams running on the decode pod. Send requests with 1K to 16K uncached tokens,
            each size with and without a split. Record the TTFT of each probe and the inter-token gaps of
            the 16 streams while the probe waits for its first token.

Usage:
  bash cluster/loadgen.sh e4-hop tool -m tools.pd_probe hop --decode {decode} --prefill {prefill} \\
    --lmcache {lmcache} --capture /data/capture/frozen.jsonl
  bash cluster/loadgen.sh e5-d2560 tool -m tools.pd_probe split --decode {decode} --prefill {prefill} \\
    --capture /data/capture/app-calls.jsonl
It writes <out>/hop.json or <out>/split.json.
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
from tools.g1_tests import delta, metrics, pct

MODEL = "companion"
PATTERN = r"^vllm:(nixl_|prefix_cache_|num_preemptions)"
# The LMCache MP server (the base hop since G0): moved bytes, lookups, and stores.
LMCACHE_PATTERN = r"^lmcache_mp_(transfer_phase_bytes|lookup_hit_tokens|lookup_requested_tokens|num_finished_stores)"


def body(messages: list[dict[str, Any]], max_tokens: int, *, ignore_eos: bool = False) -> dict[str, Any]:
    return {"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0,
            "ignore_eos": ignore_eos, "stream": True, "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": False}}


async def stream(client: httpx.AsyncClient, base: str, payload: dict[str, Any], *,
                 headers: dict[str, str] | None = None, stop: asyncio.Event | None = None) -> dict[str, Any]:
    """One streaming request. Returns the status, the TTFT, the arrival time of each content chunk, and the usage."""
    t0 = time.monotonic()
    out: dict[str, Any] = {"t_send": t0, "status": 0, "ttft_s": None, "times": [], "usage": {}}
    try:
        async with client.stream("POST", f"{base}/v1/chat/completions", json=payload, headers=headers or {}) as resp:
            out["status"] = resp.status_code
            if resp.status_code != 200:
                out["error"] = (await resp.aread())[:300].decode(errors="replace")
                return out
            async for line in resp.aiter_lines():
                if stop is not None and stop.is_set():
                    break  # closing the stream aborts the request in vLLM
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                data = json.loads(line[5:])
                out["usage"] = data.get("usage") or out["usage"]
                if ((data.get("choices") or [{}])[0].get("delta") or {}).get("content"):
                    now = time.monotonic()
                    out["times"].append(now)
                    if out["ttft_s"] is None:
                        out["ttft_s"] = round(now - t0, 4)
    except httpx.HTTPError as exc:
        out["status"], out["error"] = 599, type(exc).__name__
    out["t_end"] = time.monotonic()
    return out


def row(r: dict[str, Any]) -> dict[str, Any]:
    usage = r.get("usage") or {}
    return {"status": r["status"], "ttft_s": r["ttft_s"], "prompt_tokens": usage.get("prompt_tokens"),
            "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "error": r.get("error")}


def lmcache_totals(d: dict[str, float]) -> dict[str, float]:
    """Moved bytes and hit tokens from the deltas of the LMCache server counters (not the _created series)."""
    d = {k: v for k, v in d.items() if "_created" not in k.split("{")[0]}
    return {"bytes": sum(v for k, v in d.items() if "transfer_phase_bytes" in k),
            "hit_tokens": sum(v for k, v in d.items() if "lookup_hit_tokens" in k)}


def nixl_totals(d: dict[str, float]) -> dict[str, float]:
    """The transferred bytes and the transfer count from the deltas of the NIXL metrics (histogram or counter)."""
    def base(k: str) -> str:
        return k.split("{")[0]
    nixl = {k: v for k, v in d.items() if base(k).startswith("vllm:nixl_") and not base(k).endswith("_bucket")}
    return {"bytes": sum(v for k, v in nixl.items() if "bytes" in k and not base(k).endswith("_count")),
            "transfers": sum(v for k, v in nixl.items() if "bytes" in k and base(k).endswith("_count"))}


# E4


async def hop(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    text = corpus_text(a.capture)
    sidecar, vllm = f"http://{a.decode}:8000", f"http://{a.decode}:8200"
    split = {"x-prefiller-host-port": f"{a.prefill}:8000"}
    prefix = f"Known prefix {uuid.uuid4().hex}.\n\n" + long_text(a.prefix_tokens, 11, text)

    def messages(system: str, i: int | str, seed: int) -> list[dict[str, Any]]:
        question = f"Question {i} ({uuid.uuid4().hex}): give one fact from the notes.\n\n"
        return [{"role": "system", "content": system},
                {"role": "user", "content": question + long_text(a.suffix_tokens, seed, text)}]

    seed = await stream(client, vllm, body(messages(prefix, "seed", 1), 1))
    groups: dict[str, Any] = {}
    lm_url = f"{a.lmcache}/metrics" if getattr(a, "lmcache", None) else None
    for group in ("shared", "unshared"):
        before = await metrics(client, f"{vllm}/metrics", PATTERN)
        lm_before = await metrics(client, lm_url, LMCACHE_PATTERN)
        rows = []
        for i in range(a.requests):
            system = prefix if group == "shared" else (
                f"Other prefix {uuid.uuid4().hex}.\n\n" + long_text(a.prefix_tokens, 1000 + i, text))
            seed_i = 2000 + i if group == "shared" else 3000 + i
            rows.append(row(await stream(client, sidecar, body(messages(system, i, seed_i), 16), headers=split)))
        d = delta(before, await metrics(client, f"{vllm}/metrics", PATTERN))
        lm = delta(lm_before, await metrics(client, lm_url, LMCACHE_PATTERN))
        groups[group] = {"requests": rows, "metric_delta": d, **nixl_totals(d),
                         "lmcache_delta": lm, "lmcache": lmcache_totals(lm)}
    return {"prefix_tokens": a.prefix_tokens, "suffix_tokens": a.suffix_tokens, "seed": row(seed),
            "groups": groups}


# E5


def window_gaps(gaps: list[tuple[float, float]], lo: float, hi: float) -> list[float]:
    """The inter-token gaps of the background streams that end inside [lo, hi]."""
    return [g for t, g in gaps if lo <= t <= hi]


async def split(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    text = corpus_text(a.capture)
    sidecar = f"http://{a.decode}:8000"
    split_headers = {"x-prefiller-host-port": f"{a.prefill}:8000"}
    stop = asyncio.Event()
    gaps: list[tuple[float, float]] = []  # (arrival time, gap) of each token of the background streams

    async def background(k: int) -> None:
        n = 0
        while not stop.is_set():
            notes = long_text(1000, 3000 + 100 * k + n, text)
            prompt = f"Stream {k}-{n} {uuid.uuid4().hex}.\n\n{notes}\n\nExplain these notes at length."
            r = await stream(client, sidecar, body([{"role": "user", "content": prompt}], a.stream_tokens,
                                                   ignore_eos=True), stop=stop)
            times = r["times"]
            gaps.extend((t2, t2 - t1) for t1, t2 in zip(times, times[1:], strict=False))
            n += 1
            await asyncio.sleep(0.05)

    tasks = [asyncio.create_task(background(k)) for k in range(a.streams)]
    await asyncio.sleep(a.warm_s)
    probes: list[dict[str, Any]] = []
    for rnd in range(a.rounds):
        for size in a.sizes:
            for mode in ("split", "local"):
                prompt = (f"Probe {uuid.uuid4().hex}.\n\n" + long_text(size, 5000 + 10 * size + rnd, text)
                          + "\n\nGive the main idea in one line.")
                r = await stream(client, sidecar, body([{"role": "user", "content": prompt}], 16),
                                 headers=split_headers if mode == "split" else None)
                first = r["times"][0] if r["times"] else r["t_end"]
                probes.append({"round": rnd, "size": size, "mode": mode, **row(r),
                               "t_send": r["t_send"], "t_first": first})
                await asyncio.sleep(a.gap_s)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)

    windows = [(p["t_send"], p["t_first"] + 0.2) for p in probes]
    for p, (lo, hi) in zip(probes, windows, strict=True):
        w = window_gaps(gaps, lo, hi)
        p["itl_window"] = {"n": len(w), "p50_s": pct(w, 0.5), "p95_s": pct(w, 0.95),
                           "max_s": round(max(w), 4) if w else None}
    baseline = [g for t, g in gaps if not any(lo <= t <= hi for lo, hi in windows)]
    summary: dict[str, Any] = {}
    for size in a.sizes:
        for mode in ("split", "local"):
            ps = [p for p in probes if p["size"] == size and p["mode"] == mode and p["status"] == 200]
            ttfts = [p["ttft_s"] for p in ps if p["ttft_s"] is not None]
            p95s = [p["itl_window"]["p95_s"] for p in ps if p["itl_window"]["p95_s"] is not None]
            summary[f"{size}-{mode}"] = {
                "ok": len(ps), "ttft_median_s": round(statistics.median(ttfts), 4) if ttfts else None,
                "window_itl_p95_median_s": round(statistics.median(p95s), 4) if p95s else None,
                "prompt_tokens": ps[0]["prompt_tokens"] if ps else None}
    return {"streams": a.streams, "sizes": a.sizes, "rounds": a.rounds,
            "baseline_itl": {"n": len(baseline), "p50_s": pct(baseline, 0.5), "p95_s": pct(baseline, 0.95)},
            "summary": summary, "probes": probes}


TESTS = {"hop": hop, "split": split}


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="E4 hop proof and E5 split threshold, straight to the P/D pods")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in TESTS:
        s = sub.add_parser(name)
        s.add_argument("--decode", required=True, help="the IP of the decode pod")
        s.add_argument("--prefill", required=True, help="the IP of the prefill pod")
        s.add_argument("--capture", help="the capture file: real chunk text for the prompts")
        s.add_argument("--lmcache", help="the LMCache server, http://<node ip>:8080 (the base hop, G0)")
        s.add_argument("--out", required=True, help="the run folder")
        if name == "hop":
            s.add_argument("--prefix-tokens", type=int, default=4000)
            s.add_argument("--suffix-tokens", type=int, default=1000)
            s.add_argument("--requests", type=int, default=8)
        else:
            s.add_argument("--sizes", type=lambda v: [int(x) for x in v.split(",")],
                           default=[1000, 2000, 4000, 8000, 16000])
            s.add_argument("--streams", type=int, default=16)
            s.add_argument("--stream-tokens", type=int, default=6000)
            s.add_argument("--rounds", type=int, default=3)
            s.add_argument("--warm-s", type=float, default=15.0)
            s.add_argument("--gap-s", type=float, default=3.0)
    a = ap.parse_args()

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0),
                                     limits=httpx.Limits(max_connections=64)) as client:
            return await TESTS[a.cmd](client, a)

    result = asyncio.run(run())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.cmd}.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k not in ("probes",)}, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
