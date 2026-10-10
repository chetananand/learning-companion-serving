"""Write notebook/part5_queue.ipynb (H-64): one section for each Part 5 question (H-65 to H-73),
and notebook/experiments.ipynb (H-04): one section for each experiment, and the four resources (H-23).

Each section has the question, the planned answer, and a code cell that loads a saved run from
metrics/ and makes a plot or a table. The notebook runs from the saved files, so a reader does not
need the cluster. Before the measurement sessions, each cell prints "no data yet".
The run ids are at the top of the notebook. Change them after each session.

Usage: python3 tools/make_notebook.py [--execute]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "notebook" / "part5_queue.ipynb"

SETUP = '''import sys
from pathlib import Path

from IPython.display import Image, display

ROOT = Path.cwd().parent if Path.cwd().name == "notebook" else Path.cwd()
sys.path.insert(0, str(ROOT))
from notebook import proof  # noqa: E402

# Run ids in metrics/ (sessions 1 and 2, 2026-09-29). 100% load = RATE100 = 0.9 scripts each second (the E2 knee).
RUNS = {
    "m4_150": "e3-c-150",        # M4 at 150%, layout C (P/D)
    "m1": "e11-pass",            # M1 traffic: M3 with the metrics proxy in pass mode (layout A, 100%)
    "m2": "e6-on",               # M2 shared prefix, prefix cache on (layout C, 100%)
    "m3": "e11-frozen",          # M3: pod B serves a frozen snapshot of an empty pod (layout A, 100%)
    "m4": "e3-c-100",            # M4 mixed interactive and batch (layout C, 100%)
    "m2_prefix_off": "e6-noprefix", "e1_8k": "e1-8k", "e1_24k": "e1-24k",
    "e12_abort": "e12-abort",    # M4 at 100%, 20% of the streams closed in the middle
    "e8_ramp": "e8-a-ramp",      # layout A: pod B restarts, then the warm controller ramps it
    "e8_jump": "e8-a-jump",      # layout A: the returned pod gets the full weight at once
    "e8_warmup": "e8-c-warmup", "e8_immediate": "e8-c-immediate",  # layout C: the one decode pod restarts
    "m2_fp8kv": "e6-fp8kv", "e2_soak": "e2-soak",
    "e14": ["e14-on-int", "e14-off-int", "e14-on-batch", "e14-off-batch"],  # split on/off, retrieve class
}


def show(path):
    if path is None:
        print("no data yet")
    else:
        display(Image(filename=str(path)))
'''

SECTIONS = [
    ("H-65", "Who sits in our queue and who sits in the vLLM waiting queue?",
     "Our queue is the flow-control queue of the llm-d router. A request waits there before the pick, in its "
     "priority band (interactive 10, batch 0) and for its tenant. The vLLM waiting queue holds a request after the "
     "pick, inside one pod. In `e3-c-150` (M4 at 150% load, layout C), our queue held up to 48 interactive and 24 "
     "batch requests. The vLLM waiting queue of the decode pod held up to 50 requests, and the prefill pod held up "
     "to 8. Most agent calls have fewer than 2,048 new tokens, so the router does not split them, and the decode "
     "pod also computes their prompts.",
     'show(proof.queue_before_and_after_pick(proof.load_run(RUNS["m4_150"])))'),
    ("H-66", "Show waiting, running, and preempted counts (V1 has no swap).",
     "vLLM V1 preempts by recompute. In `e3-c-150`, the decode pod ran 24 requests at most (its `--max-num-seqs`), "
     "and up to 50 waited. The prefill pod ran 4 at most. No pod preempted a request. The router flow control held "
     "the load at the door, and the KV use of the decode pod stayed at 90% or less.",
     'show(proof.engine_counts(proof.load_run(RUNS["m4_150"])))'),
    ("H-67", "Show the queue depth for each pod under each traffic mix.",
     "Our `orch_replica_queue_depth` is the router view of the queue of each pod (`router_pod_queue`), next to "
     "`vllm:num_requests_waiting`. At 100% load (0.9 scripts each second): M1 and M3 (layout A) kept 2 or fewer "
     "requests in each queue. M2 kept 6 or fewer. M4 (layout C) put up to 36 requests in the router queue of the "
     "decode pod, and up to 38 in its vLLM waiting queue. The prefill pod stayed at 9 or fewer.",
     'for mix in ("m1", "m2", "m3", "m4"):\n    print(mix, RUNS[mix])\n'
     '    show(proof.queue_depth_by_pod(proof.load_run(RUNS[mix])))'),
    ("H-68", "If a 32K RAG retrieve and a short agent decode are both ready, who goes first?",
     "E14 sends a retrieve of about 27,400 tokens and five short agent steps at the same time. Each arm has five "
     "rounds. In all rounds of all arms, an agent step got the first token. Our queue gives priority by class.\n\n"
     "The P/D split decides the cost. With the split, the long prefill runs on the prefill pod, and the agent steps "
     "start in 0.25 s (median). With no split, the long prompt shares the decode pod, and the agent steps need "
     "1.0 to 1.26 s.",
     'for row in proof.queue_order(RUNS["e14"]):\n    print(row)\nshow(proof.queue_order_plot(RUNS["e14"]))'),
    ("H-69", "PagedAttention packs KV. Prefix cache reuses KV. Which one saved memory on our shared-prefix mix?",
     "E6 runs M2 at 100% load in three arms: the GPU prefix cache on, off, and on with fp8 KV. PagedAttention "
     "packs the KV in all arms. The KV use of the decode pod was 13% on average. At this load, the prefix cache "
     "did not save memory.\n\n"
     "The prefix cache saved prefill work. On the decode pod, 44% of the prompt tokens were cache hits. The TTFT "
     "p50 went from 0.60 s (off) to 0.43 s (on). With the GPU prefix cache off, the LMCache tier gave back most "
     "of the hits: about 4,500 tokens each second in both arms.\n\n"
     "fp8 KV saved memory. The mean KV use fell to 5.5%, and each pod held twice the tokens (345,235 against "
     "173,657).",
     'for row in proof.kv_table([proof.load_run(RUNS[k]) for k in ("m2", "m2_prefix_off", "m2_fp8kv")]):\n'
     '    print(row)\nshow(proof.engine_counts(proof.load_run(RUNS["m2"])))'),
    ("H-70", "Give the engine flags for chunked prefill and continuous batching, and the reason for each value.",
     "The flags come from the run record of `e3-c-100`. The reasons are in `docs/spec/04-system-design.md`, "
     "section 3. The decode pod has `--max-num-seqs=24`, because its KV holds about 20 sequences at 24K tokens. "
     "With `--max-num-batched-tokens=2560`, it runs a prefill below the split threshold in one chunk. The "
     "threshold is 2,048 uncached tokens. vLLM refuses a value below the largest image item of Gemma 4 (2,496 "
     "tokens).\n\n"
     "The prefill pod has `--max-num-seqs=8`, because its requests leave after the hop. It has "
     "`--max-num-batched-tokens=16384`: large chunks for throughput. Both pods use `--scheduling-policy=priority`, "
     "`--enable-prefix-caching`, and `--block-size=64`.\n\n"
     "E15 changed one value at a time. With 32 decode sequences, the interactive TTFT p50 fell from 4.59 s to "
     "2.10 s, so E3 runs again with 32. A prefill chunk of 8,192 tokens made it worse (8.50 s).",
     'flags = proof.engine_flags(proof.load_run(RUNS["m4"]))\nfor pod, args in flags.items():\n'
     '    print(pod)\n    print("   ", " ".join(a for a in args if a.startswith("--max") or a.startswith("--sched")))'),
    ("H-71", "If the KV is full after admit, do we shed at the door or does the engine preempt?",
     "We shed at the door. The soak (E2) adds 0.05 scripts each second each minute. The first capacity shed "
     "came in minute 18 (503 `timeout_queue`): this is RATE100, 0.9 scripts each second. In the whole soak, "
     "3,645 calls were ok, and the router shed 43. The KV use of the decode pod stayed at 90% or less, and no "
     "pod preempted a request.",
     'print(proof.knee_rate(proof.load_run(RUNS["e2_soak"])))\nshow(proof.soak(proof.load_run(RUNS["e2_soak"])))'),
    ("H-72", "If the client is gone (aborted), who frees the KV and how?",
     "E12 closes 20% of the streams in the middle (46 calls). Envoy closed each of the 46 streams at the time of "
     "the client close, and it got no usage data for them. The vLLM API server sees the closed stream. Then "
     "`abort_requests` stops the request in the engine. The engine frees its KV blocks. vLLM v0.30.0 does not "
     "count "
     "these aborts in `vllm:request_success_total{finished_reason=\"abort\"}` "
     "(`vllm/v1/engine/output_processor.py`, `abort_requests`), so that counter stays at 0.",
     'show(proof.engine_counts(proof.load_run(RUNS["e12_abort"])))'),
    ("H-73", "After a worker returns, do we send 100% at once, or do we ramp while p99 holds?",
     "We ramp. The warm controller gives a returned pod the ramp label r10. While the TTFT p99 of the pod holds, "
     "the label goes to r25, r50, and r100. The router has a ramp scorer for this label.\n\n"
     "E8 in layout A deletes pod B at 180 s. With the ramp, the TTFT p95 of the returned pod in its first minute "
     "was 14.6 s. With a jump to 100%, it was 57.3 s. The ramp is a score, not a cap. In the first 10 s, the empty "
     "pod got 75% of the calls, because the queue scorer prefers it.\n\n"
     "In layout C, the one decode pod was down, and 182 to 184 calls got 503 `no_endpoints`. With the warmup "
     "routine, the TTFT p95 in the first minute of the new pod was 7.3 s. With no warmup, it was 10.9 s.",
     'for key in ("e8_ramp", "e8_jump", "e8_warmup", "e8_immediate"):\n'
     '    print(proof.new_pod_window(proof.load_run(RUNS[key])))\n'
     'show(proof.new_pod_plot([proof.load_run(RUNS[k]) for k in ("e8_ramp", "e8_jump")], name="e8-a-new-pod.png"))\n'
     'show(proof.warmup_first_minute([proof.load_run(RUNS[k]) for k in ("e8_warmup", "e8_immediate")]))'),
]


EXPERIMENTS_OUT = ROOT / "notebook" / "experiments.ipynb"
EXPERIMENTS = [
    ("Four resources (H-23)",
     "The four scarce resources, each with its measured peak. Decode slots and KV blocks set the limit of our "
     "traffic. The hop costs 0.5 to 0.8 s for each split request. A decode pod that restarts is out for about 5 "
     "minutes.",
     'for row in proof.four_resources():\n    print(row)'),
    ("E1: capacity at fixed prompt lengths",
     "Prompts of 8,000 and 24,000 tokens, straight to the gateway. All calls were ok up to 32 and 24 concurrent "
     "requests, with no preemption. At 24K tokens, the TTFT grows with the prefill queue.",
     'show(proof.capacity({8000: proof.load_run("e1-8k"), 24000: proof.load_run("e1-24k")}, {8000: 32, 24000: 20}))'),
    ("E2: the soak and the knee",
     "The soak adds 0.05 scripts each second each minute. The first capacity shed sets RATE100.",
     'print(proof.knee_rate(proof.load_run("e2-soak")))\nshow(proof.soak(proof.load_run("e2-soak")))'),
    ("E3: layout C (P/D) against layout A (two colocated replicas)",
     "Layout A was better at each load of the M4 replay. The decode pod of layout C was the bottleneck.",
     'show(proof.ttft_bars([(f"{arm} {p}%", f"e3-{arm.lower()}-{p}") for p in (50, 100, 150) for arm in ("C", "A")],\n'
     '                     "e3-topology.png", "E3: layout C (P/D) against layout A (two colocated replicas), M4"))'),
    ("E3 again on 8 x A100 80 GB (2026-10-01)",
     "No H100 had stock, so E3 ran again on one node with 8 x A100 80 GB. Both layouts had 32 decode sequences. "
     "The levels are scripts each second, lower than on the H100. Layout A had about half of the TTFT p50 of "
     "layout C at each level. At 0.45, the router shed 117 interactive calls in layout C, and 2 in layout A.",
     'show(proof.ttft_bars([(f"{arm} {lvl}", rid) for lvl, c, a in (("0.15", "e3-c32-r015", "e3-a32-r015"),\n'
     '                                                          ("0.30", "e3-c32-r030", "e3-a32-r030"),\n'
     '                                                          ("0.45", "e3-c32-50", "e3-a32-r045"))\n'
     '                      for arm, rid in (("C", c), ("A", a))],\n'
     '                     "e3-a100.png", "E3 on 8 x A100: layout C against layout A, 32 decode sequences"))'),
    ("E4: the hop through the LMCache tier against NIXL",
     "With the store barrier, the LMCache hop takes 0.5 to 0.8 s. NIXL between two pods used TCP and took about "
     "4 s.",
     'show(proof.hop_plot({"LMCache tier": "e4-hop", "NIXL over TCP": "e4-hop-nixl"}))'),
    ("E5: the split protects the other decode streams",
     "One extra prompt either splits or stays on the decode pod, next to 16 decode streams. The split keeps the "
     "ITL of the other streams low. Even with the split, the ITL p95 of 16 streams stayed above SLO-2.",
     'show(proof.split_itl_plot("e5-d2560"))\nshow(proof.split_itl_plot("e5-d4096"))'),
    ("E6: prefix cache and KV format",
     "The prefix cache saved prefill work, and fp8 KV saved memory.",
     'for row in proof.kv_table([proof.load_run(r) for r in ("e6-on", "e6-noprefix", "e6-fp8kv")]):\n    print(row)'),
    ("E7: ghost prefixes after a cache clear",
     "The CPU tier gave the cleared prefixes back at once, so the token hit ratio did not fall. The next "
     "section has the run with no CPU tier.",
     'for r in ("e7-precise", "e7-approx"):\n'
     '    print(json.dumps(json.loads((ROOT / "metrics" / r / "ghosts-by-threshold.json").read_text()), indent=1))'),
    ("E7 again: no CPU tier (2026-10-01)",
     "In layout A with no KV connector, the run clears the prefix cache of pod B at 150 s. Pod B sent one "
     "`AllBlocksCleared` event and no `BlockRemoved` event. The router still sent the next call of each warm "
     "session to pod B. The precise index did this for 15 of 15 sessions, and the "
     "approximate index for 10 of 10. Each of these calls missed the cache once. Thus the precise index did not "
     "act on the clear.",
     'for r in ("e7b-precise", "e7b-approx2"):\n'
     '    g = json.loads((ROOT / "metrics" / r / "ghosts.json").read_text())\n'
     '    print(r, {k: g[k] for k in ("warm_sessions", "eligible", "ghosts", "ghost_ratio")})\n'
     '    for ep, v in json.loads((ROOT / "metrics" / r / "kv-events.json").read_text())["by_endpoint"].items():\n'
     '        print("   ", ep, "stored", v["stored"], "removed", v["removed"], "cleared", v["cleared"])'),
    ("E8: a pod comes back",
     "Layout A: the ramp against the jump. Layout C: the warmup routine against none.",
     'for r in ("e8-a-ramp", "e8-a-jump", "e8-c-warmup", "e8-c-immediate"):\n'
     '    print(proof.new_pod_window(proof.load_run(r)))\n'
     'show(proof.new_pod_plot([proof.load_run(r) for r in ("e8-a-ramp", "e8-a-jump")], name="e8-a-new-pod.png"))\n'
     'show(proof.new_pod_plot([proof.load_run(r) for r in ("e8-c-warmup", "e8-c-immediate")],\n'
     '                        name="e8-c-new-pod.png"))\n'
     'show(proof.warmup_first_minute([proof.load_run(r) for r in ("e8-c-warmup", "e8-c-immediate")]))'),
    ("E9: which pool scales (2026-10-01, one node)",
     "At each time, only one pool was free to grow, and KEDA permitted 2 pods for each pool. Decode-heavy "
     "load: the planner asked for a second decode pod, and KEDA made it 15 s later. Prefill-heavy load: the "
     "planner asked for a second prefill pod only after we set its capacity to the A100 value (the dotted "
     "line). Each new pod got the warm label about 4 minutes after the request.",
     'for rid, pool in (("e9-decode", "decode"), ("e9-prefill", "prefill")):\n'
     '    print(rid, proof.scale_events(proof.load_run(rid), pool))\n'
     'show(proof.e9_scale_plot(["e9-decode", "e9-prefill"],\n'
     '                         marks={"prefill capacity 10,500 -> 1,800": 1790916147}))'),
    ("E10: tenant isolation, FCFS against EDF",
     "The noisy tenant got 429 `tenant_tokens`. In the interactive band, FCFS gave a lower TTFT than EDF.",
     'show(proof.ttft_bars([("FCFS", "e10-fcfs"), ("EDF", "e10-edf")], "e10-order.png",\n'
     '                     "E10: the order inside the interactive band (M4, 100%)"))'),
    ("E11: stale metrics",
     "Pod B serves real metrics, a frozen snapshot of an empty pod, or no answer. With the frozen snapshot, "
     "the router sent 80% of the work to pod B. With no answer, it sent 8%. With real metrics, it sent 51%.",
     'for m in ("pass", "frozen", "stall"):\n    print(m, proof.work_share(proof.load_run(f"e11-{m}")))\n'
     'show(proof.ttft_bars([("pass", "e11-pass"), ("frozen", "e11-frozen"), ("stall", "e11-stall")],\n'
     '                     "e11-stale.png", "E11: stale metrics from pod B (M3, layout A)"))'),
    ("E14: who goes first",
     "A retrieve of about 27,400 tokens and five short agent steps are ready at the same time.",
     'show(proof.queue_order_plot(["e14-on-int", "e14-off-int", "e14-on-batch", "e14-off-batch"]))'),
    ("E15: engine flags",
     "One change at a time, against the base of E3 at 100% load. 32 decode sequences gave the best TTFT.",
     'show(proof.ttft_bars([("base", "e3-c-100"), ("seqs 16", "e15-seqs-16"), ("seqs 32", "e15-seqs-32"),\n'
     '                      ("prefill chunk 8K", "e15-mnbt-8k")], "e15-flags.png", "E15: engine flags (M4, 100%)"))'),
    ("E16: the CPU tier after a restart",
     "With the LMCache tier (K2), the restarted decode pod loaded its prefixes from the tier. With no tier (K0), "
     "the TTFT p50 after the restart was about five times higher.",
     'show(proof.ttft_bars([("K2", "e16-k2"), ("K2 restart", "e16-k2-restart"), ("K0", "e16-k0"),\n'
     '                      ("K0 restart", "e16-k0-restart")], "e16-tier.png", "E16: the CPU tier (M2)"))'),
    ("E17: the guard",
     "The chat guard blocked all attack turns and no benign turn. The page check missed half of the injected "
     "pages.",
     'for f in ("latency.json", "pages.json"):\n'
     '    d = json.loads((ROOT / "metrics" / "e17" / f).read_text())\n'
     '    print(f, {k: v for k, v in d.items() if k not in ("real_scores", "injected_scores")})'),
    ("The demo check on 8 x A100 80 GB (2026-10-01)",
     "The acceptance run (`app/demo_check.py`) after the demo fixes. Three reports are debug runs of D-07 during "
     "the E3 load. `docs/results.md` has a note for each report. D-08 passed after the fix. D-07 now gives an "
     "answer, but its claims have no evidence: the docs page gave 404.",
     'for f in sorted((ROOT / "metrics" / "demo").glob("demo-check-20261002T*.json")):\n'
     '    d = json.loads(f.read_text())\n'
     '    items = d if isinstance(d, list) else d.get("results", [])\n'
     '    print(f.stem, " ".join(f\'{it["id"]} {"pass" if it["pass"] else "fail"}\' for it in items))'),
]


def build_experiments() -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    cells = [nbf.v4.new_markdown_cell(
        "# The experiments (H-04)\n\nEach section loads saved runs from `metrics/` and makes a plot or a table. "
        "The notebook does not need the cluster. The tables of all runs are in `docs/results.md`, and the report "
        "is `DESIGN.md`."), nbf.v4.new_code_cell("import json\n" + SETUP)]
    for title, text, code in EXPERIMENTS:
        cells.append(nbf.v4.new_markdown_cell(f"## {title}\n\n{text}"))
        cells.append(nbf.v4.new_code_cell(code))
    nb.cells = cells
    return nb


def build() -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    cells = [nbf.v4.new_markdown_cell(
        "# Part 5: queue (H-64)\n\nEach answer loads a saved run from `metrics/` and makes a plot or a table. "
        "The notebook does not need the cluster. Before the measurement sessions, a cell prints \"no data yet\"."),
        nbf.v4.new_code_cell(SETUP)]
    for hid, question, answer, code in SECTIONS:
        cells.append(nbf.v4.new_markdown_cell(f"## {hid}: {question}\n\n{answer}"))
        cells.append(nbf.v4.new_code_cell(code))
    nb.cells = cells
    return nb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    for nb, out in ((build(), OUT), (build_experiments(), EXPERIMENTS_OUT)):
        if a.execute:
            from nbclient import NotebookClient

            NotebookClient(nb, timeout=300, resources={"metadata": {"path": str(ROOT / "notebook")}}).execute()
        out.write_text(nbf.writes(nb))
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
