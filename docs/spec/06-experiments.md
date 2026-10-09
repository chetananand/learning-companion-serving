# Proof plan: gates and experiments

The handout says: measure the app, not a generic trace (L728). This file lists the gates and the experiments that prove our design. Each experiment names the checklist items that it closes.

## 1. Rules for every run

1. Smoke the engine before we debug admission control and routing (H-82).
2. Warm every pod before we measure. Never quote TTFT from a cold pod as the SLO (H-118). The notebook also drops the first 60 s of each replay run (`Run.steady` in `notebook/proof.py`).
3. For each run, record the vLLM version, the image digest, the model id, and all engine flags.
4. Also record the GPU SKU, the llm-d router config, the Agent Router config, and the `edge` config. Save all run data in `metrics/<run-id>/run.json`.
5. Save the raw data: Prometheus range queries as JSON, the `edge` access log, the hop records, and the client-side results. Put them in `metrics/<run-id>/`.
6. Make the plots from the saved data with the notebook, not from screenshots.
7. Give token counts next to each time value. Do not compare seconds across models without token counts (H-117).
8. Keep Grafana images of each run (`tools/grafana_shots.py`). The images show all dashboards of the handout walk (L732) for the run window. They render after node 1 stops, and `walk.md` puts them in the order of the talk. Gate G0 checks that each panel has data before any H100 session.

## 2. Traffic mixes (H-84, H-85)

The Companion API writes the shape of each LLM call to a trace file (FR-14). The trace file has no prompt text. Thus the API also has a capture mode (`APP_CAPTURE_PATH`). It saves the exact request body of each LLM call on the cluster volume. The replayer sends these real prompts again at a set arrival rate. It gives each replayed script one new session id, so the affinity stays real.

How we make the capture (`app/loadgen/questions.py`):

1. Build the question sets from the corpus:
   - b1: 200 unique questions.
   - b2: the 10 demo questions and 10 more.
   - b3: 50 verified-mode questions.
2. Send the sets through the Companion API at a low rate. The session id of each turn carries the set name (`b1-0007`).
3. Run the freshness sweep once. It adds the batch scripts.
4. Freeze the capture (`cluster/g0.sh freeze`). The replayer reads `frozen.jsonl`, so later turns do not change the replay input. The laptop backup keeps a copy for the next session.

A script is the ordered list of the calls of one turn, of one S4 loop, or one S3 answer call alone. The replayer sends the calls of a script one after the other, as the agent did.

| Mix | Content | Based on |
|---|---|---|
| M1 unique | Draft calls (S3) with different chunk sets. Only the 2K system prefix is shared. | class7 `t1_unique` |
| M2 shared-prefix | Many agent sessions in the verify loop (S4), plus popular questions that retrieve the same chunks. | class7 `t2_shared` |
| M3 stale metrics | M1, with a metrics proxy on one pod that serves a 15 s old snapshot of an empty pod. | class7 `t3_stale` |
| M4 mixed | 70% interactive user turns (quick and verified), 20% batch (sweep), 10% long agent sessions. | class7 `mixed` |
| Soak | M4 with an arrival rate that grows each minute. | class7 advanced 1 |

Replayer options: Poisson arrivals, target rate, class mix, abort probability, tenant list, and one more tenant stream (`--extra`, E10). The router policy comes from the router config of the run. Interactive scripts use the tenants `load-1` to `load-32`. Batch scripts use the tenant `sweep`. There are 32 load tenants, so the load reaches the engine limit before the tenant windows. Each window stays a real limit for one user.

The replayer writes one line for each call (`client.jsonl`) and a summary. `cluster/loadgen.sh` runs each tool as a Job in the cluster. Then it copies the results, the run record (`run.json`, rule 4), and the Prometheus range queries (rule 5) into `metrics/<run-id>/`.

For E1, `app/loadgen/capacity.py` sends N concurrent requests with long prompts from real bookmark text. Each prompt starts with a unique line, so no prefix-cache hit hides the prefill. `ignore_eos` makes each output 512 tokens long. Each worker uses its own load tenant. E1 sends to the gateway, not to `edge`, because E1 measures the engine. Also, the safety model of guard stage 2 has a context of only 8,192 tokens (G0).

