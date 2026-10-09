"""Tests of the G1 runner: the tool-call checks and the stream timing, against the fake OpenAI server."""

from __future__ import annotations

import argparse
import json
import re

import httpx

from app.tests.fakes import FakeEdge, Reply
from tools import g1_tests as g1


def msg(name, args):
    return {"tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}]}


def test_check_case_rules():
    search = {"expect": {"tool": "search_bookmarks", "args": {"query": "nonempty"}}}
    assert g1.check_case(search, msg("search_bookmarks", {"query": "retry storms"})) == []
    assert g1.check_case(search, msg("search_bookmarks", {"query": " "})) == ["query is empty"]
    assert g1.check_case(search, {"content": "text"}) == ["no tool call"]
    fig = {"expect": {"tool": ["screenshot_page", "fetch_page"], "args": {"url": ["https://g.com/"]}}}
    assert g1.check_case(fig, msg("fetch_page", {"url": "https://g.com/"})) == []
    assert g1.check_case(fig, msg("fetch_page", {"url": "https://other.com/"}))
    plan = {"expect": {"tool": ["write_todos", "task"], "args": {}, "task_subagent": "fact-checker"}}
    assert g1.check_case(plan, msg("task", {"description": "x", "subagent_type": "general-purpose"}))
    cc = {"expect": {"tool": "ClaimCheck", "args": {"claim": "nonempty"}, "claimcheck_url": ["https://d.io/"]}}
    good = {"claim": "c", "pages": [{"url": "https://d.io/", "verdict": "OUTDATED", "corrected_value": "1"}]}
    assert g1.check_case(cc, msg("ClaimCheck", good)) == []
    assert g1.check_case(cc, msg("ClaimCheck", {**good, "pages": [{"url": "https://d.io/", "verdict": "MAYBE"}]}))


def test_the_40_cases_parse_and_have_rules():
    cases = json.loads((g1.ROOT / "tools" / "g1_cases.json").read_text())
    assert len(cases) == 40 and all(c["expect"]["tool"] for c in cases)
    assert all(c["body"]["messages"][0]["role"] == "system" for c in cases)


async def test_stream_timing_and_the_ttft_test():
    edge = FakeEdge(lambda call: Reply(content="one two three four"))
    client = httpx.AsyncClient(transport=edge.transport())
    r = await g1.stream(client, "http://edge.test", {"model": "companion", "messages": []})
    assert r["status"] == 200 and r["text"].split() == ["one", "two", "three", "four"] and len(r["times"]) == 4
    a = argparse.Namespace(base="http://edge.test", tokens=500, limit_s=5.0)
    result = await g1.t_ttft(client, a)
    assert result["pass"] and len(result["runs"]) == 5 and result["runs"][0]["prompt_tokens"] == 1000


async def test_prefix_test_counts_bad_outputs():
    edge = FakeEdge(lambda call: Reply(content="" if "Question 3:" in json.dumps(call.body) else "fine"))
    client = httpx.AsyncClient(transport=edge.transport())
    a = argparse.Namespace(base="http://edge.test", metrics=None, concurrency=4, requests=8)
    result = await g1.t_prefix(client, a)
    assert result["bad_count"] == 1 and not result["pass"]


STOP = {"what", "does", "from", "which", "this", "still", "true", "claim", "question", "bookmarks", "with",
        "explain", "support", "supports", "latest", "major", "version", "added", "guide", "installs"}


def test_each_later_step_has_evidence_about_its_own_question():
    """G1 (2026-09-28): one shared history made the model search again. The evidence must match the question."""
    cases = json.loads((g1.ROOT / "tools" / "g1_cases.json").read_text())
    later = [c for c in cases if c["group"] in ("quick-answer", "verify-fetch", "verify-claimcheck")]
    assert len(later) == 12
    for c in later:
        msgs = c["body"]["messages"]
        user = next(m["content"] for m in msgs if m["role"] == "user")
        topic = user.split("Question:")[-1] if c["group"] == "quick-answer" else user.split("[")[0]
        words = {w.strip(".,?") for w in re.findall(r"[a-z][a-z0-9.]{3,}", topic.lower())} - STOP
        evidence = " ".join(m["content"] for m in msgs if m["role"] == "tool").lower()
        assert any(w in evidence for w in words), (c["id"], words)


def hop_server(cached: int) -> httpx.MockTransport:
    """A decode sidecar that answers 42 and reports `cached` prompt tokens (the LMCache hop loaded them)."""
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [{"choices": [{"delta": {"content": "42"}}]},
                  {"choices": [], "usage": {"prompt_tokens": 3100, "prompt_tokens_details": {"cached_tokens": cached}}}]
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


async def test_split_passes_on_the_lmcache_hop_evidence():
    a = argparse.Namespace(base="http://decode:8000", metrics=None, prefill="10.0.0.1:8000")
    hop = await g1.t_split(httpx.AsyncClient(transport=hop_server(3072)), a)
    assert hop["pass"] and hop["cached_tokens"] == 3072 and hop["nixl_delta"] == {}
    none = await g1.t_split(httpx.AsyncClient(transport=hop_server(0)), a)
    assert none["pass"] is False  # a correct answer with no hop evidence is not a split


def test_a_quote_of_nan_is_not_garbage():
    from tools.g1_tests import is_garbage
    assert not is_garbage("One fact from the notes is that there is an open bug where NaN logits occur after")
    assert is_garbage("") and is_garbage("nan nan nan") and is_garbage("NaN NaN ok")
