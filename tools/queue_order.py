"""E14 queue order (handout H-68): a 32K RAG retrieve and five short agent steps, ready at the same moment.

Who goes first: our queue (the priority bands of the llm-d flow control) or the engine (vLLM)?
Each round sends the long retrieve and the five agent steps together through edge, and it records
the TTFT and the end time of each request. The arms are flags and variants:
  --long-class interactive | batch      the class of the retrieve (both interactive, or retrieve as batch)
  split on | off                        the router variant (render.py --set pd_mode=decider | off)
The agent steps come from a capture file (verify-loop calls), else from short synthetic prompts.

The long retrieve uses real chunk text from the capture file (tools/corpus.py).

Usage (a Job in the cluster, through cluster/loadgen.sh, which adds --out /data/runs/<run-id>):
  bash cluster/loadgen.sh e14-batch tool -m tools.queue_order --long-class batch --rounds 5 \
    --capture /data/capture/app-calls.jsonl
It writes <out>/queue-order.json.
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

from tools.corpus import corpus_text


def long_retrieve(tokens: int, text: str) -> dict[str, Any]:
    chunks = (text * (1 + tokens * 4 // max(1, len(text))))[: tokens * 4]
    prompt = f"Request {uuid.uuid4().hex}. Answer from the chunks.\n\n{chunks}\n\nQuestion: give the main idea."
    return {"model": "companion", "messages": [{"role": "user", "content": prompt}], "max_tokens": 64,
            "temperature": 0}


def agent_steps(capture: str | None, n: int) -> list[dict[str, Any]]:
    if capture and Path(capture).exists():
        bodies = [json.loads(x)["body"] for x in Path(capture).read_text().splitlines()
                  if x.strip() and json.loads(x).get("step") == "verify"]
        if len(bodies) >= n:
            return [{**b, "stream": False, "max_tokens": 64} for b in bodies[:n]]
    return [{"model": "companion", "max_tokens": 64, "temperature": 0,
             "messages": [{"role": "system", "content": "You check one claim against the live web."},
                          {"role": "user", "content": f"Step {i}: which query checks the claim 'vLLM 0.30 adds X'?"}]}
            for i in range(n)]


async def send(client: httpx.AsyncClient, body: dict[str, Any], kind: str, cls: str, t0: float) -> dict[str, Any]:
    headers = {"X-Tenant-Id": "load-1", "X-Request-Class": cls, "X-Session-Id": uuid.uuid4().hex,
               "X-Step": "queue-order", "X-Deadline-Ms": "120000", "X-Allow-Overflow": "false",
               "X-Request-Id": uuid.uuid4().hex}
    body = {**body, "stream": True, "stream_options": {"include_usage": True}}
    start = time.monotonic()
    rec: dict[str, Any] = {"kind": kind, "class": cls, "sent_s": round(start - t0, 4), "ttft_s": None}
    async with client.stream("POST", "/chat/completions", json=body, headers=headers) as resp:
        rec["status"] = resp.status_code
        if resp.status_code != 200:
            rec["reason"] = (await resp.aread())[:120].decode(errors="replace")
        else:
            async for line in resp.aiter_lines():
                if line.startswith("data:") and line.strip() != "data: [DONE]":
                    data = json.loads(line[5:])
                    delta = ((data.get("choices") or [{}])[0].get("delta") or {})
                    # The first output token of any kind (session 2): the agent steps answer with tool calls or
                    # reasoning first, so a check of content alone gave no TTFT at all.
                    first = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning") \
                        or delta.get("tool_calls")
                    if rec["ttft_s"] is None and first:
                        rec["ttft_s"] = round(time.monotonic() - start, 4)
                    if data.get("usage"):
                        rec["prompt_tokens"] = data["usage"].get("prompt_tokens")
    rec["end_s"] = round(time.monotonic() - t0, 4)
    return rec


async def one_round(client: httpx.AsyncClient, long_body: dict[str, Any], steps: list[dict[str, Any]],
                    long_class: str) -> list[dict[str, Any]]:
    t0 = time.monotonic()
    jobs = [send(client, long_body, "retrieve_32k", long_class, t0)]
    jobs += [send(client, s, "agent_step", "interactive", t0) for s in steps]
    return list(await asyncio.gather(*jobs))


def summarize(rounds: list[list[dict[str, Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in ("retrieve_32k", "agent_step"):
        rs = [r for rnd in rounds for r in rnd if r["kind"] == kind and r.get("ttft_s") is not None]
        out[kind] = {"n": len(rs), "ttft_median_s": round(statistics.median([r["ttft_s"] for r in rs]), 4)
                     if rs else None, "end_median_s": round(statistics.median([r["end_s"] for r in rs]), 4)
                     if rs else None}
    firsts = []
    for rnd in rounds:
        done = [r for r in rnd if r.get("ttft_s") is not None]
        if done:
            firsts.append(min(done, key=lambda r: r["sent_s"] + r["ttft_s"])["kind"])
    out["first_token_went_to"] = {k: firsts.count(k) for k in ("retrieve_32k", "agent_step")}
    return out


async def run(target: str, long_class: str, rounds: int, capture: str | None, tokens: int,
              transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    text = corpus_text(capture)
    all_rounds = []
    async with httpx.AsyncClient(base_url=target.rstrip("/"), timeout=httpx.Timeout(600.0, connect=10.0),
                                 transport=transport) as client:
        for _ in range(rounds):
            all_rounds.append(await one_round(client, long_retrieve(tokens, text), agent_steps(capture, 5),
                                              long_class))
            await asyncio.sleep(2.0)
    return {"long_class": long_class, "rounds": all_rounds, "summary": summarize(all_rounds)}


def main() -> int:
    ap = argparse.ArgumentParser(description="E14: who goes first, our queue or the engine?")
    ap.add_argument("--target", default="http://edge.companion.svc.cluster.local:8080/v1")
    ap.add_argument("--long-class", choices=["interactive", "batch"], default="interactive")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--tokens", type=int, default=28000)  # 32000 gave 30,965 tokens: edge limit 30,000
    ap.add_argument("--capture")
    ap.add_argument("--out", required=True, help="the run folder")
    a = ap.parse_args()
    result = asyncio.run(run(a.target, a.long_class, a.rounds, a.capture, a.tokens))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "queue-order.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