E4 and E5 need an exact split. Thus `tools/pd_probe.py` sends its requests straight to the pods: to the routing sidecar of the decode pod, with or without the header `x-prefiller-host-port`. `tools/queue_order.py` (E14) sends its requests through `edge`. Both run as Jobs (`cluster/loadgen.sh <run-id> tool ...`). Their long prompts use real chunk text from the capture file (`tools/corpus.py`).

The exact commands, the time budgets, and the order of the runs are in `docs/runbooks/experiments.md`.

## 3. Gates (go or no-go decisions)

### Gate G0: platform check on node 2 (dev day 1, 2 x A6000)

The runbook is `cluster/g0.sh`. It runs these stages in order:

1. The node IPs, the k3s bootstrap, the kubeconfig, and the tunnel.
2. The images.
3. The `sim` overlay (the llm-d simulator in both engine pods) and the smoke tests.
4. The `dev` overlay (the dev model), the ingest job, and the evidence report.

| Check | Pass rule |
|---|---|
| k3s, HAMi, KEDA, kube-prometheus-stack, Envoy Gateway, the Agent Router, and the llm-d router come up with the pinned versions. | All pods Ready. |
| The HAMi slice memory shows inside the container. | `nvidia-smi` in the pod shows the slice size. |
| The full request path works with the llm-d simulator. | One request passes `edge`, `guard`, the Agent Router, and the router, and returns a stream. |
| Codes: a tenant over its window, and a router queue timeout. | The Agent Router gives 429. The router gives 429 with `x-llm-d-request-dropped-reason`, and `edge` returns 503 with the reason. |
| Warm gate and ramp: a pod without `companion.io/warm=true` gets no traffic. A change of `companion.io/ramp` changes the scores. | Router logs and metrics show both. |
| P/D with the dev model (`gemma-4-E4B-it`) across GPU 0 and GPU 1 on HAMi slices, with the hop through the LMCache tier. | One split request completes. The decode pod loads the prompt from the tier (cached tokens). G0 passed on 2026-09-28. |
| KV events reach the router. | The router prefix index grows. |
| `cached_tokens` is in the usage block. | The field is present. |
| Guard: `/v1/checks` runs both input rails. Nemotron 3.5 runs on our vLLM image, or on v0.20.2. Prompt Guard 2 has a server. | A safe prompt passes. An injected prompt and an unsafe prompt get 400. Guard p95 latency is recorded. |
| SIE serves embeddings, rerank, and one OCR model on slices. | Three test calls return. |
| The source of the hop records. | One split request gives one hop record with the source pod and the destination pod. |
| NodePorts listen only on 127.0.0.1. | `ss -ltnp` shows 127.0.0.1. |

If a check fails, we record the fallback in `DEBATE-LOG.md`: vLLM v0.26.0 for the engine, router v0.10, or K0 (no LMCache).

### Gate G1: model and KV tier (2 x H100 SXM, about 3 hours)

Candidates: `RedHatAI/gemma-4-31B-it-FP8-dynamic` (main) and `meta-models/Muse-Glimmer-30B` with online FP8 (challenger). See ADR-003.

| Test | Pass rule |
|---|---|
| Tool-call suite: 40 cases from our agent (search, fetch, screenshot, verdict JSON). | 95% or more valid calls with correct arguments. We record the pass rate. |
| Screenshot reading: 10 screenshots of real bookmark pages. | 8 or more correct answers to a fixed question. |
| Prefix-cache correctness: 2,000 requests with shared prefixes and random lengths. | Zero NaN or empty outputs. Prefix hit ratio 60% or more. |
| Warm TTFT at 8K prompt, single request. | 1.0 s or less. |
| Decode speed at 8 concurrent requests. | Inter-token latency p95 of 40 ms or less. |
| P/D startup with `MultiConnector` (NIXL and LMCache) and HMA on, the same block size on both pods. | Both pods start. One split request completes with correct output. |
| CPU tier: a prefix that left HBM comes back from LMCache. | LMCache hits go up, and the TTFT is lower than a cold prefill. |
| CPU tier after a vLLM pod restart. | The first request after the restart hits the CPU tier. |

Decision rules:

1. Model: Glimmer replaces Gemma 4 only if Gemma 4 fails a test that Glimmer passes. If both pass all tests, Glimmer wins only with a tool-call pass rate that is at least 10 points higher.
2. KV tier: if the LMCache tests fail for the chosen model, we use K0 (GPU prefix cache only) and write the reason in `DESIGN.md`.
3. We record the numbers in ADR-003 and ADR-002.

