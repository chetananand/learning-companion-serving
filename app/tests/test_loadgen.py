"""Tests of the load generator: capture, scripts, mixes, the replayer, E1 capacity, and the question sets."""

from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

import httpx
import pytest

from app.llm import make_http_client, make_model
from app.loadgen.capacity import make_prompt
from app.loadgen.capacity import run as capacity_run
from app.loadgen.capture import Capture
from app.loadgen.questions import build_sets, drive
from app.loadgen.replay import ReplayConfig, Replayer
from app.loadgen.scripts import draft_scripts, load_capture, mix_picker, turn_scripts, verify_loops
from app.prompts import PROMPT_VERSION
from app.service import Companion, TurnRequest
from app.sweep import Sweep, SweepItem
from app.tests.fakes import FakeEdge, Reply
from app.tests.test_agent_flow import BOOKMARK_URL, make_companion, script
from app.tests.test_sweep import sweep_script


def both(call):
    return sweep_script(call) if call.step == "sweep" else script(call)


async def capture_run(tmp_path: Path) -> Path:
    path = tmp_path / "capture.jsonl"
    edge = FakeEdge(both)
    companion, _ = make_companion(tmp_path, edge)
    cap = Capture(path)
    http = make_http_client(companion.s, transport=edge.transport(), capture=cap)
    models = {r: make_model(r, companion.s, http, PROMPT_VERSION) for r in ("quick", "agent", "verify", "sweep")}
    captured = Companion(companion.s, companion.toolbox, models, companion.metrics)
    for sid, mode in (("b2-0001", "quick"), ("b1-0002", "quick"), ("b3-0003", "verified")):
        [e async for e in captured.run(TurnRequest(question="Which vLLM version?", mode=mode, session_id=sid))]
    item = SweepItem("b1", "Deploy Mixtral", BOOKMARK_URL, dt.date(2024, 3, 13), "pip install vllm==0.2.1")
    await Sweep(companion.s, companion.toolbox, models).run([item])
    return path


async def test_capture_and_scripts(tmp_path):
    records = load_capture([await capture_run(tmp_path)])
    assert {r["step"] for r in records} == {"quick", "agent", "verify", "sweep"}
    assert all("messages" in r["body"] for r in records)
    turns = turn_scripts(records)
    kinds = sorted(s.kind for s in turns)
    assert kinds.count("quick") == 2 and "verified" in kinds and "sweep" in kinds
    quick = next(s for s in turns if s.label == "b2")
    assert [c.stream for c in quick.calls] == [False, False, True]
    assert {s.label for s in draft_scripts(records)} >= {"b1", "b2"}
    loops = verify_loops(records)
    assert loops and all(c.step == "verify" for s in loops for c in s.calls)
    sweep = next(s for s in turns if s.kind == "sweep")
    assert sweep.request_class == "batch"
    rng = random.Random(1)
    assert mix_picker("m1", records)(rng).kind == "draft"
    assert {mix_picker("m2", records)(rng).kind for _ in range(40)} <= {"verify_loop", "quick"}
    assert {mix_picker("m4", records)(rng).kind for _ in range(200)} >= {"quick", "verified", "sweep"}
    with pytest.raises(ValueError):
        mix_picker("m9", records)


def test_capture_write_error_never_breaks_a_turn(tmp_path, caplog):
    cap = Capture(tmp_path / "capture" / "app-calls.jsonl")
    cap.path.mkdir()  # a folder at the file path: open() fails, as with a wrong owner after a restore
    req = httpx.Request("POST", "http://edge.test/v1/chat/completions", json={"messages": []})
    cap.record(req)
    cap.record(req)
    assert cap.failed and cap.count == 0
    assert sum("capture stopped" in r.message for r in caplog.records) == 1


async def test_replayer_sends_scripts_with_new_sessions_and_records_results(tmp_path):
    records = load_capture([await capture_run(tmp_path)])
    edge = FakeEdge(both)
    cfg = ReplayConfig(target="http://edge.test/v1", mix="m4", rate=40.0, duration_s=0.5, out_dir=tmp_path / "run",
                       seed=3, drain_s=10)
    summary = await Replayer(cfg, mix_picker("m4", records), transport=edge.transport()).run()
    lines = [json.loads(x) for x in (tmp_path / "run" / "client.jsonl").read_text().splitlines()]
    assert lines and summary["calls"] == len(lines)
    assert all(x["status"] == 200 for x in lines)
    tenants = {c.headers["x-tenant-id"] for c in edge.calls}
    assert tenants <= {f"load-{i}" for i in range(1, 33)} | {"sweep"}
    assert all(c.headers["x-session-id"].startswith("replay-") for c in edge.calls)
    batch = [c for c in edge.calls if c.headers["x-request-class"] == "batch"]
    assert all(c.headers["x-tenant-id"] == "sweep" and c.headers["x-deadline-ms"] == "120000" for c in batch)
    streamed = [x for x in lines if x["stream"]]
    assert streamed and all(x["ttft_s"] is not None and x["output_tokens"] == 20 for x in streamed)
    assert json.loads((tmp_path / "run" / "summary.json").read_text())["config"]["mix"] == "m4"


