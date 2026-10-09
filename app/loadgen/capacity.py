"""E1: measured capacity against the paper value (06-experiments.md, E1).

Send N concurrent requests of a set prompt length with `max_tokens` 512, and increase N. Each prompt
starts with a unique line, so no prefix-cache hit hides the prefill. `ignore_eos` makes each output
512 tokens long, so the KV use is the same for each request. The notebook reads
`vllm:num_preemptions_total` from the saved Prometheus scrape and finds the first N with preemptions.

Run: python -m app.loadgen.capacity --tokens 8000 --levels 1,2,4,8,16,24,32 --out /data/runs/e1-8k
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from app.loadgen.replay import LOAD_TENANTS, CallResult, summarize

CHARS_PER_TOKEN = 4


def make_prompt(pool: list[str], tokens: int, rng: random.Random) -> str:
    """A prompt of about `tokens` tokens from real bookmark text, with a unique first line."""
    head = f"Request {uuid.uuid4().hex}. Read the notes below.\n\n"
    budget = tokens * CHARS_PER_TOKEN - len(head) - 80
    parts, size = [], 0
    while size < budget:
        piece = rng.choice(pool)
        parts.append(piece)
        size += len(piece) + 2
    return head + "\n\n".join(parts)[:budget] + "\n\nSummarize the notes above in detail."


def body(prompt: str, model: str, max_tokens: int) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "stream": True, "stream_options": {"include_usage": True}, "ignore_eos": True, "temperature": 0}


async def level(client: httpx.AsyncClient, pool: list[str], n: int, *, tokens: int, max_tokens: int, model: str,
                repeats: int, rng: random.Random, log: Any) -> list[CallResult]:
    results: list[CallResult] = []

    async def worker(w: int) -> None:
        for r in range(repeats):
            tenant = LOAD_TENANTS[w % len(LOAD_TENANTS)]  # the tenant windows must not limit E1
            res = CallResult(f"e1-n{n}", f"cap-{n}-{w}", r, "capacity", "interactive", tenant, True, time.time())
            t0 = time.monotonic()
            headers = {"X-Tenant-Id": tenant, "X-Session-Id": f"cap-{uuid.uuid4().hex[:8]}",
                       "X-Request-Class": "interactive", "X-Step": "capacity", "X-Deadline-Ms": "300000",
                       "X-Allow-Overflow": "false", "X-Request-Id": uuid.uuid4().hex}
            try:
                async with client.stream("POST", "/chat/completions", json=body(make_prompt(pool, tokens, rng), model,
                                                                                 max_tokens), headers=headers) as resp:
                    res.status = resp.status_code
                    if resp.status_code != 200:
                        res.reason = (await resp.aread())[:80].decode(errors="replace")
                    else:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                                continue
                            data = json.loads(line[5:])
                            delta = ((data.get("choices") or [{}])[0].get("delta") or {})
                            if res.ttft_s is None and delta.get("content"):
                                res.ttft_s = round(time.monotonic() - t0, 4)
                            if data.get("usage"):
                                res.prompt_tokens = data["usage"].get("prompt_tokens")
                                res.output_tokens = data["usage"].get("completion_tokens")
            except httpx.HTTPError as exc:
                res.status, res.reason = 599, type(exc).__name__
            res.latency_s = round(time.monotonic() - t0, 4)
            log.write(json.dumps({**asdict(res), "level": n, "target_tokens": tokens}) + "\n")
            results.append(res)

    await asyncio.gather(*(worker(w) for w in range(n)))
    return results


async def run(target: str, pool: list[str], levels: list[int], *, tokens: int, max_tokens: int, model: str,
              repeats: int, out: Path, pause_s: float = 10.0,
              transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(3)
    report: dict[str, Any] = {"tokens": tokens, "max_tokens": max_tokens, "levels": {}}
    async with httpx.AsyncClient(base_url=target.rstrip("/"), transport=transport,
                                 timeout=httpx.Timeout(600.0, connect=5.0)) as client:
        with (out / "client.jsonl").open("a", encoding="utf-8") as log:
            for n in levels:
                t0 = time.monotonic()
                results = await level(client, pool, n, tokens=tokens, max_tokens=max_tokens, model=model,
                                      repeats=repeats, rng=rng, log=log)
                report["levels"][str(n)] = {"t_start": time.time(), **summarize(results, time.monotonic() - t0)}
                await asyncio.sleep(pause_s)
    (out / "summary.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="E1: concurrency sweep with long prompts")
    ap.add_argument("--tokens", type=int, default=8000)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--levels", default="1,2,4,8,16,24,32")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--model", default="companion")
    ap.add_argument("--target", default="http://edge.companion.svc.cluster.local:8080/v1")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from app.config import get_settings
    from app.loadgen.questions import corpus

    s = get_settings()
    pool = [b["text"] for b in asyncio.run(corpus(s.qdrant_url, s.collection)) if len(b["text"]) > 500]
    report = asyncio.run(run(a.target, pool, [int(x) for x in a.levels.split(",")], tokens=a.tokens,
                             max_tokens=a.max_tokens, model=a.model, repeats=a.repeats, out=Path(a.out)))
    print(json.dumps({k: v["by_class"] for k, v in report["levels"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