### Gate G2: data-plane choice (dev days, node 2)

1. Pick the OCR model (`paddleocr-vl`, `glm-ocr`, or `lightonocr` in SIE) with the best result on the same 10 screenshots.
2. Pick `bge-m3` for embeddings, unless it fails on SIE.
3. Pick the Gemma 4 image token budget (280, 560, or 1,120) from the screenshot result and the TTFT.
4. Record the result in ADR-007.

## 4. Experiments

| ID | Name | Topology | Mix | Checklist |
|---|---|---|---|---|
| E1 | Capacity: paper against measured | T2 | synthetic ramp at 8K and 24K | H-36 to H-42, H-104 |
| E2 | Soak | T2 | Soak | H-97, H-124 |
| E3 | Layout: option A (two replicas) against option C (P/D with the decider) | T2 | M4 at 3 load levels | H-45 |
| E4 | Hop proof and "what is not copied" | T2 | M2 | H-75 to H-77, H-101, H-110 |
| E5 | Split threshold and decode chunk size | T2 | synthetic, uncached 1K to 16K | H-45, H-47 |
| E6 | Paged KV against prefix cache, and FP8 KV | T2 | M2 | H-69 |
| E7 | Ghost cache: approximate against exact prefix index | T2 | M2 | H-102, R-04 |
| E8 | Warmth and recovery ramp | T2 | M4 | H-73, H-78, H-79, H-111, H-118 |
| E9 | Scale: which pool (recorded offline) | T4 | decode-heavy, then prefill-heavy | H-53, H-94, H-106 |
| E10 | Tenant isolation and codes | T2 | M4 plus `noisy` | H-22, H-81, H-100, H-114 |
| E11 | Stale metrics | T2 | M3 | H-62 |
| E12 | Client abort | T2 | M4 with 20% aborts | H-72 |
| E13 | Overflow gate (no live provider) | T2 | M4 above capacity | H-17, H-48, H-112 |
| E14 | Queue order: 32K retrieve against a short agent step | T2 | synthetic pair | H-65, H-68 |
| E15 | Engine flags | T2 | M4 | H-70 |
| E16 | LMCache tier for agent sessions and restarts | T2 | M2 | H-93, H-98 |
| E17 | Guard cost and accuracy | node 2 | user turns, real pages, injected pages | H-16, H-56 |

We dropped the old E17 (NIXL against MooncakeConnector). See `DEBATE-LOG.md`, point 3.

### E1 Capacity: paper against measured

1. Read the startup log of each pod. Record the KV cache size in tokens and the maximum concurrency.
2. Send N concurrent requests of 8K tokens with `max_tokens` 512. Increase N until `vllm:num_preemptions_total` goes up.
3. Do step 2 again at 24K tokens.
4. Compare the N at the first preemption with the paper value.

Output: `plots/capacity-paper-vs-measured.png`, a table in `DESIGN.md`.

### E2 Soak

1. Run the soak mix. Increase the arrival rate each minute.
2. Record admitted, completed, and shed requests each 5 s, by reason.
3. Pass rule: the system starts to shed before completions go down. Completions never fall to zero.
4. RATE100 is the arrival rate of the last full minute before the first capacity shed (`knee_rate` in `notebook/proof.py`). A 429 `tenant_tokens` is not a capacity shed. The other experiments use RATE100 as the 100% load.

Output: `plots/soak.png`.

### E3 Layout

1. Run M4 at 50%, 100%, and 150% of RATE100 (E2) for 10 minutes each. E1 gives the concurrency, and the soak gives the arrival rate of M4 at that concurrency.
2. Arm A: two pods with the role `prefill-decode` and the `single-profile-handler`. Arm C: our layout (`disagg-profile-handler` with the decider).
3. Measure goodput (requests inside SLO-1 and SLO-2 each second), TTFT p95 and p99, ITL p95, and tokens each second.

Output: `plots/layout-goodput.png`. The numbers go into ADR-005.

### E4 Hop proof

1. `tools/pd_probe.py hop` seeds the decode pod with a known prefix of 4K tokens (straight to vLLM).
2. It sends split requests that share this prefix, then split requests that do not share it.
3. Compare the moved bytes of each group with the formula bytes for the full prompt and for the uncached part. The base hop gives the bytes from the LMCache server. A second run with the preset `nixl-hop` gives the NIXL bytes and time, so E4 also compares the two hop media.
4. Run M2 through the gateway for 5 minutes at 50% of RATE100. `tools/hop_records.py` writes each hop in `metrics/<run-id>/hops.jsonl`.

