"""Write docs/results.md: the result tables of the experiments, computed from the saved runs in metrics/.

The report (DESIGN.md) and the slides quote these tables. Each number comes from a file in metrics/, so a reader
can check it. Run it again after a session: python3 tools/report_numbers.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from notebook import proof  # noqa: E402

OUT = ROOT / "docs" / "results.md"


def fmt(v: Any, digits: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def table(head: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(fmt(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def sheds(cls: dict[str, Any]) -> str:
    s = cls.get("sheds") or {}
    return ", ".join(f"{n} {k}" for k, n in sorted(s.items(), key=lambda kv: -kv[1])) or "none"


def replay_row(run_id: str, label: str) -> list[Any]:
    r = proof.load_run(run_id)
    i = (r.summary.get("by_class") or {}).get("interactive", {})
    b = (r.summary.get("by_class") or {}).get("batch", {})
    return [label, f"`{run_id}`", f"{i.get('ok', 0):,} of {i.get('calls', 0):,}", i.get("ttft_p50_s"),
            i.get("ttft_p95_s"), sheds(i), f"{b.get('ok', 0):,} of {b.get('calls', 0):,}" if b else "-"]


REPLAY_HEAD = ["Arm", "Run", "Interactive ok", "TTFT p50 (s)", "TTFT p95 (s)", "Interactive sheds", "Batch ok"]


def e1() -> str:
    out = []
    for run_id, tokens in (("e1-8k", 8000), ("e1-24k", 24000)):
        r = proof.load_run(run_id)
        rows = []
        for n, v in sorted((int(k), v) for k, v in r.summary.get("levels", {}).items()):
            c = v["by_class"]["interactive"]
            rows.append([n, f"{c['ok']} of {c['calls']}", c.get("ttft_p50_s"), c.get("ttft_p95_s"),
                         c.get("latency_p50_s")])
        out.append(f"`{run_id}`: {tokens:,} prompt tokens, 512 output tokens, straight to the gateway.\n\n"
                   + table(["Concurrent", "Ok", "TTFT p50 (s)", "TTFT p95 (s)", "Latency p50 (s)"], rows))
    return "\n\n".join(out)


def e2() -> str:
    r = proof.load_run("e2-soak")
    k = proof.knee_rate(r) or {}
    i, b = r.summary["by_class"]["interactive"], r.summary["by_class"]["batch"]
    return table(["Run", "RATE100 (scripts/s)", "First capacity shed", "Interactive ok", "Interactive sheds",
                  "Batch ok", "Batch sheds"],
                 [["`e2-soak`", k.get("rate100"), f"minute {k.get('shed_minute')}: {k.get('reason')}",
                   f"{i['ok']:,} of {i['calls']:,}", sheds(i), f"{b['ok']:,} of {b['calls']:,}", sheds(b)]])


def e4() -> str:
    rows = []
    for run_id, label in (("e4-hop", "LMCache server + store barrier"), ("e4-hop-nixl", "NIXL (UCX over TCP)")):
        d = json.loads((ROOT / "metrics" / run_id / "hop.json").read_text())
        for group, g in d["groups"].items():
            ok = [x for x in g["requests"] if x["status"] == 200]
            rows.append([label, f"`{run_id}`", group, len(ok), proof.percentile([x["ttft_s"] for x in ok], 0.5),
                         proof.percentile([x["cached_tokens"] for x in ok], 0.5),
                         proof.percentile([x["prompt_tokens"] for x in ok], 0.5)])
    hs = json.loads((ROOT / "metrics" / "e4-m2" / "hops-summary.json").read_text())
    return (table(["Hop", "Run", "Prefix", "Ok", "TTFT median (s)", "Cached tokens (median)",
                   "Prompt tokens (median)"], rows)
            + f"\n\nOn the M2 mix (`e4-m2`, 50% load), {hs['split']} of {hs['requests']} calls split "
              f"({hs['split_ratio']:.1%}). The other {hs['same_pod']} stayed on the decode pod.")


def e5() -> str:
    rows = []
    for run_id in ("e5-d2560", "e5-d4096"):
        d = json.loads((ROOT / "metrics" / run_id / "split.json").read_text())
        for size in d["sizes"]:
            s, loc = d["summary"].get(f"{size}-split"), d["summary"].get(f"{size}-local")
            if s and loc:
                rows.append([f"`{run_id}`", size, s["ttft_median_s"], loc["ttft_median_s"],
                             s["window_itl_p95_median_s"], loc["window_itl_p95_median_s"]])
    return ("16 decode streams run. One extra prompt either splits (prefill pod, then the hop) or stays on the "
            "decode pod. The ITL is the p95 of the other streams while the prompt runs.\n\n"
            + table(["Run (decode chunk)", "Prompt tokens", "TTFT split (s)", "TTFT local (s)",
                     "ITL p95 split (s)", "ITL p95 local (s)"], rows))


def e6() -> str:
    rows = [[f"`{r['run']}`", r["decode_kv_use_mean"], r["decode_kv_use_max"], r["decode_prefix_hit_ratio_mean"],
             r["lmcache_hit_tokens_per_s_mean"], r["interactive_ttft_p50_s"], r["interactive_ttft_p95_s"]]
            for r in proof.kv_table([proof.load_run(x) for x in ("e6-on", "e6-noprefix", "e6-fp8kv")])]
    return table(["Run", "KV use mean", "KV use max", "Prefix hit ratio", "LMCache hit tokens/s",
                  "TTFT p50 (s)", "TTFT p95 (s)"], rows)


def e7() -> str:
    rows = []
    for run_id in ("e7-precise", "e7-approx"):
        d = json.loads((ROOT / "metrics" / run_id / "ghosts-by-threshold.json").read_text())
        for phase in ("before", "after_60s", "after"):
            p = d[phase]
            rows.append([f"`{run_id}`", phase, p["requests"], p["split"], p["not_split_ghosts"], p["token_hit_ratio"]])
    return table(["Run", "Phase", "Requests", "Split", "Not split, 2,048+ uncached", "Token hit ratio"], rows)


def e8() -> str:
    rows = []
    for run_id, label in (("e8-c-warmup", "layout C, warmup"), ("e8-c-immediate", "layout C, no warmup"),
                          ("e8-a-ramp", "layout A, warmup + ramp"), ("e8-a-jump", "layout A, warmup + jump")):
        run = proof.load_run(run_id)
        w = proof.new_pod_window(run)
        f, later = w["first_minute"], w["minutes_2_to_4"]
        no_ep = sum((c.get("sheds") or {}).get("503 no_endpoints", 0) for c in run.summary["by_class"].values())
        rows.append([label, f"`{run_id}`", no_ep, f["calls"], f["ttft_p50_s"], f["ttft_p95_s"],
                     later["ttft_p50_s"], later["ttft_p95_s"]])
    return table(["Arm", "Run", "503 no_endpoints (client)", "Calls, first minute", "First minute p50 (s)",
                  "First minute p95 (s)", "Minutes 2 to 4 p50 (s)", "Minutes 2 to 4 p95 (s)"], rows)


def e11() -> str:
    rows = []
    for mode in ("pass", "frozen", "stall"):
        run = proof.load_run(f"e11-{mode}")
        share = next((v for pod, v in proof.work_share(run).items() if pod.startswith("vllm-decode")), None)
        i = run.summary["by_class"]["interactive"]
        rows.append([mode, f"`e11-{mode}`", share, f"{i['ok']} of {i['calls']}", i["ttft_p50_s"], i["ttft_p95_s"]])
    return ("Pod B (`vllm-decode`) has the stale-metrics proxy. Its share is its part of all prompt tokens.\n\n"
            + table(["Mode", "Run", "Pod B share of the prompt tokens", "Interactive ok", "TTFT p50 (s)",
                     "TTFT p95 (s)"], rows))


def exists(run_id: str) -> bool:
    return (ROOT / "metrics" / run_id / "summary.json").exists()


def e3_a100() -> str:
    """2026-10-01 on 8 x A100 80 GB (no H100 had stock): 32 decode sequences, load levels for this GPU."""
    rows = []
    for label, run_id in (("C, 0.15", "e3-c32-r015"), ("C, 0.30", "e3-c32-r030"), ("C, 0.45", "e3-c32-50"),
                          ("A, 0.15", "e3-a32-r015"), ("A, 0.30", "e3-a32-r030"), ("A, 0.45", "e3-a32-r045")):
        if exists(run_id):
            rows.append(replay_row(run_id, label))
    if not rows:
        return "No run yet."
    return ("Both layouts have 32 decode sequences. The levels are scripts each second. On the A100, the levels of "
            "the H100 runs were too high: 0.45 gave a TTFT p50 of 16 s.\n\n" + table(REPLAY_HEAD, rows))


def e7_again() -> str:
    rows = []
    resets = {"e7b-precise": "not recorded (the hit ratio fell)", "e7b-approx2": "success",
              "e7b-approx-reset-failed": "not recorded (no fall: most likely failed)"}
    for run_id in ("e7b-precise", "e7b-approx2", "e7b-approx-reset-failed"):
        f, g = ROOT / "metrics" / run_id / "ghost-probe.json", ROOT / "metrics" / run_id / "ghosts.json"
        if not f.exists():
            continue
        summ = json.loads(f.read_text())["summary"]
        gh = json.loads(g.read_text()) if g.exists() else {}
        rows.append([f"`{run_id}`", resets[run_id], summ.get("sessions"), summ.get("sessions_spanning_the_clear"),
                     (summ.get("before_clear") or {}).get("token_hit_ratio"),
                     (summ.get("first_60s_after") or {}).get("token_hit_ratio"),
                     gh.get("eligible"), gh.get("ghosts"), gh.get("ghost_ratio")])
    if not rows:
        return "No run yet."
    return ("Layout A with no KV connector (no LMCache). 24 sessions send a turn each 12 s for 300 s. At 150 s, "
            "the run clears the prefix cache of pod B.\n\n"
            + table(["Run", "Reset of the prefix cache", "Sessions", "Spanning the clear", "Hit ratio before",
                     "Hit ratio, 60 s after", "Eligible calls", "Ghosts", "Ghost ratio"], rows)
            + "\n\nvLLM v0.30.0 answers POST /reset_prefix_cache with HTTP 200 and `{\"success\": false}` when blocks "
              "are in "
              "use (fault 36). The run `e7b-approx2` asks with `reset_running_requests=true` and records the answer.")


E9_RUNS = (("e9-decode", "decode", "48 streams: 1,000 prompt tokens, 4,000 output tokens"),
           ("e9-prefill", "prefill", "16 streams: 24,000 prompt tokens, 64 output tokens"))


def clock(t: float) -> str:
    """A unix time as UTC and as the time on the laptop of the owner (PDT in October)."""
    u = dt.datetime.fromtimestamp(t, dt.UTC)
    return f"{u:%H:%M:%S} UTC ({u - dt.timedelta(hours=7):%H:%M:%S} PDT)"


def failed_calls(calls: list[dict[str, Any]]) -> str:
    """The calls that did not get 200, by status, with their median latency (the timeout that stopped them)."""
    out = []
    for status in sorted({c.get("status") for c in calls} - {200}, key=str):
        lat = sorted(c["latency_s"] for c in calls if c.get("status") == status and c.get("latency_s") is not None)
        out.append(f"{sum(1 for c in calls if c.get('status') == status)} x {status} at {lat[len(lat) // 2]:.0f} s"
                   if lat else f"{status}")
    return ", ".join(out) or "none"


def whole(v: float | None) -> int | None:
    return None if v is None else round(v)


def after(ev: dict[str, Any], key: str) -> str:
    return f"+{ev[key]:.0f} s" if ev.get(key) is not None else "-"


def e9() -> str:
    """2026-10-01, one node (8 x A100 80 GB). The times come from the Prometheus data of each run (no summary
    for e9-prefill: we stopped it before the capture)."""
    scale, load = [], []
    for run_id, pool, what in E9_RUNS:
        r = proof.load_run(run_id)
        ev = proof.scale_events(r, pool)
        if ev is None:
            continue
        scale.append([f"`{run_id}`", pool, clock(ev["asked"]), after(ev, "made_after_s"),
                      after(ev, "engine_up_after_s"), after(ev, "warm_after_s"), whole(ev["planner_max"]),
                      whole(ev["replicas_max"]), whole(ev["new_pod_running_max"]), ev["new_pod_ramp_weight_max"]])
        ttft = sorted(c["ttft_s"] for c in r.client if c.get("status") == 200 and c.get("ttft_s") is not None)
        ok = sum(1 for c in r.client if c.get("status") == 200)
        load.append([f"`{run_id}`", what, f"{ok} of {len(r.client)}", proof.percentile(ttft, 0.5),
                     proof.percentile(ttft, 0.95), failed_calls(r.client)])
    if not scale:
        return "No run yet."
    return ("At each time, only one pool was free to grow: `cluster/one.sh hold` kept the other pool at one pod. "
            "KEDA permitted 2 pods for each pool (overlay `one-e9`). The times after the planner request come "
            "from the Prometheus queries (step 5 s).\n\n"
            + table(["Run", "Pool", "Planner asks for a second pod", "KEDA makes it", "Warmup starts",
                     "Warm label: the router can use it", "Planner asks (max)", "Pods (max)",
                     "New pod: running (max)", "New pod: ramp weight (max)"], scale)
            + "\n\nThe load (`app/loadgen/capacity.py`, straight to the gateway):\n\n"
            + table(["Run", "Load", "Calls ok", "TTFT p50 (s)", "TTFT p95 (s)", "Failed calls"], load)
            + "\n\n- The planner asked for up to 4 pods. KEDA stopped at 2, the limit of the overlay."
              "\n- In `e9-prefill`, the planner rule first used the capacity of one H100 prefill pod: 10,500 "
              "tokens each second (E1). With this value, the planner asked for one prefill pod for about 16 "
              "minutes. At 04:42:27 UTC (21:42:27 PDT), we changed the value to 1,800. Under this load, the A100 "
              "prefill pod computed a median of 1,904 prompt tokens each second. 85 s after the change, the "
              "planner asked for 2 pods."
              "\n- The ramp of each new pod stayed at r10. The warm controller moves a ramp up only while the "
              "fleet TTFT p99 is at most 2 x the baseline (2.0 s on the A100). This load was far above it. At "
              "r10, the new decode pod still ran 24 sequences: the ramp is a score, not a cap (E8)."
              "\n- In `e9-decode`, the route timeout (300 s) stopped 24 calls. 3 calls got 504, and 21 calls lost "
              "the stream (599 is the code of the load generator for a client error). In `e9-prefill`, the router "
              "queue evicted 14 calls after 120 s (429).")


# The demo-check reports of 2026-10-01 (PDT). Three are debug runs of D-07 during the E3 load.
DEMO_RUNS = {
    "demo-check-20261002T020050Z": ("all", "dev", "idle", "The full check. SIE loaded its models during the run."),
    "demo-check-20261002T020134Z": ("D-07, D-08", "dev", "idle", "The two questions with no answer, again."),
    "demo-check-20261002T022021Z": ("D-07", "fix1", "`e3-c32-50`", "Debug run. The image had the old code: "
                                    "an error in the copy step."),
    "demo-check-20261002T022451Z": ("D-07", "fix2", "`e3-c32-50`", "Debug run. The image had the old code "
                                    "again, and the router shed the call (503 timeout_queue)."),
    "demo-check-20261002T023006Z": ("D-07", "fix3", "`e3-c32-100`", "Debug run. The image has the fix, but the "
                                    "router shed the call (503 timeout_queue)."),
    "demo-check-20261002T050815Z": ("D-03, D-07, D-08, D-09", "fix3", "idle", "The four failed questions, "
                                    "after E9."),
}


def demo_a100() -> str:
    files = sorted((ROOT / "metrics" / "demo").glob("demo-check-20261002T*.json"))
    if not files:
        return "No run yet."
    runs = []
    for f in files:
        qs, image, load, why = DEMO_RUNS.get(f.stem, ("?", "?", "?", "-"))
        runs.append([f"`{f.stem}`", clock(dt_from_stem(f.stem)), qs, f"`companion/app:{image}`", load, why])
    rows = []
    for f in files:
        d = json.loads(f.read_text())
        items = d if isinstance(d, list) else d.get("results", [])
        for it in items:
            errors = ", ".join(f"{e.get('code')} {e.get('reason')}" for e in it.get("errors") or []) or "-"
            claims = ", ".join(f"{c[0]} {c[1]} ({c[2]})" for c in it.get("claims") or []) or "-"
            rows.append([f"`{f.stem}`", it["id"], "pass" if it["pass"] else "fail", it["rule_ok"], it["time_ok"],
                         it.get("first_token_s"), it.get("total_s"), errors, claims])
    return ("The reports (`metrics/demo/`). The image `fix3` has the fix for the empty answer (`app/agent/"
            "middleware.py`, `app/service.py`).\n\n"
            + table(["Report", "End", "Questions", "App image", "Load", "Note"], runs)
            + "\n\n" + table(["Report", "Question", "Result", "Rule ok", "Time ok", "First token (s)", "Total (s)",
                               "Errors", "Claims (status, reason)"], rows))


def dt_from_stem(stem: str) -> float:
    return dt.datetime.strptime(stem.removeprefix("demo-check-"), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC).timestamp()


def e14() -> str:
    rows = [[f"`{r['run']}`", r["retrieve_class"], r["retrieve_ttft_median_s"], r["agent_step_ttft_median_s"],
             r["agent_step_end_median_s"], r["first_token_to_agent_step"]]
            for r in proof.queue_order(["e14-on-int", "e14-off-int", "e14-on-batch", "e14-off-batch"])]
    return table(["Run (split on/off, retrieve class)", "Retrieve class", "Retrieve TTFT (s)", "Agent step TTFT (s)",
                  "Agent step end (s)", "First token to an agent step"], rows)


def e17() -> str:
    lat = json.loads((ROOT / "metrics" / "e17" / "latency.json").read_text())
    pages = json.loads((ROOT / "metrics" / "e17" / "pages.json").read_text())
    rows = [[f"{x['threshold']}", x["false_positive_rate"], x["miss_rate"]] for x in pages["rates"]]
    return (table(["Turns", "Attacks blocked", "Benign blocked", "Check p50 cold (s)", "Check p95 cold (s)",
                   "Cached verdict p50 (ms)"],
                  [[lat["turns"], f"{lat['attacks_blocked']} of {lat['attacks']}",
                    f"{lat['benign_blocked']} of {lat['benign']}", lat["cold"]["p50_s"], lat["cold"]["p95_s"],
                    round(lat["warm"]["p50_s"] * 1000, 1)]])
            + f"\n\nPage check (Prompt Guard 2 on {pages['pages']} real pages, and on the same pages with an "
              "injected window):\n\n"
            + table(["Threshold", "False positive rate", "Miss rate"], rows))


def build() -> str:
    sections = [
        ("E1 Capacity at fixed prompt lengths", e1()),
        ("E2 Soak: the knee (RATE100)", e2()),
        ("E3 Topology: layout C (P/D) against layout A (two whole pods), M4", table(REPLAY_HEAD, [
            replay_row(f"e3-{arm}-{load}", f"{'C' if arm == 'c' else 'A'}, {load}%")
            for load in (50, 100, 150) for arm in ("c", "a")])),
        ("E4 The hop: LMCache server against NIXL", e4()),
        ("E5 The split decision", e5()),
        ("E6 Prefix cache and KV format (M2, 100%)", e6()),
        ("E7 Ghost prefixes after a cache clear (M2, 50%)", e7()),
        ("E8 A pod comes back: warmup and ramp (M4, 70%)", e8()),
        ("E10 Tenant isolation and the order in the band (M4, 100%)", table(REPLAY_HEAD, [
            replay_row("e10-fcfs", "FCFS"), replay_row("e10-edf", "EDF")])),
        ("E11 Stale metrics (M3, layout A, 100%)", e11()),
        ("E12 Client aborts (M4, 100%, 20% of the streams closed)", table(REPLAY_HEAD, [
            replay_row("e12-abort", "abort")])),
        ("E13 The overflow gate (M4, 150%)", table(REPLAY_HEAD, [replay_row("e13-gate", "gate")])),
        ("E14 Who goes first: a 27K retrieve against short agent steps", e14()),
        ("E15 Engine flags (M4, 100%, layout C)", table(REPLAY_HEAD, [
            replay_row("e3-c-100", "base: decode seqs 24, prefill chunk 16384"),
            replay_row("e15-seqs-16", "decode seqs 16"), replay_row("e15-seqs-32", "decode seqs 32"),
            replay_row("e15-mnbt-8k", "prefill chunk 8192")])
         + "\n\nThe E15 runs had fewer calls than the base run. The first 1.4 s of each run had the 503 "
           "`no_endpoints` calls of fault 30. The TTFT values are still comparable."),
        ("E16 The LMCache server: K2 (on) against K0 (none), M2", table(REPLAY_HEAD, [
            replay_row(r, r.split("-", 1)[1]) for r in ("e16-k2", "e16-k2-restart", "e16-k0", "e16-k0-restart")])),
        ("E17 The guard", e17()),
        ("E3 again on 8 x A100 80 GB (2026-10-01)", e3_a100()),
        ("E7 again: sessions that span the clear, no LMCache (2026-10-01)", e7_again()),
        ("E9 Scale: which pool (2026-10-01, one node)", e9()),
        ("The demo check on 8 x A100 80 GB (2026-10-01)", demo_a100()),
    ]
    head = ("# Results\n\nThis file comes from `tools/report_numbers.py`, which reads the saved runs in `metrics/`. "
            "Do not change it by hand. Run the script again after a session.\n\n"
            "The load levels come from E2: 100% load is RATE100, 0.9 scripts each second. TTFT is the time to the "
            "first token at the client. The tables use all calls of a run, with no warmup cut.\n")
    return head + "".join(f"\n## {title}\n\n{body}\n" for title, body in sections)


def main() -> int:
    OUT.write_text(build())
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
