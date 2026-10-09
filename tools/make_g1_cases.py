"""Build the G1 tool-call suite (06-experiments.md, Gate G1): 40 cases from the real agent requests.

The script runs one verified turn of the real app against the fake edge and captures the request
bodies. So each case has the exact system prompt, tool schemas, and `tool_choice` of production.
Then it changes the question or the claim, and it adds the tool results of the earlier steps.
Output: tools/g1_cases.json (tools/g1_tests.py sends the cases to the model on node 1).

Usage: PYTHONPATH=. uv run python tools/make_g1_cases.py   (it imports the app package)
"""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "g1_cases.json"

QUESTIONS = [
    "What does Uber do to protect its services from retry storms?",
    "Explain continuous batching and chunked prefill, from my bookmarks.",
    "How do I deploy Mixtral with vLLM on AWS EC2?",
    "Which storage backends does LMCache support?",
    "Which techniques did Character.AI use to cut its inference cost?",
    "Do Go generics make code slower?",
    "How do I upgrade TimescaleDB from 1.x to 2.x?",
    "What is a KV cache, and why does it grow with the context?",
    "How does prefix caching save prefill work?",
    "What is speculative decoding?",
]
CLAIMS = [
    ("The guide installs vLLM 0.2.1.", "B1"), ("LMCache supports CPU, disk, and NIXL backends.", "B2"),
    ("The latest TimescaleDB major version is 2.", "B1"), ("Go 1.18 added generics.", "B3"),
    ("Character.AI uses multi-query attention to cut the KV cache size.", "B2"),
    ("vLLM uses PagedAttention for the KV cache.", "B1"),
]
CHUNKS = """[B1] {title}
url: {url} | saved: 2024-03-13 | page date: unknown
{text}"""
# quick-answer: for each of QUESTIONS[:4], the search query and one chunk that answers the question.
ANSWERS = [
    ("retry storms", "Retry storms at Uber", "https://www.uber.com/blog/retry-storms/",
     "Uber limits retries with retry budgets, and it uses adaptive concurrency limits. "
     "Clients back off with jitter. " * 3),
    ("continuous batching chunked prefill", "Continuous batching and chunked prefill",
     "https://blog.example.com/continuous-batching",
     "Continuous batching adds new requests to the running batch at each decode step, so the GPU does not "
     "wait for the slowest request. Chunked prefill splits a long prompt into chunks, so the decode steps "
     "of other requests run between the chunks. " * 2),
    ("Mixtral vLLM EC2", "Deploy Mixtral with vLLM on AWS EC2", "https://blog.example.com/mixtral-vllm-ec2",
     "Launch a GPU instance with the Deep Learning AMI. Install vLLM with pip. Start the server with "
     "vllm serve mistralai/Mixtral-8x7B-Instruct-v0.1 --tensor-parallel-size 8, then send requests to the "
     "OpenAI-compatible API on port 8000. " * 2),
    ("LMCache storage backends", "LMCache storage backends", "https://docs.lmcache.ai/kv_cache/storage_backends/",
     "LMCache stores KV cache in CPU memory, on local disk, and in remote backends such as Redis, Mooncake, "
     "and NIXL. A vLLM instance loads a stored prefix from the fastest tier. " * 2),
]
# verify-fetch and verify-claimcheck: for each of CLAIMS[:4], the web search, its results, and one page.
EVIDENCE = [
    ("vllm latest release", ["https://docs.vllm.ai/en/latest/", "https://github.com/vllm-project/vllm/releases"],
     ["vLLM 0.30.0 release notes.", "v0.30.0"],
     "vLLM 0.30.0 is the current release. Install it with pip install vllm."),
    ("LMCache storage backends", ["https://docs.lmcache.ai/kv_cache/storage_backends/",
                                  "https://github.com/LMCache/LMCache"],
     ["LMCache storage backends: CPU, local disk, NIXL, Mooncake, Redis.", "LMCache: KV cache layer for LLM serving"],
     "LMCache stores KV cache in CPU memory and on local disk. Remote backends: NIXL, Mooncake, and Redis."),
    ("TimescaleDB latest release", ["https://docs.timescale.com/about/latest/release-notes/",
                                    "https://github.com/timescale/timescaledb/releases"],
     ["TimescaleDB release notes.", "TimescaleDB releases"],
     "The release notes list the 2.x releases. The current major version of TimescaleDB is 2."),
    ("Go 1.18 release notes generics", ["https://go.dev/doc/go1.18", "https://go.dev/blog/intro-generics"],
     ["Go 1.18 Release Notes.", "An Introduction To Generics"],
     "Go 1.18 adds support for generic code with type parameters."),
]


def _results(urls: list[str], snippets: list[str]) -> str:
    return "\n\n".join(f"[{i + 1}] url: {u} | published: unknown | tier: 1\n{s}"
                         for i, (u, s) in enumerate(zip(urls, snippets, strict=True)))


def _body(bodies: list[dict[str, Any]], step: str, first: bool = True) -> dict[str, Any]:
    picks = [b for b in bodies if b["step"] == step]
    return copy.deepcopy((picks[0] if first else picks[-1])["body"])


def _base(body: dict[str, Any], user: str) -> dict[str, Any]:
    body = copy.deepcopy(body)
    system = body["messages"][0]
    body["messages"] = [system, {"role": "user", "content": user}]
    body["stream"] = False
    body.pop("stream_options", None)
    return body