Output: the hop record, a table "expected bytes against transferred bytes", and the list of what the hop does not copy.

### E5 Split threshold and decode chunk size

1. `tools/pd_probe.py split` keeps 16 decode streams running on the decode pod.
2. It sends requests with 1K, 2K, 4K, 8K, and 16K uncached tokens. It sends each size once with a split (the header `x-prefiller-host-port`) and once on the decode pod only (no header). It does this 3 times.
3. Measure the TTFT of the new request. Also measure the ITL of the 16 streams until its first token.
4. The threshold is the smallest size where the split gives the lower total cost. It becomes `pd_non_cached_tokens` in the router policy.
5. Repeat with decode `--max-num-batched-tokens` 4096. The base value is 2560. vLLM refuses a value below 2,496, the largest image item of Gemma 4 (G1).

Output: `plots/split-threshold.png`, the value of `nonCachedTokens` and the decode chunk size.

### E6 Paged KV against prefix cache

1. Run M2 with prefix caching on, then off.
2. Measure KV use, prefill tokens each second, prefix hit ratio, and TTFT.
3. Run M2 again with `--kv-cache-dtype fp8`.
4. The LMCache tier stays on in all arms. Thus the GPU KV use answers H-69, and the TTFT includes the CPU-tier hits.

Output: `plots/shared-prefix-kv.png`. The answer to H-69 in `DESIGN.md`.

### E7 Ghost cache

1. Run M2 with the prefix scorer. Record the prefix hit ratio.
2. At 150 s, clear the prefix cache of the decode pod (vLLM `POST /reset_prefix_cache`, through `kubectl exec`). This endpoint needs `VLLM_SERVER_DEV_MODE=1` (vLLM v0.30.0 source), so the run uses the `devmode` variant. Do not tell the router.
3. Arm A: the approximate prefix index (no KV events). Arm B: the exact index (`precise-prefix-cache-producer` with KV events).
4. Measure the ghost count (`tools/ghosts.py`, from the Envoy access log), the prefix hit ratio, and the TTFT for 2 minutes.

Output: `plots/ghost-cache.png`.

### E8 Warmth and recovery ramp

1. Delete the decode pod. Kubernetes starts a new pod.
2. Arm A: route traffic as soon as the pod is ready (warm label set at once). Arm B: route traffic only after the warmup routine.
3. Record the time to ready, the time to warm, the first TTFT, and the first hop time.
4. After the pod is warm, give the TTFT again (H-79).
5. Compare the ramp labels (`r10` to `r100` in 30 s) with a jump to `r100`. Measure the fleet TTFT p99.
6. Arms A and B run in layout C. The ramp test (step 5) needs two pods of one role, so it runs in layout A.

Output: `plots/warmup-ttft.png`, `plots/recovery-ramp.png`.

### E9 Scale: which pool (T4 session, recorded offline)

1. Send a decode-heavy mix (long outputs, many sessions). The planner rule must ask for 2 decode pods. KEDA adds `vllm-decode-1`.
2. Send a prefill-heavy mix (verified turns with large chunks). The planner rule must ask for 2 prefill pods.
3. Record `companion:planner_desired_replicas`, the KEDA scaler value, the replicas, the warm time, the ramp, and the TTFT.
4. Record a screen video of the "Pods / replicas / KEDA" dashboard for each run.

Output: `plots/scale-which-pool.png`, the videos in `plots/video/`.

### E10 Tenant isolation and codes

1. Run M4 for the load tenants. Add `noisy` at about 10 times its window. The window is 60,000 tokens each minute. The stream sends 0.25 user turns each second, at about 40,000 tokens each turn.
2. Pass rules: `noisy` gets 429 `tenant_tokens`. The TTFT p95 of the other tenants stays inside SLO-1. No 429 goes to the overflow.
3. Check the code table: a tenant over its limit gets 429, even when the fleet is full. A full fleet and a tenant under its limit give 503.
4. Compare `fcfs-ordering-policy` with `edf-ordering-policy` inside the interactive band (the router variant `flow_control.ordering`).

### E11 Stale metrics

1. Run M3 in layout A. The metrics proxy in pod B (`vllm-decode`) makes pod B look empty. Do one run for each proxy mode:
   - `pass`: the real metrics.
   - `frozen`: the snapshot of the idle pod.
   - `stall`: no answer, so the last sample ages.
