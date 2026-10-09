"""Tests of the proof library on a synthetic run folder."""

from __future__ import annotations

import json
from pathlib import Path

from notebook import proof


def write_run(base: Path, run_id: str) -> Path:
    path = base / run_id
    (path / "prom").mkdir(parents=True)
    t0 = 1_800_000_000.0
    calls = []
    for i in range(60):
        status, reason = (429, "timeout_queue") if i % 10 == 9 else (200, "")
        calls.append({"t_start": t0 + i, "status": status, "reason": reason, "aborted": i == 5,
                      "request_class": "batch" if i % 5 == 0 else "interactive", "ttft_s": 0.2 + i / 100,
                      "prompt_tokens": 8000, "output_tokens": 300})
    (path / "client.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))

    def prom(name: str, labels: list[dict[str, str]], value) -> None:
        result = [{"metric": lab, "values": [[t0 + s, str(value(s, k))] for s in range(0, 60, 5)]}
                  for k, lab in enumerate(labels)]
        (path / "prom" / f"{name}.json").write_text(json.dumps({"status": "success", "data": {"result": result}}))

    prom("flow_queue_by_priority", [{"priority": "10"}, {"priority": "0"}], lambda s, k: (s % 20) * (k + 1))
    prom("vllm_waiting", [{"pod": "vllm-decode-0"}], lambda s, k: s % 7)
    prom("router_pod_queue", [{"model_server_endpoint": "vllm-decode-0"}, {"model_server_endpoint": "vllm-prefill-0"}],
         lambda s, k: (s + k) % 5)
    prom("vllm_running", [{"pod": "vllm-decode-0"}], lambda s, k: 20)
    prom("vllm_preemptions_rate", [{"pod": "vllm-decode-0"}], lambda s, k: 0.5 if s > 40 else 0)
    prom("first_ttft_mean", [{"pod": "vllm-decode-1", "warm": "false"}, {"pod": "vllm-decode-1", "warm": "true"}],
         lambda s, k: 2.5 if k == 0 else 0.4)
    prom("ramp_weight", [{"pod": "vllm-decode-1"}], lambda s, k: min(1.0, 0.1 + s / 50))
    prom("vllm_ttft_p95", [{"pod": "vllm-decode-1"}], lambda s, k: 0.5)
    levels = {str(n): {"t_start": t0 + 10 * j, "wall_s": 10, "by_class": {"interactive": {"ttft_p95_s": 0.1 * n}}}
              for j, n in enumerate([1, 2, 4, 8, 16])}
    (path / "summary.json").write_text(json.dumps({"levels": levels}))
    (path / "run.json").write_text(json.dumps({"pods": [
        {"name": "vllm-decode-0", "labels": {"app": "vllm-decode"}, "containers": [
            {"name": "modelserver", "image": "vllm/vllm-openai:v0.30.0", "args": ["--max-num-seqs", "24"]}]},
        # The router pod also runs the vLLM image (its tokenizer sidecar): engine_flags must skip it.
        {"name": "companion-router-epp-0", "labels": {"app": "companion-router-epp"}, "containers": [
            {"name": "vllm-render", "image": "vllm/vllm-openai:v0.30.0", "args": ["--port=8000"]}]}]}))
    return path


def test_plots_from_a_saved_run(tmp_path):
    write_run(tmp_path, "r1")
    run = proof.load_run("r1", base=tmp_path)
    assert run.exists and len(run.client) == 60 and run.prom("vllm_waiting")[0].labels == {"pod": "vllm-decode-0"}
    out = tmp_path / "plots"
    for path in (proof.queue_before_and_after_pick(run, out), proof.engine_counts(run, out), proof.soak(run, out),
                 proof.ttft_by_class([run], out, skip_s=0), proof.capacity({8000: run}, {8000: 10.0}, out),
                 proof.warmup_ttft(run, out), proof.ramp(run, out), proof.queue_depth_by_pod(run, out)):
        assert path is not None and path.exists() and path.stat().st_size > 1000
    buckets = proof.outcome_buckets(run)
    assert sum(buckets["429 timeout_queue"].values()) == 6 and sum(buckets["aborted"].values()) == 1
    levels = sorted((int(n), v) for n, v in run.summary["levels"].items())
    assert proof.first_preempt_level(run, levels) == 16
    assert proof.engine_flags(run) == {"vllm-decode-0": ["--max-num-seqs", "24"]}
    assert len(run.steady(30)) == 30 and proof.ttft_by_class([run], out) is None  # all 60 calls in minute one


def test_missing_data_gives_none(tmp_path):
    run = proof.load_run("absent", base=tmp_path)
    assert not run.exists and proof.soak(run, tmp_path) is None and proof.engine_counts(run, tmp_path) is None


def test_token_share(tmp_path):
    trace = tmp_path / "trace.jsonl"
    recs = [{"step": "quick", "prompt_tokens": 10000, "shared_prefix_tokens": 2000, "output_tokens": 500},
            {"step": "quick", "prompt_tokens": 6000, "shared_prefix_tokens": 2000, "output_tokens": 20},
            {"step": "verify", "prompt_tokens": 3000, "shared_prefix_tokens": 1500, "output_tokens": 100}]
    trace.write_text("".join(json.dumps(r) + "\n" for r in recs))
    rows = {r["step"]: r for r in proof.token_share(trace)}
    assert rows["quick"]["calls"] == 2 and rows["quick"]["shared_share"] == 0.25
    assert rows["verify"]["shared_share"] == 0.5


def test_knee_rate_is_the_last_minute_before_the_first_capacity_shed():
    t0 = 1000.0
    client = [{"t_start": t0 + s, "status": 200} for s in range(0, 400, 7)]
    client += [{"t_start": t0 + 100, "status": 429, "reason": "tenant_tokens"},  # a tenant window: no
               {"t_start": t0 + 130, "status": 599, "reason": "ReadTimeout"},    # a client error: no
               {"t_start": t0 + 30, "status": 400, "reason": "prompt_injection"},  # a guard block: no
               {"t_start": t0 + 40, "status": 503, "reason": "guard_unavailable"},  # the guard: no
               {"t_start": t0 + 50, "status": 413, "reason": "upstream_rejected"},  # a 4xx reject: no
               {"t_start": t0 + 250, "status": 503, "reason": "timeout_queue"}]  # minute 4: the knee
    run = proof.Run("e2", Path("."), client=client,
                    summary={"t_start": t0, "config": {"rate": 0.1, "ramp_per_min": 0.1}})
    k = proof.knee_rate(run)
    assert k["rate100"] == 0.4 and k["shed_minute"] == 4 and k["reason"] == "503 timeout_queue"
    early = proof.Run("e2", Path("."), client=[{"t_start": t0 + 20, "status": 503, "reason": "kv_free"}],
                      summary={"t_start": t0, "config": {"rate": 2.0, "ramp_per_min": 0.1}})
    assert proof.knee_rate(early)["rate100"] is None
    calm = proof.Run("e2", Path("."), client=client[:5], summary={"t_start": t0, "config": {"rate": 0.1}})
    assert proof.knee_rate(calm)["rate100"] is None


def test_new_pod_window_from_the_envoy_log(tmp_path):
    import datetime as dt
    run_dir = tmp_path / "e8-x"
    run_dir.mkdir()
    t0 = dt.datetime(2026, 9, 30, 0, 0, tzinfo=dt.UTC)
    lines = []
    for k in range(120):  # pod A serves all; pod B comes back at 60 s and gets every second call
        t = t0 + dt.timedelta(seconds=k)
        host = "10.0.0.2:8000" if k >= 60 and k % 2 else "10.0.0.1:8000"
        fb = 9000 if host.startswith("10.0.0.2") and k < 70 else 500
        lines.append(json.dumps({"start_time": t.isoformat().replace("+00:00", "Z"), "upstream_host": host,
                                 "status": 200, "first_byte_ms": fb, "input_tokens": 2000,
                                 "cached_input_tokens": 1000, "session": "replay-1"}))
    lines.append(json.dumps({"start_time": t0.isoformat().replace("+00:00", "Z"), "session": "variant-ready-x",
                             "upstream_host": "10.0.0.9:8000", "status": 200}))  # skipped
    (run_dir / "envoy-access.log").write_text("\n".join(lines))
    w = proof.new_pod_window(proof.load_run("e8-x", tmp_path), first_s=30)
    assert w["new_pod"] == "10.0.0.2:8000" and w["back_at_s"] == 61
    assert w["first_minute"]["calls"] == 15 and w["first_minute"]["ttft_p95_s"] == 9.0
    assert w["first_minute"]["cached_share"] == 0.5 and w["share_10s"][0] == 0.5


def test_ttft_bars_and_split_plot(tmp_path):
    for rid, p50 in (("a", 1.0), ("b", 2.0)):
        d = tmp_path / rid
        d.mkdir()
        (d / "summary.json").write_text(json.dumps({"by_class": {"interactive": {
            "calls": 10, "ok": 9, "ttft_p50_s": p50, "ttft_p95_s": p50 * 3}}}))
    out = tmp_path / "plots"
    path = proof.ttft_bars([("A", "a"), ("B", "b"), ("missing", "zz")], "x.png", "t", out=out, base=tmp_path)
    assert path is not None and path.exists() and path.stat().st_size > 1000
    assert proof.ttft_bars([("missing", "zz")], "y.png", "t", out=out, base=tmp_path) is None
    s = tmp_path / "e5"
    s.mkdir()
    (s / "split.json").write_text(json.dumps({"sizes": [1000, 8000], "summary": {
        f"{n}-{m}": {"window_itl_p95_median_s": v} for n, m, v in
        ((1000, "split", 0.1), (1000, "local", 0.1), (8000, "split", 0.07), (8000, "local", 0.24))}}))
    assert proof.split_itl_plot("e5", out=out, base=tmp_path).exists()
    assert proof.split_itl_plot("nothing", out=out, base=tmp_path) is None


def test_scale_events_from_the_prometheus_data_only(tmp_path):
    """E9: no summary.json (a stopped run). The old pod has metrics from the start, the new pod after the change."""
    path = tmp_path / "e9-x" / "prom"
    path.mkdir(parents=True)
    t0 = 1_800_000_000.0

    def prom(name: str, series: list[tuple[dict[str, str], list[tuple[float, float]]]]) -> None:
        result = [{"metric": lab, "values": [[t0 + s, str(v)] for s, v in pts]} for lab, pts in series]
        (path / f"{name}.json").write_text(json.dumps({"status": "success", "data": {"result": result}}))

    steps = range(0, 400, 5)
    prom("planner_desired", [({"pool": "decode"}, [(s, 1 if s < 60 else 4) for s in steps]),
                             ({"pool": "prefill"}, [(s, 1) for s in steps])])
    prom("deploy_replicas", [({"deployment": "vllm-decode"}, [(s, 1 if s < 75 else 2) for s in steps])])
    prom("vllm_running", [({"pod": "vllm-decode-old"}, [(s, 24) for s in steps]),
                          ({"pod": "vllm-decode-new"}, [(s, 0 if s < 300 else 24) for s in steps if s >= 280])])
    prom("vllm_prompt_tokens_rate", [({"pod": "vllm-decode-new"}, [(s, 0 if s < 290 else 900) for s in steps
                                                                   if s >= 280])])
    prom("ramp_weight", [({"pod": "vllm-decode-new"}, [(s, 0 if s < 310 else 0.1) for s in steps if s >= 80])])
    ev = proof.scale_events(proof.load_run("e9-x", tmp_path), "decode")
    assert ev["pod"] == "vllm-decode-new"
    assert (ev["made_after_s"], ev["engine_up_after_s"], ev["warm_after_s"]) == (15, 230, 250)
    assert (ev["planner_max"], ev["replicas_max"], ev["new_pod_running_max"], ev["new_pod_ramp_weight_max"]) == (
        4, 2, 24, 0.1)
    assert proof.scale_events(proof.load_run("e9-x", tmp_path), "prefill") is None  # the planner never asked


def test_scale_steps_keep_only_the_later_run_in_an_overlap(tmp_path):
    t0 = 1_800_000_000.0
    for rid, start, value in (("r1", 0, 2), ("r2", 50, 1)):
        path = tmp_path / rid / "prom"
        path.mkdir(parents=True)
        values = [[t0 + s, str(value)] for s in range(start, start + 100, 5)]
        data = {"status": "success", "data": {"result": [{"metric": {"deployment": "vllm-decode"}, "values": values}]}}
        (path / "deploy_replicas.json").write_text(json.dumps(data))
    _, actual = proof.scale_steps(["r1", "r2"], base=tmp_path)
    pts = sorted(actual["decode"])
    assert [v for t, v in pts if t < t0 + 50] == [2] * 10 and [v for t, v in pts if t >= t0 + 50] == [1] * 20
