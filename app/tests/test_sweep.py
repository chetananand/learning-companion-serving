"""Tests of the freshness sweep: batch headers, claim extraction, and rule F2 for each claim."""

from __future__ import annotations

import datetime as dt

from app.llm import make_model
from app.prompts import PROMPT_VERSION
from app.retrieval import Hit
from app.sweep import Sweep, SweepItem, markdown_report, parse_claims, pick
from app.tests.fakes import Call, FakeEdge, Reply
from app.tests.test_agent_flow import BOOKMARK_URL, make_companion, verify_reply


def sweep_script(call: Call) -> Reply:
    if call.step == "sweep":
        return Reply(content='Claims: ["The guide installs vLLM 0.2.1."]')
    return verify_reply(call)


def test_pick_prefers_old_bookmarks_with_volatile_text():
    today = dt.date(2026, 9, 27)
    hits = [Hit("a-0", "a", "Old guide", "https://a.com", "Use vLLM 0.2.1 and 2023 flags", dt.date(2024, 3, 1), None),
            Hit("b-0", "b", "New post", "https://b.com", "vLLM 0.30.0", dt.date(2026, 9, 1), None),
            Hit("c-0", "c", "Essay", "https://c.com", "no numbers here", dt.date(2020, 1, 1), None)]
    assert [i.bookmark_id for i in pick(hits, 5, today=today)] == ["a"]


def test_parse_claims():
    assert parse_claims('x ["a", "b", "c", "d"] y') == ["a", "b", "c"]
    assert parse_claims("no json") == []


async def test_sweep_sends_batch_traffic_and_resolves_each_claim(tmp_path):
    edge = FakeEdge(sweep_script)
    companion, _ = make_companion(tmp_path, edge)
    http = companion.models["quick"].http_async_client
    models = {r: make_model(r, companion.s, http, PROMPT_VERSION) for r in ("sweep", "verify")}
    item = SweepItem("b1", "Deploy Mixtral", BOOKMARK_URL, dt.date(2024, 3, 13), "pip install vllm==0.2.1")
    report = await Sweep(companion.s, companion.toolbox, models).run([item])
    [row] = report["bookmarks"]
    [claim] = row["claims"]
    assert (claim["status"], claim["path"]) == ("updated", "tier") and claim["live"]["value"] == "0.30.0"
    assert {(c.headers["x-tenant-id"], c.headers["x-request-class"]) for c in edge.calls} == {("sweep", "batch")}
    assert {c.headers["x-allow-overflow"] for c in edge.calls} == {"false"}
    assert {c.step for c in edge.calls} == {"sweep", "verify"}
    assert "| updated | tier | 0.30.0 |" in markdown_report(report)