2. Measure the share of traffic to B and the TTFT p99.
3. Record how the router treats the stale metrics (**VERIFY**), and compare it with the class7 rule "unknown is not idle".

### E12 Client abort

1. Run M4. The replayer closes 20% of the streams in the middle.
2. Pass rules: `vllm:request_success_total{finished_reason="abort"}` goes up. KV use goes back to the base level after the run. No KV leak.
3. For aborts after the P leg, the stored KV stays in the LMCache tier until LRU evicts it (with `nixl-hop`: `vllm:nixl_num_kv_expired_reqs` goes up after the lease).

### E13 Overflow

The handout makes a hosted API "overflow at most" (L523). It asks for the gate and a named target with a limiter, not for a live call. Thus E13 runs with no provider key. The gate decides as usual, and the `edge` access log keeps each decision (`would_leave`, or `stay` with the reason).

1. Run M4 at 150% of RATE100.
2. Pass rules: each capacity reject of an interactive request with `X-Allow-Overflow: true` has `would_leave`. Batch requests stay (`batch_stays`). Requests with `X-Allow-Overflow: false` stay (`privacy_switch`). No 429 has `would_leave` (handout: "A 429 that overflowed").
3. The unit tests prove the rest: a leave passes the limiter first, and a provider error gives the client the local outcome (`control/edge/tests`).

### E14 Queue order

1. At the same moment, send one 32K-token retrieve and five short agent steps.
2. Arms: both interactive, retrieve as batch, and the split on or off. Split off: `pd_non_cached_tokens` set high, so the decider never splits.
3. Record the order in the flow-control queue, the vLLM queue time, and the TTFT of each request.

### E15 Engine flags

1. Change `max-num-batched-tokens` on the prefill pod: 8192, 16384.
2. Change `max-num-seqs` on the decode pod: 16, 24, 32.
3. Measure ITL p95, TTFT p95, and preemptions under M4.

Output: the flag table with reasons (H-70).

### E16 LMCache tier

1. Run M2 with many agent sessions, so the HBM prefix cache evicts blocks.
2. Arm A: K0 (no LMCache, the overlay `t2-k0`). Arm B: K2 (LMCache MP, 150 GiB).
3. Measure the prefix hit ratio (GPU and CPU), the TTFT p95, and the prefill tokens each second.
4. Restart the decode pod in each arm. Measure the first TTFT after warm.

Output: `plots/lmcache-tier.png`.

### E17 Guard cost and accuracy

1. Send 200 user turns through `guard`. Measure the stage 2 latency (p50 and p95) with a cold and a warm verdict cache.
2. Send 50 real bookmark pages and 50 pages with injected instructions through the page check.
3. Record the false-positive rate and the missed injections. Tune the Prompt Guard 2 threshold.
4. `control/guard/eval.py` runs in an `edge` pod, with the guard clients of `edge`.

Output: a table in `DESIGN.md`.

## 5. The Part 5 notebook (H-64)

`notebook/part5_queue.ipynb` answers H-65 to H-73. Each answer has a code cell that loads a saved scrape and a plot or a table. The notebook runs from the saved files, so a reader does not need the cluster. If the cluster is up, a flag lets the notebook query Prometheus live.

## 6. Recordings for the talk

The talk shows charts. A live demo is optional (`DEBATE-LOG.md`, point 6).

1. Session 2: a screen video of the demo questions from start to end (see `08-question-bank.md`).
2. E9: a screen video of each scale-out.
3. All plots come from the saved data in `metrics/`.
4. The Grafana walk of the talk uses the images of each run (`metrics/<run-id>/grafana/walk.md`). It shows the eight dashboards of the handout:
   - cluster, success and failures, gateway and admission, router
   - queue depth by pod, vLLM, the KV hop store, and pods, replicas, and KEDA

## 7. GPU time for the proof

| Block | Topology | Hours (estimate) |
|---|---|---|
| G0, G2, and dev work | node 2 only | 16 |
| G1 | 2 x H100 SXM and node 2 | 3 |
| E1 to E8, E10 to E16 | 2 x H100 SXM and node 2 | 10 (session 1: 5.5, session 2: 4.5) |
| E9 | 4 x H100 SXM and node 2 | 2.5 |
| E17 | node 2 (during dev days) | 1 |