async def test_replayer_aborts_streams_and_stops_a_script_on_a_reject(tmp_path):
    records = load_capture([await capture_run(tmp_path)])

    def reject_agent(call):
        return Reply(status=429, error_type="tenant_tokens") if call.step == "verify" else script(call)

    edge = FakeEdge(reject_agent)
    cfg = ReplayConfig(target="http://edge.test/v1", mix="m2", rate=40.0, duration_s=0.4, out_dir=tmp_path / "r2",
                       abort_p=1.0, seed=5, drain_s=10)
    summary = await Replayer(cfg, mix_picker("m2", records), transport=edge.transport()).run()
    lines = [json.loads(x) for x in (tmp_path / "r2" / "client.jsonl").read_text().splitlines()]
    assert any(x["aborted"] for x in lines)
    rejected = [x for x in lines if x["status"] == 429]
    assert rejected and all(x["reason"] == "tenant_tokens" and x["call_index"] == 0 for x in rejected)
    sheds = sum(c["sheds"].get("429 tenant_tokens", 0) for c in summary["by_class"].values())
    assert sheds == len(rejected)  # the verify loops of the sweep are batch calls


async def test_capacity_level_uses_unique_long_prompts(tmp_path):
    rng = random.Random(1)
    pool = ["alpha beta gamma " * 50, "delta epsilon " * 80]
    p1, p2 = make_prompt(pool, 2000, rng), make_prompt(pool, 2000, rng)
    assert p1[:40] != p2[:40] and 7000 < len(p1) < 8100
    edge = FakeEdge(lambda call: Reply(content="x y z"))
    report = await capacity_run("http://edge.test/v1", pool, [1, 2], tokens=1000, max_tokens=64, model="companion",
                                repeats=2, out=tmp_path / "e1", pause_s=0, transport=edge.transport())
    assert report["levels"]["2"]["calls"] == 4
    assert all(c.body["ignore_eos"] is True and c.headers["x-allow-overflow"] == "false" for c in edge.calls)
    assert {c.headers["x-tenant-id"] for c in edge.calls} == {"load-1", "load-2"}  # one tenant for each worker


def test_load_tenants_match_the_router_policy():
    import yaml

    from app.loadgen.replay import LOAD_TENANTS

    policy = yaml.safe_load((Path(__file__).resolve().parents[2] / "control/router/policy.yaml").read_text())
    assert [f"load-{i}" for i in range(1, policy["load_tenants"]["count"] + 1)] == LOAD_TENANTS


def test_build_sets_from_the_corpus():
    books = [{"title": f"Guide number {i} to vLLM", "text": "vLLM 0.2.1 in 2024 and 0.3.0"} for i in range(260)]
    sets = build_sets(books)
    assert len(sets["b1"]) == 200 and len(sets["b2"]) == 20 and len(sets["b3"]) == 50
    assert {q["mode"] for q in sets["b3"]} == {"verified"}


async def test_drive_labels_the_sessions(tmp_path):
    from app.api import create_app

    companion, metrics = make_companion(tmp_path, FakeEdge(script))
    transport = httpx.ASGITransport(app=create_app(companion, metrics))
    results = await drive("http://api", [{"set": "b1", "mode": "quick", "question": "Q?"}], transport=transport)
    assert results[0]["outcome"] == "ok" and results[0]["session_id"] == "b1-0000"


async def test_replayer_extra_tenant_stream_for_e10(tmp_path):
    records = load_capture([await capture_run(tmp_path)])
    edge = FakeEdge(both)
    cfg = ReplayConfig(target="http://edge.test/v1", mix="m4", rate=5.0, duration_s=0.5, out_dir=tmp_path / "e10",
                       seed=9, drain_s=10, extra=[("noisy", 40.0)])
    await Replayer(cfg, mix_picker("m4", records), transport=edge.transport()).run()
    tenants = [c.headers["x-tenant-id"] for c in edge.calls]
    assert tenants.count("noisy") > tenants.count("load-1")  # the noisy stream is much faster
    noisy = [c for c in edge.calls if c.headers["x-tenant-id"] == "noisy"]
    assert all(c.headers["x-request-class"] == "interactive" for c in noisy)
