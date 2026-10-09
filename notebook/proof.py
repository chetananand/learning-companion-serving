"""Load the saved run data and make the proof plots (06-experiments.md, rules 5 to 7).

A run folder `metrics/<run-id>/` holds:
  client.jsonl   one line for each call (app/loadgen/replay.py, app/loadgen/capacity.py)
  summary.json   the client summary
  run.json       the run record (tools/run_record.py)
  prom/*.json    the Prometheus range queries (tools/prom_dump.py)
The plots come from these files, not from screenshots. Each plot shows token counts next to the times
(H-117), and the runs use warm pods only (H-118).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
METRICS, PLOTS = ROOT / "metrics", ROOT / "plots"


@dataclass
class Series:
    labels: dict[str, str]
    t: list[float]
    v: list[float]

    def name(self, keys: tuple[str, ...] = ()) -> str:
        keys = keys or tuple(k for k in self.labels if k != "__name__")
        return " ".join(str(self.labels.get(k, "")) for k in keys) or "all"


@dataclass
class Run:
    id: str
    path: Path
    client: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def prom(self, name: str) -> list[Series]:
        f = self.path / "prom" / f"{name}.json"
        if not f.exists():
            return []
        data = json.loads(f.read_text())
        out = []
        for r in data.get("data", {}).get("result", []):
            pts = [(float(t), float(v)) for t, v in r.get("values", []) if v not in ("NaN", "+Inf", "-Inf")]
            out.append(Series(r.get("metric", {}), [p[0] for p in pts], [p[1] for p in pts]))
        return out

    def t0(self) -> float:
        starts = [c["t_start"] for c in self.client if "t_start" in c]
        return min(starts) if starts else 0.0

    def steady(self, skip_s: float = 60.0) -> list[dict[str, Any]]:
        """The calls after the first `skip_s` seconds. First the arrivals ramp up and the caches fill (H-118)."""
        t0 = self.summary.get("t_start") or self.t0()
        return [c for c in self.client if c.get("t_start", 0.0) >= t0 + skip_s]


def load_run(run_id: str, base: Path = METRICS) -> Run:
    path = base / run_id
    run = Run(run_id, path)
    if (path / "client.jsonl").exists():
        run.client = [json.loads(x) for x in (path / "client.jsonl").read_text().splitlines() if x.strip()]
    if (path / "summary.json").exists():
        run.summary = json.loads((path / "summary.json").read_text())
    if (path / "run.json").exists():
        run.record = json.loads((path / "run.json").read_text())
    return run


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]


def _save(fig: Any, name: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_series(ax: Any, series: list[Series], t0: float, keys: tuple[str, ...] = (), title: str = "",
                ylabel: str = "") -> None:
    for s in series:
        ax.plot([t - t0 for t in s.t], s.v, label=s.name(keys), linewidth=1)
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("seconds from the run start", fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    if series:
        ax.legend(fontsize=7)


def outcome_buckets(run: Run, width_s: float = 5.0) -> dict[str, dict[int, int]]:
    """Calls for each 5 s bucket by outcome: ok, aborted, or the shed reason (E2 soak)."""
    t0 = run.t0()
    out: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for c in run.client:
        b = int((c["t_start"] - t0) // width_s)
        key = "ok" if c["status"] == 200 and not c.get("aborted") else (
            "aborted" if c.get("aborted") else f"{c['status']} {c.get('reason', '')}".strip())
        out[key][b] += 1
    return out


# Part 5 and the experiments

BAND_NAMES = {"10": "interactive (priority 10)", "0": "batch (priority 0)"}  # control/router/policy.yaml


# The capacity sheds: the router flow-control reasons and 529 (the edge marks them "may leave").
# Session 1: guard blocks (400 prompt_injection), guard_unavailable (503), and 4xx rejects are not capacity.
CAPACITY_REASONS = {"kv_free", "timeout_queue", "no_endpoints", "p99_spread", "queue_pressure", "evicted",
                    "overloaded"}


def is_capacity_shed(call: dict[str, Any]) -> bool:
    reason = call.get("reason") or ""
    return reason in CAPACITY_REASONS or (reason.startswith("router_")
                                          and reason not in ("router_internal", "router_shutdown"))


def knee_rate(run: Run) -> dict[str, Any] | None:
    """E2: RATE100, the arrival rate (scripts each second) of the last full minute before the first shed.

    Only capacity sheds count (CAPACITY_REASONS). A tenant window (429 tenant_tokens), a client error (599),
    a guard result, and a 4xx reject do not. E3, E5, E10, and E15 use RATE100 as the 100% load.
    """
    cfg = run.summary.get("config") or {}
    if not run.client or "rate" not in cfg:
        return None
    t0 = run.summary.get("t_start") or run.t0()
    sheds = sorted((c["t_start"], f"{c['status']} {c.get('reason', '')}".strip()) for c in run.client
                   if c["status"] not in (200, 599) and is_capacity_shed(c))
    if not sheds:
        return {"rate100": None, "note": "no shed: run the soak again with a higher --rate or --ramp"}
    first_s, reason = sheds[0][0] - t0, sheds[0][1]
    minute = int(first_s // 60)
    if minute == 0:
        return {"rate100": None, "first_shed_s": round(first_s, 1), "reason": reason,
                "note": "shed in the first minute: run the soak again with a lower --rate"}
    rate = cfg["rate"] + cfg.get("ramp_per_min", 0.0) * (minute - 1)
    return {"rate100": round(rate, 3), "first_shed_s": round(first_s, 1), "shed_minute": minute, "reason": reason}



def queue_before_and_after_pick(run: Run, out: Path = PLOTS) -> Path | None:
    """H-65, H-67: our queue (router flow control, before the pick) and the vLLM waiting queue (after)."""
    before = run.prom("flow_queue_by_priority") or run.prom("flow_queue")
    after = run.prom("vllm_waiting")
    if not before and not after:
        return None
    fig, (a, b) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    t0 = run.t0() or min((s.t[0] for s in before + after if s.t), default=0)
    for s_ in before:  # the bands of control/router/policy.yaml
        s_.labels["priority"] = BAND_NAMES.get(s_.labels.get("priority", ""), s_.labels.get("priority", ""))
    plot_series(a, before, t0, ("priority",), "Our queue: router flow control, by band (before the pick)", "requests")
    plot_series(b, after, t0, ("pod",), "vLLM waiting queue, by pod (after the pick)", "requests")
    return _save(fig, f"{run.id}-queues.png", out)


def queue_depth_by_pod(run: Run, out: Path = PLOTS) -> Path | None:
    """H-67: our orch_replica_queue_depth. The router view of each pod queue, and the vLLM waiting queue."""
    router, engine = run.prom("router_pod_queue"), run.prom("vllm_waiting")
    if not router and not engine:
        return None
    fig, (a, b) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    t0 = run.t0() or min((s.t[0] for s in router + engine if s.t), default=0)
    plot_series(a, router, t0, ("model_server_endpoint",), "Queue depth for each pod, router view", "requests")
    plot_series(b, engine, t0, ("pod",), "vLLM waiting for each pod", "requests")
    return _save(fig, f"{run.id}-queue-depth-by-pod.png", out)


def engine_counts(run: Run, out: Path = PLOTS) -> Path | None:
    """H-66: waiting, running, and preempted counts (vLLM V1 has no swap)."""
    parts = [("vllm_running", "running"), ("vllm_waiting", "waiting"), ("vllm_preemptions_rate", "preemptions/s")]
    if not any(run.prom(n) for n, _ in parts):
        return None
    fig, axes = plt.subplots(3, 1, figsize=(8, 7), sharex=True)
    t0 = run.t0()
    for ax, (name, label) in zip(axes, parts, strict=True):
        plot_series(ax, run.prom(name), t0, ("pod",), label, label)
    return _save(fig, f"{run.id}-engine-counts.png", out)


def ttft_by_class(runs: list[Run], out: Path = PLOTS, name: str = "ttft-by-class.png",
                  skip_s: float = 60.0) -> Path | None:
    """TTFT p50 and p95 by class for each run, with the prompt tokens next to the time (H-117).

    It drops the first `skip_s` seconds of each run (Run.steady).
    """
    rows = []
    for run in runs:
        calls = run.steady(skip_s)
        for cls in sorted({c["request_class"] for c in calls}):
            ok = [c for c in calls if c["request_class"] == cls and c["status"] == 200 and c.get("ttft_s")]
            if ok:
                rows.append((f"{run.id}\n{cls}", percentile([c["ttft_s"] for c in ok], 0.5),
                             percentile([c["ttft_s"] for c in ok], 0.95),
                             percentile([c.get("prompt_tokens") or 0 for c in ok], 0.5)))
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(max(6, 1.4 * len(rows)), 4))
    x = range(len(rows))
    ax.bar([i - 0.2 for i in x], [r[1] for r in rows], 0.4, label="p50")
    ax.bar([i + 0.2 for i in x], [r[2] for r in rows], 0.4, label="p95")
    ax.set_xticks(list(x), [f"{r[0]}\n~{int(r[3])} prompt tok" for r in rows], fontsize=7)
    ax.set_ylabel("TTFT (s)")
    ax.legend()
    return _save(fig, name, out)


def queue_order(run_ids: list[str], base: Path = METRICS) -> list[dict[str, Any]]:
    """H-68, E14: a 27K-token retrieve and five short agent steps are ready at the same time.

    `tools/queue_order.py` writes queue-order.json: the TTFT median of each kind, and which kind got the first
    token in each round. The run id says the P/D split (on/off) and the class of the retrieve (int/batch).
    """
    rows = []
    for rid in run_ids:
        f = base / rid / "queue-order.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text())
        s = d["summary"]
        rows.append({"run": rid, "retrieve_class": d["long_class"],
                     "retrieve_ttft_median_s": s["retrieve_32k"]["ttft_median_s"],
                     "agent_step_ttft_median_s": s["agent_step"]["ttft_median_s"],
                     "agent_step_end_median_s": s["agent_step"]["end_median_s"],
                     "first_token_to_agent_step": f"{s['first_token_went_to']['agent_step']} of "
                                                  f"{sum(s['first_token_went_to'].values())} rounds"})
    return rows


def queue_order_plot(run_ids: list[str], out: Path = PLOTS) -> Path | None:
    rows = queue_order(run_ids)
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(8, 4))
    x = range(len(rows))
    ax.bar([i - 0.2 for i in x], [r["retrieve_ttft_median_s"] for r in rows], 0.4, label="27K-token retrieve")
    ax.bar([i + 0.2 for i in x], [r["agent_step_ttft_median_s"] for r in rows], 0.4, label="agent step")
    ax.set_xticks(list(x), [r["run"] for r in rows], fontsize=8)
    ax.set_ylabel("TTFT median (s)")
    ax.set_title("E14: who gets the first token when both are ready (5 rounds each)", fontsize=9)
    ax.legend()
    return _save(fig, "e14-queue-order.png", out)


def soak(run: Run, out: Path = PLOTS) -> Path | None:
    """E2: the system sheds before completions go down, and completions never fall to zero."""
    buckets = outcome_buckets(run)
    if not buckets:
        return None
    fig, ax = plt.subplots(figsize=(9, 4))
    last = max(max(b) for b in buckets.values())
    xs = list(range(last + 1))
    bottom = [0] * len(xs)
    for key in sorted(buckets, key=lambda k: (k != "ok", k)):
        ys = [buckets[key].get(x, 0) for x in xs]
        ax.bar([x * 5 for x in xs], ys, 5, bottom=bottom, label=key, align="edge")
        bottom = [a + b for a, b in zip(bottom, ys, strict=True)]
    ax.set_xlabel("seconds from the run start")
    ax.set_ylabel("calls in each 5 s")
    ax.legend(fontsize=7)
    return _save(fig, f"{run.id}-soak.png", out)


def capacity(runs: dict[int, Run], paper: dict[int, float], out: Path = PLOTS) -> Path | None:
    """E1: measured concurrency at the first preemption against the paper value, for each prompt length."""
    if not any(r.summary for r in runs.values()):
        return None
    fig, axes = plt.subplots(1, len(runs), figsize=(5 * len(runs), 4), squeeze=False)
    for ax, (tokens, run) in zip(axes[0], sorted(runs.items()), strict=True):
        levels = sorted((int(n), v) for n, v in run.summary.get("levels", {}).items())
        xs = [n for n, _ in levels]
        p95 = [v["by_class"].get("interactive", {}).get("ttft_p95_s") for _, v in levels]
        ax.plot(xs, p95, marker="o", label="TTFT p95 (s)")
        pre = run.prom("vllm_preemptions_rate")
        if pre:
            ax.axvline(first_preempt_level(run, levels), color="red", linestyle="--", label="first preemption")
        if tokens in paper:
            ax.axvline(paper[tokens], color="gray", linestyle=":", label="paper concurrency")
        ax.set_title(f"{tokens} prompt tokens, 512 output tokens", fontsize=9)
        ax.set_xlabel("concurrent requests")
        ax.legend(fontsize=7)
    return _save(fig, "capacity-paper-vs-measured.png", out)


def first_preempt_level(run: Run, levels: list[tuple[int, dict[str, Any]]]) -> float:
    """The first concurrency level whose time window has a preemption rate above zero."""
    pre = run.prom("vllm_preemptions_rate")
    for n, v in levels:
        start = v.get("t_start", 0)
        end = start + v.get("wall_s", 0)
        if any(start <= t <= end and val > 0 for s in pre for t, val in zip(s.t, s.v, strict=True)):
            return n
    return levels[-1][0] if levels else 0


def token_share(trace_path: Path) -> list[dict[str, Any]]:
    """H-95: shared-prefix tokens against all prompt tokens, for each step (from the app trace)."""
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in trace_path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("prompt_tokens"):
                by[rec["step"]].append(rec)
    rows = []
    for step, recs in sorted(by.items()):
        prompt = [r["prompt_tokens"] for r in recs]
        shared = [r["shared_prefix_tokens"] for r in recs]
        rows.append({"step": step, "calls": len(recs), "prompt_p50": percentile(prompt, 0.5),
                     "shared_prefix_p50": percentile(shared, 0.5),
                     "shared_share": round(sum(shared) / max(1, sum(prompt)), 3),
                     "output_p50": percentile([r.get("output_tokens") or 0 for r in recs], 0.5)})
    return rows


def engine_flags(run: Run) -> dict[str, list[str]]:
    """H-70: the vLLM args of each pod, from the run record."""
    out = {}
    for pod in run.record.get("pods", []):
        if (pod.get("labels") or {}).get("app") not in ("vllm-prefill", "vllm-decode"):
            continue  # the router pod also runs the vLLM image (its tokenizer sidecar)
        for c in pod.get("containers", []):
            if c.get("name") == "modelserver" and c.get("args"):
                out[pod["name"]] = c["args"]
    return out


def warmup_ttft(run: Run, out: Path = PLOTS) -> Path | None:
    """H-14, H-79: the TTFT of the 4K probe on a new pod, cold and then warm (the warm-controller)."""
    last: dict[tuple[str, str], float] = {}
    for s in run.prom("first_ttft_mean"):
        if s.v:
            last[(s.labels.get("pod", ""), s.labels.get("warm", ""))] = s.v[-1]
    if not last:
        return None
    pods = sorted({p for p, _ in last})
    fig, ax = plt.subplots(figsize=(max(5, 2 * len(pods)), 4))
    for i, pod in enumerate(pods):
        for j, (warm, color) in enumerate((("false", "tab:red"), ("true", "tab:green"))):
            if (pod, warm) in last:
                ax.bar(i + (j - 0.5) * 0.4, last[(pod, warm)], 0.4, color=color,
                       label=("cold" if warm == "false" else "warm") if i == 0 else None)
    ax.set_xticks(range(len(pods)), pods, fontsize=8)
    ax.set_ylabel("TTFT of a 4K-token probe (s)")
    ax.set_title("A new pod: TTFT before and after the warmup routine", fontsize=9)
    ax.legend()
    return _save(fig, "warmup-ttft.png", out)


def envoy_calls(run: Run) -> list[dict[str, Any]]:
    """The Envoy access log of a run (one JSON line for each call), without the readiness calls of variant.sh."""
    import datetime as dt

    f = run.path / "envoy-access.log"
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (d.get("session") or "").startswith("variant-ready") or not d.get("start_time"):
            continue
        d["t"] = dt.datetime.fromisoformat(d["start_time"].replace("Z", "+00:00")).timestamp()
        out.append(d)
    return sorted(out, key=lambda d: d["t"])


def new_pod_window(run: Run, first_s: float = 60.0) -> dict[str, Any] | None:
    """E8, H-73, H-79: the pod that comes back after the delete, from the Envoy log.

    The warm-controller gauges of 2026-09-29 lost the engine pod label (Prometheus renamed it to exported_pod,
    and the dump grouped by pod), so the access log is the evidence: the upstream pod, the first-byte time,
    and the prompt tokens of each call.
    """
    calls = envoy_calls(run)
    first: dict[str, float] = {}
    for d in calls:
        if d.get("upstream_host") and d["upstream_host"] not in first:
            first[d["upstream_host"]] = d["t"]
    if len(first) < 2:
        return None
    new = max(first, key=first.get)
    t_new = first[new]

    def stats(lo: float, hi: float) -> dict[str, Any]:
        sel = [d for d in calls if d.get("upstream_host") == new and d.get("status") == 200
               and d.get("first_byte_ms") is not None and lo <= d["t"] - t_new < hi]
        fb = [d["first_byte_ms"] / 1000 for d in sel]
        prompt = [d.get("input_tokens") or 0 for d in sel]
        cached = sum(d.get("cached_input_tokens") or 0 for d in sel)
        return {"calls": len(sel), "ttft_p50_s": percentile(fb, 0.5), "ttft_p95_s": percentile(fb, 0.95),
                "prompt_tokens_p50": percentile(prompt, 0.5), "cached_share": round(cached / max(1, sum(prompt)), 2)}

    buckets: dict[int, list[bool]] = defaultdict(list)
    for d in calls:
        if d.get("upstream_host") and -30 <= d["t"] - t_new < 180:
            buckets[int((d["t"] - t_new) // 10)].append(d["upstream_host"] == new)
    outage = [d for d in calls if d.get("dropped_reason") == "rejected-no-endpoints"]
    gap = [d["t"] for d in outage if d["t"] < t_new]
    return {"run": run.id, "new_pod": new, "back_at_s": round(t_new - calls[0]["t"]),
            "outage_s": round(t_new - min(gap)) if gap else None,  # first reject -> first call on the new pod
            "no_endpoint_calls": len(outage), "first_minute": stats(0, first_s), "minutes_2_to_4": stats(120, 240),
            "share_10s": {k * 10: round(sum(v) / len(v), 2) for k, v in sorted(buckets.items())}}


def new_pod_plot(runs: list[Run], out: Path = PLOTS, name: str = "e8-new-pod.png") -> Path | None:
    """E8: the share of the calls that go to the returned pod, and its TTFT p95, in 10 s steps."""
    data = [(r, new_pod_window(r)) for r in runs]
    data = [(r, w) for r, w in data if w]
    if not data:
        return None
    fig, (a, b) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for run, w in data:
        xs = sorted(w["share_10s"])
        a.plot(xs, [w["share_10s"][x] for x in xs], marker=".", label=run.id)
        calls = [d for d in envoy_calls(run) if d.get("upstream_host") == w["new_pod"] and d.get("first_byte_ms")]
        t_new = min(d["t"] for d in calls)
        by: dict[int, list[float]] = defaultdict(list)
        for d in calls:
            if d["t"] - t_new < 180:
                by[int((d["t"] - t_new) // 10) * 10].append(d["first_byte_ms"] / 1000)
        b.plot(sorted(by), [percentile(by[k], 0.95) for k in sorted(by)], marker=".", label=run.id)
    a.set_title("Share of the calls that go to the returned pod (10 s steps)", fontsize=9)
    a.set_ylabel("share")
    a.legend(fontsize=7)
    b.set_title("TTFT p95 of the calls on the returned pod (first byte at Envoy)", fontsize=9)
    b.set_xlabel("seconds after the first call to the returned pod")
    b.set_ylabel("s")
    b.legend(fontsize=7)
    return _save(fig, name, out)


def warmup_first_minute(runs: list[Run], out: Path = PLOTS) -> Path | None:
    """H-79: the TTFT on the returned pod in its first minute, with the warmup routine and without it."""
    rows = [(r.id, new_pod_window(r)) for r in runs]
    rows = [(rid, w) for rid, w in rows if w and w["first_minute"]["calls"]]
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(rows))
    ax.bar([i - 0.2 for i in x], [w["first_minute"]["ttft_p50_s"] for _, w in rows], 0.4, label="p50")
    ax.bar([i + 0.2 for i in x], [w["first_minute"]["ttft_p95_s"] for _, w in rows], 0.4, label="p95")
    ax.set_xticks(list(x), [f"{rid}\n{w['first_minute']['calls']} calls, ~{w['first_minute']['prompt_tokens_p50']}"
                            f" prompt tok" for rid, w in rows], fontsize=8)
    ax.set_ylabel("TTFT in the first 60 s on the returned pod (s)")
    ax.set_title("A returned decode pod: with the warmup routine, and with none", fontsize=9)
    ax.legend()
    return _save(fig, "warmup-ttft.png", out)


def kv_table(runs: list[Run]) -> list[dict[str, Any]]:
    """H-69, E6: KV use, the prefix-cache hit ratio, and the tokens from the LMCache tier, for each run."""
    def mean_max(run: Run, name: str, pod_part: str) -> tuple[float | None, float | None]:
        vals = [v for s in run.prom(name) if pod_part in s.labels.get("pod", "") for v in s.v]
        return (round(sum(vals) / len(vals), 3), round(max(vals), 3)) if vals else (None, None)

    rows = []
    for run in runs:
        kv_mean, kv_max = mean_max(run, "vllm_kv_usage", "decode")
        hit_mean, _ = mean_max(run, "vllm_prefix_hit_ratio", "decode")
        lm = [v for s in run.prom("lmcache_hit_tokens_rate") for v in s.v]
        inter = (run.summary.get("by_class") or {}).get("interactive", {})
        rows.append({"run": run.id, "decode_kv_use_mean": kv_mean, "decode_kv_use_max": kv_max,
                     "decode_prefix_hit_ratio_mean": hit_mean,
                     "lmcache_hit_tokens_per_s_mean": round(sum(lm) / len(lm)) if lm else None,
                     "interactive_ttft_p50_s": inter.get("ttft_p50_s"),
                     "interactive_ttft_p95_s": inter.get("ttft_p95_s")})
    return rows


def ttft_bars(rows: list[tuple[str, str]], name: str, title: str, out: Path = PLOTS,
              base: Path = METRICS) -> Path | None:
    """Interactive TTFT p50 and p95 for each (label, run id), with the share of the calls that were ok."""
    data = []
    for label, rid in rows:
        inter = (load_run(rid, base).summary.get("by_class") or {}).get("interactive")
        if inter:
            data.append((label, inter.get("ttft_p50_s") or 0, inter.get("ttft_p95_s") or 0,
                         inter["ok"] / max(1, inter["calls"])))
    if not data:
        return None
    fig, ax = plt.subplots(figsize=(max(6, 1.3 * len(data)), 4))
    x = range(len(data))
    ax.bar([i - 0.2 for i in x], [d[1] for d in data], 0.4, label="TTFT p50")
    ax.bar([i + 0.2 for i in x], [d[2] for d in data], 0.4, label="TTFT p95")
    ax.set_xticks(list(x), [f"{d[0]}\n{d[3]:.0%} ok" for d in data], fontsize=8)
    ax.set_ylabel("interactive TTFT (s)")
    ax.set_title(title, fontsize=9)
    ax.legend()
    return _save(fig, name, out)


def hop_plot(run_ids: dict[str, str], out: Path = PLOTS, base: Path = METRICS) -> Path | None:
    """E4: the TTFT of a split request for each hop medium, with and without a shared prefix (hop.json)."""
    rows = []
    for label, rid in run_ids.items():
        f = base / rid / "hop.json"
        if f.exists():
            for group, g in json.loads(f.read_text())["groups"].items():
                ok = [x["ttft_s"] for x in g["requests"] if x["status"] == 200]
                rows.append((f"{label}\n{group}", percentile(ok, 0.5)))
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(range(len(rows)), [r[1] for r in rows], color=["tab:green", "tab:green", "tab:red", "tab:red"][:len(rows)])
    ax.set_xticks(range(len(rows)), [r[0] for r in rows], fontsize=8)
    ax.set_ylabel("TTFT median of a split request (s)")
    ax.set_title("E4: the hop through the LMCache tier against NIXL (about 9K tokens)", fontsize=9)
    return _save(fig, "e4-hop.png", out)


def split_itl_plot(run_id: str, out: Path = PLOTS, base: Path = METRICS) -> Path | None:
    """E5: the ITL p95 of 16 other decode streams while one prompt splits or stays on the decode pod."""
    f = base / run_id / "split.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text())
    sizes = [n for n in d["sizes"] if f"{n}-split" in d["summary"]]
    fig, ax = plt.subplots(figsize=(7, 4))
    for mode, color in (("split", "tab:green"), ("local", "tab:red")):
        ax.plot(sizes, [d["summary"][f"{n}-{mode}"]["window_itl_p95_median_s"] * 1000 for n in sizes],
                marker="o", color=color, label=f"the prompt {'splits' if mode == 'split' else 'stays on decode'}")
    ax.axhline(50, color="gray", linestyle=":", label="SLO-2 (50 ms)")
    ax.set_xlabel("prompt tokens")
    ax.set_ylabel("ITL p95 of the other streams (ms)")
    ax.set_title(f"E5 ({run_id}): the split protects the other decode streams", fontsize=9)
    ax.legend(fontsize=8)
    return _save(fig, f"{run_id}-itl.png", out)


def four_resources(base: Path = METRICS) -> list[dict[str, Any]]:
    """H-23: the four scarce resources, each with its measured peak and the run."""
    def peak(rid: str, name: str, pod_part: str = "") -> float | None:
        vals = [v for s in load_run(rid, base).prom(name) if pod_part in s.labels.get("pod", "") for v in s.v]
        return round(max(vals), 3) if vals else None
    hop = json.loads((base / "e4-hop" / "hop.json").read_text()) if (base / "e4-hop" / "hop.json").exists() else {}
    unshared = [x for x in (hop.get("groups", {}).get("unshared", {}).get("requests", [])) if x["status"] == 200]
    no_warmup = (new_pod_window(load_run("e8-c-immediate", base)) or {}).get("outage_s")
    return [
        {"resource": "decode slots", "metric": "vllm:num_requests_running (decode pod), --max-num-seqs=24",
         "peak": peak("e3-c-150", "vllm_running", "decode"), "run": "e3-c-150"},
        {"resource": "KV blocks", "metric": "vllm:kv_cache_usage_perc (decode pod)",
         "peak": peak("e13-gate", "vllm_kv_usage", "decode"), "run": "e13-gate"},
        {"resource": "hop bandwidth", "metric": "LMCache tokens loaded each second; split TTFT median (unshared)",
         "peak": peak("e6-fp8kv", "lmcache_hit_tokens_rate"),
         "run": f"e6-fp8kv; e4-hop {percentile([x['ttft_s'] for x in unshared], 0.5)} s"},
        {"resource": "warmup time",
         "metric": "seconds with no decode pod: first 503 to the first call on the new pod",
         "peak": (new_pod_window(load_run("e8-c-warmup", base)) or {}).get("outage_s"),
         "run": f"e8-c-warmup (no warmup: {no_warmup} s)"},
    ]


def work_share(run: Run, metric: str = "vllm_prompt_tokens_rate") -> dict[str, float]:
    """E11: the share of the work of each pod (the sum of its prompt-token rate samples over the run)."""
    totals = {s.labels.get("pod", ""): sum(s.v) for s in run.prom(metric)}
    all_ = sum(totals.values())
    return {pod: round(v / all_, 2) for pod, v in totals.items()} if all_ else {}


def scale_steps(run_ids: list[str], base: Path = METRICS) -> tuple[dict[str, list[tuple[float, float]]],
                                                                   dict[str, list[tuple[float, float]]]]:
    """The planner request and the KEDA replicas of each pool, over runs in time order.

    The query windows of two runs can overlap. In the overlap, only the later run counts. Otherwise the
    samples of the two runs alternate, and a step plot draws a solid block.
    """
    runs = [load_run(rid, base) for rid in run_ids]
    starts = [min((s.t[0] for s in r.prom("planner_desired") + r.prom("deploy_replicas") if s.t), default=None)
              for r in runs]
    desired: dict[str, list[tuple[float, float]]] = defaultdict(list)
    actual: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for k, run in enumerate(runs):
        end = min((t for t in starts[k + 1:] if t is not None), default=float("inf"))
        for s in run.prom("planner_desired"):
            desired[s.labels.get("pool", "")] += [(t, v) for t, v in zip(s.t, s.v, strict=True) if t < end]
        for s in run.prom("deploy_replicas"):
            if s.labels.get("deployment") in ("vllm-decode", "vllm-prefill"):
                pool = s.labels["deployment"].removeprefix("vllm-")
                actual[pool] += [(t, v) for t, v in zip(s.t, s.v, strict=True) if t < end]
    return dict(desired), dict(actual)


def e9_scale_plot(run_ids: list[str], marks: dict[str, float] | None = None, out: Path = PLOTS,
                  base: Path = METRICS) -> Path | None:
    """E9: the replicas that the planner asks for, against the replicas that KEDA runs, for each pool.

    `marks` are vertical lines (label -> unix time), for example the change of a planner constant.
    """
    desired, actual = scale_steps(run_ids, base)
    if not desired and not actual:
        return None
    t0 = min(t for pts in [*desired.values(), *actual.values()] for t, _ in pts)
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for ax, pool in zip(axes, ("decode", "prefill"), strict=True):
        lines = ((desired.get(pool, []), "--", "planner asks"), (actual.get(pool, []), "-", "KEDA runs"))
        for pts, style, label in lines:
            pts = sorted(set(pts))
            if pts:
                ax.step([(t - t0) / 60 for t, _ in pts], [v for _, v in pts], style, where="post", label=label)
        for label, t in (marks or {}).items():
            ax.axvline((t - t0) / 60, color="gray", linestyle=":", label=label)
        ax.set_title(f"The {pool} pool", fontsize=9)
        ax.set_ylabel("pods")
        ax.legend(fontsize=7)
    axes[-1].set_xlabel("minutes from the start of E9")
    return _save(fig, "e9-scale.png", out)


def scale_events(run: Run, pool: str) -> dict[str, Any] | None:
    """E9: when the planner asked for a second pod of `pool`, when KEDA made it, and when the router could use it.

    The times come from the Prometheus range queries alone, because a stopped run has no summary. The planner
    and KEDA times are the first samples where the value rises to 2 or more. The new pod is the pod of the pool
    whose engine metrics start after KEDA made it. Its first prompt tokens are the warmup probes (engine_up).
    The router can send calls to it from the warm label, that is the first ramp weight above 0 (warm).
    """
    desired = [s for s in run.prom("planner_desired") if s.labels.get("pool") == pool]
    replicas = [s for s in run.prom("deploy_replicas") if s.labels.get("deployment") == f"vllm-{pool}"]
    if not desired or not replicas:
        return None

    def rise(s: Series) -> float | None:
        return next((t for a, b, t in zip(s.v, s.v[1:], s.t[1:], strict=False) if a < 2 <= b), None)

    asked, made = rise(desired[0]), rise(replicas[0])
    if asked is None or made is None:
        return None
    running = {s.labels.get("pod", ""): s for s in run.prom("vllm_running")}
    new = [p for p, s in running.items() if p.startswith(f"vllm-{pool}-") and s.t and s.t[0] > made]
    pod = min(new, key=lambda p: running[p].t[0]) if new else None

    def first_above(name: str) -> float | None:
        for s in run.prom(name):
            if s.labels.get("pod") == pod:
                return next((t for t, v in zip(s.t, s.v, strict=True) if v > 0), None)
        return None

    engine_up, warm = first_above("vllm_prompt_tokens_rate"), first_above("ramp_weight")
    weights = [max(s.v) for s in run.prom("ramp_weight") if s.labels.get("pod") == pod and s.v]
    return {"pool": pool, "asked": asked, "made": made, "pod": pod, "engine_up": engine_up, "warm": warm,
            "made_after_s": made - asked,
            "engine_up_after_s": engine_up - asked if engine_up is not None else None,
            "warm_after_s": warm - asked if warm is not None else None,
            "planner_max": max(desired[0].v), "replicas_max": max(replicas[0].v),
            "new_pod_running_max": max(running[pod].v) if pod and running[pod].v else None,
            "new_pod_ramp_weight_max": max(weights) if weights else None}


def ramp(run: Run, out: Path = PLOTS) -> Path | None:
    """H-73, E8: the ramp weight of a returned pod, and the TTFT p95 while it ramps."""
    weights, ttft = run.prom("ramp_weight"), run.prom("vllm_ttft_p95")
    if not weights:
        return None
    fig, (a, b) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    t0 = run.t0() or min((s.t[0] for s in weights if s.t), default=0)
    plot_series(a, weights, t0, ("pod",), "Ramp weight (10%, 25%, 50%, 100%)", "weight")
    plot_series(b, ttft, t0, ("pod",), "TTFT p95 for each pod", "s")
    return _save(fig, f"{run.id}-ramp.png", out)


if __name__ == "__main__":  # python -m notebook.proof knee <run-id> [--env]
    import sys

    if sys.argv[1:2] != ["knee"] or len(sys.argv) not in (3, 4):
        sys.exit("usage: python -m notebook.proof knee <run-id> [--env]")
    k = knee_rate(load_run(sys.argv[2]))
    if sys.argv[3:] == ["--env"]:  # the load levels of E3, E5, E8, E10, E12, E13, E15 as shell lines
        if not k or k.get("rate100") is None:
            sys.exit(f"no RATE100: {k}")
        for name, f in (("R50", 0.5), ("R70", 0.7), ("R100", 1.0), ("R150", 1.5)):
            print(f"{name}={round(f * k['rate100'], 3)}")
    else:
        print(json.dumps(k, indent=1))
