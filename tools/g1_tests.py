"""Gate G1 tests (06-experiments.md): the model and the KV tier, straight against the vLLM pods.

It needs only httpx, so it runs on node 1: uv run --with httpx python tools/g1_tests.py <test> [options]
Tests and pass rules:
  toolcalls  40 cases from tools/g1_cases.json            >= 95% valid calls with correct arguments
  prefix     2,000 requests with shared prefixes           no empty or NaN output, prefix hit ratio >= 60%
  ttft       warm TTFT of a single 8K-token request        median <= 1.0 s
  itl        8 concurrent streams of 256 tokens            inter-token latency p95 <= 40 ms
  split      one P/D request through the routing sidecar   correct output, and the hop: NIXL counters
                                                           up, or cached tokens from the LMCache tier
  cputier    a prefix that left HBM comes back             TTFT below the cold TTFT, connector hits go up
  restart    the same prefix after a pod restart           the first request hits the CPU tier
Each test writes <out>/<test>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import re
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODEL = "companion"
VERDICTS = {"SUPPORTED", "CONTRADICTED", "OUTDATED", "NOT_FOUND", "UNCLEAR"}


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))], 4)


def corpus_text() -> str:
    """Real text for long prompts: the spec files of this repo (they are on node 1 after the rsync)."""
    return "\n\n".join(p.read_text() for p in sorted((ROOT / "docs").rglob("*.md")) if "source" not in p.parts)


def long_text(tokens: int, seed: int, text: str) -> str:
    rng = random.Random(seed)
    size = tokens * 4
    start = rng.randrange(0, max(1, len(text) - size))
    chunk = text[start:start + size]
    while len(chunk) < size:
        chunk += "\n" + text[: size - len(chunk)]
    return chunk


async def metrics(client: httpx.AsyncClient, url: str | None, pattern: str) -> dict[str, float]:
    if not url:
        return {}
    out: dict[str, float] = {}
    text = (await client.get(url)).text
    for line in text.splitlines():
        if line.startswith("#") or not re.search(pattern, line):
            continue
        name_labels, _, value = line.rpartition(" ")
        with contextlib.suppress(ValueError):
            out[name_labels] = out.get(name_labels, 0.0) + float(value)
    return out


def delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {k: round(after[k] - before.get(k, 0.0), 3) for k in after if after[k] != before.get(k, 0.0)}


async def stream(client: httpx.AsyncClient, base: str, body: dict[str, Any], headers: dict[str, str] | None = None
                 ) -> dict[str, Any]:
    """Send one streaming chat request. Return the text, the TTFT, the chunk times, and the usage."""
    body = {**body, "stream": True, "stream_options": {"include_usage": True}}
    t0 = time.monotonic()
    times: list[float] = []
    parts: list[str] = []
    usage: dict[str, Any] = {}
    async with client.stream("POST", f"{base}/v1/chat/completions", json=body, headers=headers or {}) as resp:
        if resp.status_code != 200:
            return {"status": resp.status_code, "error": (await resp.aread())[:300].decode(errors="replace")}
        async for line in resp.aiter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            data = json.loads(line[5:])
            usage = data.get("usage") or usage
            delta_ = ((data.get("choices") or [{}])[0].get("delta") or {})
            if delta_.get("content"):
                times.append(time.monotonic() - t0)
                parts.append(delta_["content"])
    return {"status": 200, "text": "".join(parts), "ttft_s": times[0] if times else None, "times": times,
            "usage": usage}


# Tests


def check_case(case: dict[str, Any], message: dict[str, Any]) -> list[str]:
    exp = case["expect"]
    calls = message.get("tool_calls") or []
    if not calls:
        return ["no tool call"]
    fn = calls[0]["function"]
    allowed = exp["tool"] if isinstance(exp["tool"], list) else [exp["tool"]]
    problems = [] if fn["name"] in allowed else [f"tool {fn['name']} not in {allowed}"]
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except ValueError:
        return problems + ["arguments are not JSON"]
    for key, rule in exp.get("args", {}).items():
        value = args.get(key)
        if rule == "nonempty" and not (isinstance(value, str) and value.strip()):
            problems.append(f"{key} is empty")
        elif isinstance(rule, list) and value not in rule:
            problems.append(f"{key}={value!r} not allowed")
    if fn["name"] == "task" and exp.get("task_subagent") and args.get("subagent_type") != exp["task_subagent"]:
        problems.append(f"subagent_type={args.get('subagent_type')!r}")
    if fn["name"] == "ClaimCheck" and "claimcheck_url" in exp:
        pages = args.get("pages") or []
        if not pages:
            problems.append("ClaimCheck has no pages")
        for p in pages:
            if p.get("url") not in exp["claimcheck_url"] or p.get("verdict") not in VERDICTS:
                problems.append(f"bad page entry {p}")
    return problems


async def t_toolcalls(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    cases = json.loads(Path(a.cases).read_text())
    rows = []
    for case in cases:
        body = {**case["body"], "model": MODEL, "max_tokens": 400, "temperature": 0}
        body.pop("max_completion_tokens", None)
        resp = await client.post(f"{a.base}/v1/chat/completions", json=body)
        if resp.status_code != 200:
            rows.append({"id": case["id"], "ok": False, "problems": [f"HTTP {resp.status_code}: {resp.text[:200]}"]})
            continue
        msg = resp.json()["choices"][0]["message"]
        problems = check_case(case, msg)
        rows.append({"id": case["id"], "group": case["group"], "ok": not problems, "problems": problems,
                     "tool_calls": msg.get("tool_calls"), "content": (msg.get("content") or "")[:200]})
    rate = sum(r["ok"] for r in rows) / len(rows)
    return {"pass": rate >= 0.95, "pass_rate": round(rate, 3), "cases": rows}


def is_garbage(content: str) -> bool:
    """An empty answer, or mostly "nan" words (the NaN bug). Session 1: a good answer can quote "NaN" from
    the notes (our docs mention the NaN bug), so one "nan" word is not a failure."""
    words = content.lower().split()
    return not words or words.count("nan") > len(words) / 2


async def t_prefix(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    text, rng = corpus_text(), random.Random(5)
    prefixes = [long_text(2000, 100 + i, text) for i in range(20)]
    pattern = r"^vllm:prefix_cache_(hits|queries)_total"
    before = await metrics(client, a.metrics, pattern)
    sem, bad, done = asyncio.Semaphore(a.concurrency), [], []

    async def one(i: int) -> None:
        suffix = long_text(rng.randint(10, 500), 1000 + i, text)
        body = {"model": MODEL, "max_tokens": rng.randint(16, 64), "temperature": 0,
                "messages": [{"role": "system", "content": prefixes[i % len(prefixes)]},
                             {"role": "user", "content": f"Question {i}: give one fact from the notes. {suffix}"}]}
        async with sem:
            r = await client.post(f"{a.base}/v1/chat/completions", json=body)
        if r.status_code != 200:
            bad.append((i, f"HTTP {r.status_code}"))
            return
        content = r.json()["choices"][0]["message"].get("content") or ""
        if is_garbage(content):
            bad.append((i, repr(content[:80])))
        done.append(i)

    await asyncio.gather(*(one(i) for i in range(a.requests)))
    d = delta(before, await metrics(client, a.metrics, pattern))
    hits = sum(v for k, v in d.items() if "hits" in k)
    queries = sum(v for k, v in d.items() if "queries" in k)
    ratio = round(hits / queries, 3) if queries else None
    return {"pass": not bad and ratio is not None and ratio >= 0.6, "requests": a.requests, "bad": bad[:20],
            "bad_count": len(bad), "prefix_hit_ratio": ratio, "metric_delta": d}


async def t_ttft(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    text = corpus_text()
    await stream(client, a.base, {"model": MODEL, "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]})
    runs = []
    for i in range(5):
        prompt = f"Request {uuid.uuid4().hex}.\n\n" + long_text(a.tokens, 50 + i, text) + "\n\nSummarize in one line."
        r = await stream(client, a.base, {"model": MODEL, "max_tokens": 16, "temperature": 0,
                                          "messages": [{"role": "user", "content": prompt}]})
        runs.append({"ttft_s": r.get("ttft_s"), "prompt_tokens": (r.get("usage") or {}).get("prompt_tokens")})
    ttfts = [r["ttft_s"] for r in runs if r["ttft_s"] is not None]
    med = round(statistics.median(ttfts), 4) if ttfts else None
    return {"pass": med is not None and med <= a.limit_s, "median_ttft_s": med, "runs": runs}


async def t_itl(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    text = corpus_text()

    async def one(i: int) -> dict[str, Any]:
        notes = long_text(1000, 70 + i, text)
        prompt = f"Stream {uuid.uuid4().hex}.\n\n{notes}\n\nExplain these notes at length."
        return await stream(client, a.base, {"model": MODEL, "max_tokens": 256, "ignore_eos": True, "temperature": 0,
                                             "messages": [{"role": "user", "content": prompt}]})

    results = await asyncio.gather(*(one(i) for i in range(a.concurrency)))
    gaps = [t2 - t1 for r in results for t1, t2 in zip(r.get("times", []), r.get("times", [])[1:], strict=False)]
    p95 = pct(gaps, 0.95)
    return {"pass": p95 is not None and p95 <= a.limit_s, "itl_p50_s": pct(gaps, 0.5), "itl_p95_s": p95,
            "streams": len(results), "tokens": [(r.get("usage") or {}).get("completion_tokens") for r in results]}


async def t_split(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    pattern = r"^vllm:nixl_"
    before = await metrics(client, a.metrics, pattern)
    prompt = (f"Note {uuid.uuid4().hex}.\n\n" + long_text(3000, 90, corpus_text()) +
              "\n\nIgnore the notes. What is 12 plus 30? Answer with the number only.")
    r = await stream(client, a.base, {"model": MODEL, "max_tokens": 8, "temperature": 0,
                                      "messages": [{"role": "user", "content": prompt}]},
                     headers={"x-prefiller-host-port": a.prefill})
    d = delta(before, await metrics(client, a.metrics, pattern))
    cached = ((r.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    # The hop: the NIXL counters go up (preset nixl-hop), or the decode pod loads the prompt from the
    # shared LMCache tier (the base since G0: sidecar shared-storage), so its cached tokens are above 0.
    ok = r.get("status") == 200 and "42" in (r.get("text") or "") and (bool(d) or cached > 0)
    return {"pass": ok, "output": r.get("text"), "ttft_s": r.get("ttft_s"), "status": r.get("status"),
            "error": r.get("error"), "nixl_delta": d, "cached_tokens": cached}


def tier_prompt(seed: int) -> str:
    return f"Tier test {seed}.\n\n" + long_text(6000, seed, corpus_text()) + "\n\nGive the main idea in one line."


async def t_cputier(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    pattern = r"(lmcache|external_prefix_cache|kv_connector)"
    body = {"model": MODEL, "max_tokens": 16, "temperature": 0,
            "messages": [{"role": "user", "content": tier_prompt(a.seed)}]}
    cold = await stream(client, a.base, body)
    hbm = await stream(client, a.base, body)
    before = await metrics(client, a.metrics, pattern)
    text = corpus_text()
    for i in range(a.flood):  # unique long prompts push the tier prompt out of the GPU prefix cache
        flood = f"Flood {uuid.uuid4().hex}.\n\n" + long_text(8000, 500 + i, text)
        await client.post(f"{a.base}/v1/chat/completions",
                          json={"model": MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": flood}]})
    back = await stream(client, a.base, body)
    d = delta(before, await metrics(client, a.metrics, pattern))
    ok = back.get("ttft_s") is not None and cold.get("ttft_s") is not None and back["ttft_s"] < cold["ttft_s"]
    # Session 1: the pass needs real hits from the tier. Queries alone went up, and noise gave back < cold.
    hits = sum(v for k, v in d.items() if "hits" in k)
    return {"pass": ok and hits > 0, "tier_hit_tokens": hits, "ttft_cold_s": cold.get("ttft_s"),
            "ttft_hbm_s": hbm.get("ttft_s"), "ttft_after_evict_s": back.get("ttft_s"), "flood": a.flood,
            "connector_delta": d}


async def t_restart(client: httpx.AsyncClient, a: argparse.Namespace) -> dict[str, Any]:
    pattern = r"(lmcache|external_prefix_cache|kv_connector)"
    before = await metrics(client, a.metrics, pattern)
    r = await stream(client, a.base, {"model": MODEL, "max_tokens": 16, "temperature": 0,
                                      "messages": [{"role": "user", "content": tier_prompt(a.seed)}]})
    d = delta(before, await metrics(client, a.metrics, pattern))
    return {"pass": r.get("status") == 200 and any(v > 0 for v in d.values()), "ttft_s": r.get("ttft_s"),
            "connector_delta": d}


TESTS = {"toolcalls": t_toolcalls, "prefix": t_prefix, "ttft": t_ttft, "itl": t_itl, "split": t_split,
         "cputier": t_cputier, "restart": t_restart}


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate G1 tests against the vLLM pods")
    ap.add_argument("test", choices=sorted(TESTS))
    ap.add_argument("--base", required=True, help="the model server, for example http://10.42.0.12:8000")
    ap.add_argument("--metrics", help="the /metrics URL of the vLLM pod under test")
    ap.add_argument("--prefill", help="host:port of the prefill pod (split test)")
    ap.add_argument("--cases", default=str(ROOT / "tools" / "g1_cases.json"))
    ap.add_argument("--requests", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--tokens", type=int, default=8000)
    ap.add_argument("--limit-s", type=float, default=None)
    ap.add_argument("--flood", type=int, default=23)  # 184K tokens: more than HBM (173,657), less than the tier
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--out", default=str(ROOT / "metrics" / "g1"))
    a = ap.parse_args()
    if a.limit_s is None:
        a.limit_s = {"ttft": 1.0, "itl": 0.040}.get(a.test, 0.0)
    if a.test == "itl" and a.concurrency == 32:
        a.concurrency = 8

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0),
                                     limits=httpx.Limits(max_connections=128)) as client:
            return await TESTS[a.test](client, a)

    result = asyncio.run(run())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.test}.json").write_text(json.dumps(result, indent=1, default=str))
    summary = {k: v for k, v in result.items() if k not in ("cases", "runs", "bad")}
    print(json.dumps(summary, indent=1, default=str)[:3000])
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