def _tool_turn(body: dict[str, Any], name: str, args: dict[str, Any], result: str, i: int) -> dict[str, Any]:
    body = copy.deepcopy(body)
    call_id = f"call_g1_{i}"
    body["messages"] += [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": call_id, "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": call_id, "content": result},
    ]
    return body


def build(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    quick, agent, verify = _body(bodies, "quick"), _body(bodies, "agent"), _body(bodies, "verify")
    today = "Today: 2026-09-30. Mode: quick."
    cases: list[dict[str, Any]] = []

    def add(group: str, body: dict[str, Any], expect: dict[str, Any]) -> None:
        cases.append({"id": f"{group}-{len(cases) + 1:02d}", "group": group, "body": body, "expect": expect})

    for q in QUESTIONS:  # 10: the first step of the quick agent
        add("quick-search", _base(quick, f"{today}\n\nQuestion: {q}"),
            {"tool": "search_bookmarks", "args": {"query": "nonempty"}})
    gallery = "https://sebastianraschka.com/llm-architecture-gallery/"
    for i in range(6):  # 6: the facts are in a figure, so the agent must look at it
        chunk = CHUNKS.format(title="LLM Architecture Gallery", url=gallery,
                              text=f"Figure {i + 3}: Gemma 3 27B architecture. (The figure is an image.)")
        body = _base(quick, f"{today}\n\nQuestion: What does the Gemma 3 27B figure show about its attention layers?")
        add("quick-figure", _tool_turn(body, "search_bookmarks", {"query": "Gemma 3 27B figure"}, chunk, i),
            {"tool": ["screenshot_page", "fetch_page"], "args": {"url": [gallery]}})
    for i, q in enumerate(QUESTIONS[:4]):  # 4: the chunks answer the question, so the agent calls answer_now
        # The evidence must match the question. G1 (2026-09-28) found that one shared history made the
        # model search again, which is the right behavior for evidence about another topic.
        query, title, url, text = ANSWERS[i]
        chunk = CHUNKS.format(title=title, url=url, text=text)
        body = _base(quick, f"{today}\n\nQuestion: {q}")
        add("quick-answer", _tool_turn(body, "search_bookmarks", {"query": query}, chunk, 100 + i),
            {"tool": "answer_now", "args": {}})
    for i, (claim, ref) in enumerate(CLAIMS):  # 6: the main Deep Agent plans and delegates
        user = (f"Today: 2026-09-30. Mode: verified.\n\nQuestion: Is this still true?\n\nDraft answer:\n{claim} [{ref}]"
                f"\n\nBookmark sources:\n[{ref}] A bookmark | https://example.org/page{i} | saved 2023-05-01 | "
                "page date unknown")
        add("agent-plan", _base(agent, user),
            {"tool": ["write_todos", "task"], "args": {}, "task_subagent": "fact-checker"})
    for claim, ref in CLAIMS:  # 6: the fact-checker searches first
        add("verify-search", _base(verify, f"Claim: {claim} [{ref}]. Question: is this claim still true?"),
            {"tool": "web_search", "args": {"query": "nonempty"}})
    for i, (claim, ref) in enumerate(CLAIMS[:4]):  # 4: then it reads a result (the results match the claim)
        query, urls, snippets, _ = EVIDENCE[i]
        body = _base(verify, f"Claim: {claim} [{ref}]. Question: is this claim still true?")
        add("verify-fetch", _tool_turn(body, "web_search", {"query": query}, _results(urls, snippets), 200 + i),
            {"tool": "fetch_page", "args": {"url": urls}})
    for i, (claim, ref) in enumerate(CLAIMS[:4]):  # 4: then it returns the ClaimCheck
        query, urls, snippets, page_text = EVIDENCE[i]
        domain = urls[0].split("/")[2]
        page = (f"<<<PAGE ref=W1 url={urls[0]} domain={domain} date=unknown tier=1 via=html\n{page_text}\nPAGE>>>")
        body = _base(verify, f"Claim: {claim} [{ref}]. Question: is this claim still true?")
        body = _tool_turn(body, "web_search", {"query": query}, _results(urls, snippets), 300 + i)
        body = _tool_turn(body, "fetch_page", {"url": urls[0], "focus": claim}, page, 400 + i)
        add("verify-claimcheck", body, {"tool": "ClaimCheck", "args": {"claim": "nonempty"},
                                        "claimcheck_url": urls})
    return cases


async def capture_bodies() -> list[dict[str, Any]]:
    from app.llm import make_http_client, make_model
    from app.loadgen.capture import Capture
    from app.prompts import PROMPT_VERSION
    from app.service import Companion, TurnRequest
    from app.tests.fakes import FakeEdge
    from app.tests.test_agent_flow import make_companion, script

    tmp = Path(tempfile.mkdtemp())
    edge = FakeEdge(script)
    base, _ = make_companion(tmp, edge)
    cap = Capture(tmp / "capture.jsonl")
    http = make_http_client(base.s, transport=edge.transport(), capture=cap)
    models = {r: make_model(r, base.s, http, PROMPT_VERSION) for r in ("quick", "agent", "verify")}
    companion = Companion(base.s, base.toolbox, models, base.metrics)
    [e async for e in companion.run(TurnRequest(question="Which vLLM version?", mode="verified"))]
    return [json.loads(x) for x in (tmp / "capture.jsonl").read_text().splitlines()]


def main() -> int:
    cases = build(asyncio.run(capture_bodies()))
    assert len(cases) == 40, len(cases)
    OUT.write_text(json.dumps(cases, indent=1))
    print(f"wrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
