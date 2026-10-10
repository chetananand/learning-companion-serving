"""Slide versions of the main plots (docs/spec/10-talk.md, step 3): large text, a value on each bar, and the
two colors of the deck. The numbers come from the same saved runs and the same functions as the notebook
(notebook/proof.py), so a slide never shows a number that docs/results.md does not have.

Usage: python3 tools/slide_plots.py   (writes plots/slides/*.png and prints each number that it draws)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from notebook import proof  # noqa: E402

plt = proof.plt
OUT = ROOT / "plots" / "slides"
BLUE, ORANGE, INK, GRID, PAPER = "#2563c9", "#e07b2e", "#1f2329", "#d9dde3", "#fbfbf8"


def _style() -> None:
    plt.rcParams.update({"font.size": 18, "axes.titlesize": 20, "axes.labelsize": 18, "xtick.labelsize": 16,
                         "ytick.labelsize": 16, "legend.fontsize": 16, "axes.edgecolor": GRID, "axes.labelcolor": INK,
                         "xtick.color": INK, "ytick.color": INK, "text.color": INK, "axes.spines.top": False,
                         "axes.spines.right": False, "figure.facecolor": PAPER, "axes.facecolor": PAPER})


def _bars(ax: Any, xs: list[float], values: list[float], color: str, label: str | None = None,
          width: float = 0.38, fmt: str = "{:.2f} s") -> None:
    bars = ax.bar(xs, values, width, color=color, label=label)
    for b, v in zip(bars, values, strict=True):
        ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, b.get_height()), ha="center", va="bottom",
                    xytext=(0, 4), textcoords="offset points", fontsize=16, fontweight="bold")


def _save(fig: Any, name: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _p50(run_id: str, base: Path) -> float | None:
    inter = (proof.load_run(run_id, base).summary.get("by_class") or {}).get("interactive") or {}
    return inter.get("ttft_p50_s")


def e3(out: Path = OUT, base: Path = proof.METRICS) -> tuple[Path, dict[str, Any]]:
    """E3 on both GPUs: the interactive TTFT p50 of P/D (layout C) against two colocated replicas (layout A)."""
    h100 = [(f"{p}%", f"e3-c-{p}", f"e3-a-{p}") for p in (50, 100, 150)]
    # the A100 loads: 0.15, 0.30, and 0.45 turns each second, shown as turns each minute
    a100 = [("9", "e3-c32-r015", "e3-a32-r015"), ("18", "e3-c32-r030", "e3-a32-r030"),
            ("27", "e3-c32-50", "e3-a32-r045")]
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=False)
    numbers: dict[str, Any] = {}
    panels = (("2 x H100, 24 decode sequences", h100, "load (100% = 54 app turns each minute)"),
              ("8 x A100, 32 decode sequences", a100, "load (app turns each minute)"))
    for ax, (title, rows, xlabel) in zip(axes, panels, strict=True):
        c = [_p50(rc, base) or 0 for _, rc, _ in rows]
        a = [_p50(ra, base) or 0 for _, _, ra in rows]
        x = list(range(len(rows)))
        _bars(ax, [i - 0.2 for i in x], c, ORANGE, "P/D: 1 prefill + 1 decode pod", fmt="{:.1f} s")
        _bars(ax, [i + 0.2 for i in x], a, BLUE, "two colocated replicas", fmt="{:.1f} s")
        ax.set_xticks(x, [r[0] for r in rows])
        ax.set_xlabel(xlabel)
        ax.set_title(title)
        ax.set_ylabel("interactive TTFT p50 (s)")
        ax.grid(axis="y", color=GRID)
        numbers[title] = {r[0]: {"P/D": cv, "colocated": av} for r, cv, av in zip(rows, c, a, strict=True)}
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 1.08))
    return _save(fig, "e3.png", out), numbers


def hop(out: Path = OUT, base: Path = proof.METRICS) -> tuple[Path, dict[str, Any]]:
    """E4: the TTFT median of a split request (about 9K tokens) through the LMCache server and through NIXL."""
    rows = []
    for label, rid, color in (("LMCache server", "e4-hop", BLUE), ("NIXL over TCP", "e4-hop-nixl", ORANGE)):
        f = base / rid / "hop.json"
        for group, g in json.loads(f.read_text())["groups"].items():
            ok = [x["ttft_s"] for x in g["requests"] if x["status"] == 200]
            rows.append((f"{label}\n{group} prefix", proof.percentile(ok, 0.5), color))
    fig, ax = plt.subplots(figsize=(11, 6))
    for i, (_, v, color) in enumerate(rows):
        _bars(ax, [i], [v], color, width=0.6)
    ax.set_xticks(range(len(rows)), [r[0] for r in rows])
    ax.set_ylabel("TTFT median of a split request (s)")
    ax.grid(axis="y", color=GRID)
    return _save(fig, "hop.png", out), {r[0].replace("\n", " "): r[1] for r in rows}


def warm(out: Path = OUT, base: Path = proof.METRICS) -> tuple[Path, dict[str, Any]]:
    """E8: the first-minute TTFT p95 on the returned pod. Left: the warmup routine (layout C). Right: ramp or jump
    (layout A)."""
    pairs = ((("with the warmup", "e8-c-warmup"), ("no warmup", "e8-c-immediate")),
             (("ramp r10 to r100", "e8-a-ramp"), ("jump to r100", "e8-a-jump")))
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    numbers: dict[str, Any] = {}
    for ax, title, pair in zip(axes, ("The warmup (the one decode pod comes back)",
                                      "The ramp (a deleted pod comes back)"), pairs, strict=True):
        vals = []
        for label, rid in pair:
            w = proof.new_pod_window(proof.load_run(rid, base))
            vals.append((label, w["first_minute"]["ttft_p95_s"], w["first_minute"]["calls"]))
        for i, (_, v, _) in enumerate(vals):
            _bars(ax, [i], [v], BLUE if i == 0 else ORANGE, width=0.55, fmt="{:.1f} s")
        ax.set_xticks(range(len(vals)), [f"{lab}\n{n} calls" for lab, _, n in vals])
        ax.set_title(title)
        ax.set_ylabel("TTFT p95 in the first minute (s)")
        ax.grid(axis="y", color=GRID)
        numbers[title] = {lab: v for lab, v, _ in vals}
    return _save(fig, "warm.png", out), numbers


def e9(out: Path = OUT, base: Path = proof.METRICS) -> tuple[Path, dict[str, Any]]:
    """E9: the pods that the planner asks for against the pods that KEDA runs, with the events of each pool."""
    runs = ["e9-decode", "e9-prefill"]
    desired, actual = proof.scale_steps(runs, base)
    t0 = min(t for pts in [*desired.values(), *actual.values()] for t, _ in pts)
    events = {"decode": proof.scale_events(proof.load_run("e9-decode", base), "decode"),
              "prefill": proof.scale_events(proof.load_run("e9-prefill", base), "prefill")}
    change = 1790916147  # 2026-10-02 04:42:27 UTC: the prefill capacity 10,500 -> 1,800 (metrics/thu-one-session.log)
    fig, axes = plt.subplots(2, 1, figsize=(14, 7.5), sharex=True)
    for ax, pool in zip(axes, ("decode", "prefill"), strict=True):
        for pts, style, color, label in ((desired.get(pool, []), "--", ORANGE, "the planner asks"),
                                         (actual.get(pool, []), "-", BLUE, "KEDA runs")):
            pts = sorted(set(pts))
            ax.step([(t - t0) / 60 for t, _ in pts], [v for _, v in pts], style, where="post", color=color,
                    linewidth=3, label=label)
        ev = events[pool]
        if ev:
            for key, text in (("asked", "asks"), ("warm", "warm")):
                x = (ev[key] - t0) / 60
                ax.axvline(x, color=GRID, linewidth=2)
                ax.annotate(text, (x, 4.05), ha="center", fontsize=14)
        if pool == "prefill":
            ax.axvline((change - t0) / 60, color=INK, linestyle=":", linewidth=2)
            ax.annotate("capacity set\nfor the A100", ((change - t0) / 60, 2.6), ha="right", fontsize=14,
                        xytext=(-8, 0), textcoords="offset points")
        ax.set_title(f"The {pool} pool")
        ax.set_ylabel("pods")
        ax.set_ylim(0.5, 4.6)
        ax.grid(axis="y", color=GRID)
    axes[0].legend(loc="center right", frameon=False)
    axes[-1].set_xlabel("minutes from the start of the scale test")
    numbers = {p: {k: ev[k] for k in ("made_after_s", "engine_up_after_s", "warm_after_s", "planner_max",
                                     "replicas_max")} for p, ev in events.items() if ev}
    return _save(fig, "e9.png", out), numbers


def main() -> int:
    _style()
    for build in (e3, hop, warm, e9):
        path, numbers = build()
        print(path.relative_to(ROOT), json.dumps(numbers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
