# Results

This file comes from `tools/report_numbers.py`, which reads the saved runs in `metrics/`. Do not change it by hand. Run the script again after a session.

The load levels come from E2: 100% load is RATE100, 0.9 scripts each second. TTFT is the time to the first token at the client. The tables use all calls of a run, with no warmup cut.

## E1 Capacity at fixed prompt lengths

`e1-8k`: 8,000 prompt tokens, 512 output tokens, straight to the gateway.

| Concurrent | Ok | TTFT p50 (s) | TTFT p95 (s) | Latency p50 (s) |
|---|---|---|---|---|
| 1 | 3 of 3 | 0.98 | 1.30 | 11.07 |
| 2 | 6 of 6 | 1.49 | 2.16 | 12.22 |
| 4 | 12 of 12 | 1.64 | 3.00 | 12.64 |
| 8 | 24 of 24 | 1.63 | 6.25 | 13.61 |
| 16 | 48 of 48 | 2.26 | 11.39 | 15.26 |
| 24 | 72 of 72 | 6.99 | 14.19 | 21.21 |
| 32 | 96 of 96 | 12.91 | 20.04 | 27.33 |

`e1-24k`: 24,000 prompt tokens, 512 output tokens, straight to the gateway.

| Concurrent | Ok | TTFT p50 (s) | TTFT p95 (s) | Latency p50 (s) |
|---|---|---|---|---|
| 1 | 2 of 2 | 3.28 | 3.67 | 17.82 |
| 2 | 4 of 4 | 3.72 | 5.80 | 18.69 |
| 4 | 8 of 8 | 3.73 | 11.08 | 19.31 |
| 8 | 16 of 16 | 4.08 | 18.91 | 21.69 |
| 12 | 24 of 24 | 11.75 | 27.39 | 28.93 |
| 16 | 32 of 32 | 21.21 | 33.37 | 38.34 |
| 24 | 48 of 48 | 39.20 | 53.73 | 55.94 |

## E2 Soak: the knee (RATE100)

| Run | RATE100 (scripts/s) | First capacity shed | Interactive ok | Interactive sheds | Batch ok | Batch sheds |
|---|---|---|---|---|---|---|
| `e2-soak` | 0.90 | minute 18: 503 timeout_queue | 2,877 of 2,885 | 8 400 prompt_injection | 768 of 811 | 43 503 timeout_queue |

## E3 Topology: layout C (P/D) against layout A (two whole pods), M4

| Arm | Run | Interactive ok | TTFT p50 (s) | TTFT p95 (s) | Interactive sheds | Batch ok |
|---|---|---|---|---|---|---|
| C, 50% | `e3-c-50` | 1,316 of 1,318 | 1.19 | 2.04 | 2 400 prompt_injection | 400 of 400 |
| A, 50% | `e3-a-50` | 1,326 of 1,327 | 0.65 | 1.05 | 1 400 prompt_injection | 550 of 552 |
| C, 100% | `e3-c-100` | 2,387 of 2,402 | 4.59 | 15.95 | 11 503 timeout_queue, 4 400 prompt_injection | 418 of 511 |
| A, 100% | `e3-a-100` | 2,409 of 2,412 | 0.84 | 1.57 | 3 400 prompt_injection | 895 of 895 |
| C, 150% | `e3-c-150` | 2,639 of 2,917 | 15.78 | 21.45 | 273 503 timeout_queue, 5 400 prompt_injection | 45 of 195 |
| A, 150% | `e3-a-150` | 3,416 of 3,527 | 11.02 | 21.10 | 107 503 timeout_queue, 4 400 prompt_injection | 254 of 384 |

## E4 The hop: LMCache server against NIXL

| Hop | Run | Prefix | Ok | TTFT median (s) | Cached tokens (median) | Prompt tokens (median) |
|---|---|---|---|---|---|---|
| LMCache server + store barrier | `e4-hop` | shared | 8 | 0.52 | 8,960 | 9,100 |
| LMCache server + store barrier | `e4-hop` | unshared | 8 | 0.78 | 6,144 | 6,398 |
| NIXL (UCX over TCP) | `e4-hop-nixl` | shared | 8 | 4.27 | 7,808 | 9,101 |
| NIXL (UCX over TCP) | `e4-hop-nixl` | unshared | 8 | 4.14 | 0 | 6,402 |

On the M2 mix (`e4-m2`, 50% load), 16 of 356 calls split (4.5%). The other 340 stayed on the decode pod.

## E5 The split decision

16 decode streams run. One extra prompt either splits (prefill pod, then the hop) or stays on the decode pod. The ITL is the p95 of the other streams while the prompt runs.

| Run (decode chunk) | Prompt tokens | TTFT split (s) | TTFT local (s) | ITL p95 split (s) | ITL p95 local (s) |
|---|---|---|---|---|---|
| `e5-d2560` | 1,000 | 1.31 | 1.00 | 0.10 | 0.10 |
| `e5-d2560` | 2,000 | 1.55 | 1.32 | 0.11 | 0.15 |
| `e5-d2560` | 4,000 | 1.63 | 1.47 | 0.12 | 0.17 |
| `e5-d2560` | 8,000 | 1.88 | 1.40 | 0.07 | 0.24 |
| `e5-d2560` | 16,000 | 3.33 | 2.74 | 0.09 | 0.27 |
| `e5-d4096` | 1,000 | 1.32 | 0.90 | 0.10 | 0.10 |
| `e5-d4096` | 2,000 | 1.60 | 1.20 | 0.11 | 0.19 |
| `e5-d4096` | 4,000 | 1.68 | 1.29 | 0.12 | 0.24 |
| `e5-d4096` | 8,000 | 2.62 | 1.39 | 0.07 | 0.35 |
| `e5-d4096` | 16,000 | 3.27 | 2.63 | 0.09 | 0.42 |

## E6 Prefix cache and KV format (M2, 100%)

| Run | KV use mean | KV use max | Prefix hit ratio | LMCache hit tokens/s | TTFT p50 (s) | TTFT p95 (s) |
|---|---|---|---|---|---|---|
| `e6-on` | 0.14 | 0.57 | 0.44 | 4,374 | 0.43 | 1.28 |
| `e6-noprefix` | 0.13 | 0.45 | - | 4,471 | 0.60 | 1.29 |
| `e6-fp8kv` | 0.06 | 0.26 | 0.68 | 5,747 | 0.23 | 1.77 |

## E7 Ghost prefixes after a cache clear (M2, 50%)

| Run | Phase | Requests | Split | Not split, 2,048+ uncached | Token hit ratio |
|---|---|---|---|---|---|
| `e7-precise` | before | 180 | 13 | 20 | 0.76 |
| `e7-precise` | after_60s | 87 | 3 | 7 | 0.79 |
| `e7-precise` | after | 179 | 4 | 12 | 0.81 |
| `e7-approx` | before | 167 | 30 | 6 | 0.88 |
| `e7-approx` | after_60s | 85 | 7 | 3 | 0.85 |
| `e7-approx` | after | 177 | 13 | 4 | 0.87 |

## E8 A pod comes back: warmup and ramp (M4, 70%)

| Arm | Run | 503 no_endpoints (client) | Calls, first minute | First minute p50 (s) | First minute p95 (s) | Minutes 2 to 4 p50 (s) | Minutes 2 to 4 p95 (s) |
|---|---|---|---|---|---|---|---|
| layout C, warmup | `e8-c-warmup` | 184 | 169 | 1.03 | 7.28 | 2.38 | 12.68 |
| layout C, no warmup | `e8-c-immediate` | 182 | 198 | 1.29 | 10.89 | 1.79 | 12.74 |
| layout A, warmup + ramp | `e8-a-ramp` | 4 | 172 | 5.11 | 14.61 | 1.06 | 6.61 |
| layout A, warmup + jump | `e8-a-jump` | 3 | 43 | 10.42 | 57.30 | 0.92 | 8.53 |

## E10 Tenant isolation and the order in the band (M4, 100%)

| Arm | Run | Interactive ok | TTFT p50 (s) | TTFT p95 (s) | Interactive sheds | Batch ok |
|---|---|---|---|---|---|---|
| FCFS | `e10-fcfs` | 1,730 of 1,786 | 3.29 | 10.04 | 55 429 tenant_tokens, 1 400 prompt_injection | 284 of 321 |
| EDF | `e10-edf` | 1,968 of 2,059 | 10.92 | 18.57 | 53 429 tenant_tokens, 38 503 timeout_queue | 96 of 150 |

## E11 Stale metrics (M3, layout A, 100%)

Pod B (`vllm-decode`) has the stale-metrics proxy. Its share is its part of all prompt tokens.

| Mode | Run | Pod B share of the prompt tokens | Interactive ok | TTFT p50 (s) | TTFT p95 (s) |
|---|---|---|---|---|---|
| pass | `e11-pass` | 0.51 | 257 of 257 | 0.63 | 1.20 |
| frozen | `e11-frozen` | 0.80 | 257 of 257 | 0.64 | 1.37 |
| stall | `e11-stall` | 0.08 | 257 of 257 | 0.74 | 1.74 |

## E12 Client aborts (M4, 100%, 20% of the streams closed)

| Arm | Run | Interactive ok | TTFT p50 (s) | TTFT p95 (s) | Interactive sheds | Batch ok |
|---|---|---|---|---|---|---|
| abort | `e12-abort` | 1,623 of 1,671 | 3.04 | 6.22 | 2 400 prompt_injection | 322 of 352 |

## E13 The overflow gate (M4, 150%)

| Arm | Run | Interactive ok | TTFT p50 (s) | TTFT p95 (s) | Interactive sheds | Batch ok |
|---|---|---|---|---|---|---|
| gate | `e13-gate` | 1,924 of 2,052 | 15.00 | 22.29 | 124 503 timeout_queue, 4 400 prompt_injection | 27 of 122 |

## E14 Who goes first: a 27K retrieve against short agent steps

| Run (split on/off, retrieve class) | Retrieve class | Retrieve TTFT (s) | Agent step TTFT (s) | Agent step end (s) | First token to an agent step |
|---|---|---|---|---|---|
| `e14-on-int` | interactive | 3.66 | 0.25 | 0.66 | 5 of 5 rounds |
| `e14-off-int` | interactive | 3.00 | 1.26 | 3.53 | 5 of 5 rounds |
| `e14-on-batch` | batch | 3.66 | 0.24 | 0.66 | 5 of 5 rounds |
| `e14-off-batch` | batch | 3.02 | 1.01 | 3.48 | 5 of 5 rounds |

## E15 Engine flags (M4, 100%, layout C)

| Arm | Run | Interactive ok | TTFT p50 (s) | TTFT p95 (s) | Interactive sheds | Batch ok |
|---|---|---|---|---|---|---|
| base: decode seqs 24, prefill chunk 16384 | `e3-c-100` | 2,387 of 2,402 | 4.59 | 15.95 | 11 503 timeout_queue, 4 400 prompt_injection | 418 of 511 |
| decode seqs 16 | `e15-seqs-16` | 1,291 of 1,371 | 5.25 | 16.62 | 75 503 timeout_queue, 5 503 no_endpoints | 615 of 627 |
| decode seqs 32 | `e15-seqs-32` | 1,690 of 1,696 | 2.10 | 4.27 | 5 503 no_endpoints, 1 400 prompt_injection | 287 of 329 |
| prefill chunk 8192 | `e15-mnbt-8k` | 1,859 of 1,865 | 8.50 | 17.38 | 4 503 timeout_queue, 2 503 no_endpoints | 229 of 272 |

The E15 runs had fewer calls than the base run. The first 1.4 s of each run had the 503 `no_endpoints` calls of fault 30. The TTFT values are still comparable.

## E16 The LMCache server: K2 (on) against K0 (none), M2

| Arm | Run | Interactive ok | TTFT p50 (s) | TTFT p95 (s) | Interactive sheds | Batch ok |
|---|---|---|---|---|---|---|
| k2 | `e16-k2` | 754 of 754 | 0.44 | 1.34 | none | 214 of 214 |
| k2-restart | `e16-k2-restart` | 172 of 179 | 0.58 | 1.02 | 7 503 no_endpoints | 81 of 81 |
| k0 | `e16-k0` | 770 of 770 | 0.56 | 6.91 | none | 247 of 247 |
| k0-restart | `e16-k0-restart` | 164 of 175 | 3.11 | 8.83 | 11 503 no_endpoints | 81 of 81 |

## E17 The guard

| Turns | Attacks blocked | Benign blocked | Check p50 cold (s) | Check p95 cold (s) | Cached verdict p50 (ms) |
|---|---|---|---|---|---|
| 200 | 22 of 22 | 0 of 178 | 0.19 | 0.28 | 0.80 |

Page check (Prompt Guard 2 on 50 real pages, and on the same pages with an injected window):

| Threshold | False positive rate | Miss rate |
|---|---|---|
| 0.3 | 0.00 | 0.50 |
| 0.5 | 0.00 | 0.52 |
| 0.7 | 0.00 | 0.52 |
| 0.9 | 0.00 | 0.54 |

## E3 again on 8 x A100 80 GB (2026-10-01)

Both layouts have 32 decode sequences. The levels are scripts each second. On the A100, the levels of the H100 runs were too high: 0.45 gave a TTFT p50 of 16 s.

| Arm | Run | Interactive ok | TTFT p50 (s) | TTFT p95 (s) | Interactive sheds | Batch ok |
|---|---|---|---|---|---|---|
| C, 0.15 | `e3-c32-r015` | 414 of 417 | 4.29 | 7.96 | 2 503 timeout_queue, 1 400 prompt_injection | 154 of 154 |
| C, 0.30 | `e3-c32-r030` | 801 of 805 | 5.96 | 13.55 | 4 400 prompt_injection | 108 of 128 |
| C, 0.45 | `e3-c32-50` | 778 of 902 | 15.96 | 25.66 | 65 503 timeout_queue, 52 503 no_endpoints, 4 400 prompt_injection, 3 500 upstream_error | 28 of 64 |
| A, 0.15 | `e3-a32-r015` | 513 of 513 | 2.22 | 4.44 | none | 129 of 129 |
| A, 0.30 | `e3-a32-r030` | 1,073 of 1,075 | 3.12 | 6.97 | 2 400 prompt_injection | 269 of 269 |
| A, 0.45 | `e3-a32-r045` | 1,282 of 1,285 | 5.69 | 23.07 | 2 503 timeout_queue, 1 400 prompt_injection | 116 of 157 |

## E7 again: sessions that span the clear, no LMCache (2026-10-01)

Layout A with no KV connector (no LMCache). 24 sessions send a turn each 12 s for 300 s. At 150 s, the run clears the prefix cache of pod B.

| Run | Reset of the prefix cache | Sessions | Spanning the clear | Hit ratio before | Hit ratio, 60 s after | Eligible calls | Ghosts | Ghost ratio |
|---|---|---|---|---|---|---|---|---|
| `e7b-precise` | not recorded (the hit ratio fell) | 24 | 24 | 0.87 | 0.84 | 151 | 15 | 0.10 |
| `e7b-approx2` | success | 24 | 24 | 0.84 | 0.97 | 111 | 10 | 0.09 |
| `e7b-approx-reset-failed` | not recorded (no fall: most likely failed) | 24 | 24 | 0.90 | 0.97 | 136 | 0 | 0.00 |

vLLM v0.30.0 answers POST /reset_prefix_cache with HTTP 200 and `{"success": false}` when blocks are in use (fault 36). The run `e7b-approx2` asks with `reset_running_requests=true` and records the answer.

## E9 Scale: which pool (2026-10-01, one node)

At each time, only one pool was free to grow: `cluster/one.sh hold` kept the other pool at one pod. KEDA permitted 2 pods for each pool (overlay `one-e9`). The times after the planner request come from the Prometheus queries (step 5 s).

| Run | Pool | Planner asks for a second pod | KEDA makes it | Warmup starts | Warm label: the router can use it | Planner asks (max) | Pods (max) | New pod: running (max) | New pod: ramp weight (max) |
|---|---|---|---|---|---|---|---|---|---|
| `e9-decode` | decode | 04:13:00 UTC (21:13:00 PDT) | +15 s | +215 s | +235 s | 4 | 2 | 24 | 0.10 |
| `e9-prefill` | prefill | 04:43:52 UTC (21:43:52 PDT) | +15 s | +210 s | +225 s | 4 | 2 | 1 | 0.10 |

The load (`app/loadgen/capacity.py`, straight to the gateway):

| Run | Load | Calls ok | TTFT p50 (s) | TTFT p95 (s) | Failed calls |
|---|---|---|---|---|---|
| `e9-decode` | 48 streams: 1,000 prompt tokens, 4,000 output tokens | 168 of 192 | 5.93 | 125.02 | 3 x 504 at 300 s, 21 x 599 at 301 s |
| `e9-prefill` | 16 streams: 24,000 prompt tokens, 64 output tokens | 211 of 225 | 117.53 | 207.93 | 14 x 429 at 121 s |

- The planner asked for up to 4 pods. KEDA stopped at 2, the limit of the overlay.
- In `e9-prefill`, the planner rule first used the capacity of one H100 prefill pod: 10,500 tokens each second (E1). With this value, the planner asked for one prefill pod for about 16 minutes. At 04:42:27 UTC (21:42:27 PDT), we changed the value to 1,800. Under this load, the A100 prefill pod computed a median of 1,904 prompt tokens each second. 85 s after the change, the planner asked for 2 pods.
- The ramp of each new pod stayed at r10. The warm controller moves a ramp up only while the fleet TTFT p99 is at most 2 x the baseline (2.0 s on the A100). This load was far above it. At r10, the new decode pod still ran 24 sequences: the ramp is a score, not a cap (E8).
- In `e9-decode`, the route timeout (300 s) stopped 24 calls. 3 calls got 504, and 21 calls lost the stream (599 is the code of the load generator for a client error). In `e9-prefill`, the router queue evicted 14 calls after 120 s (429).

## The demo check on 8 x A100 80 GB (2026-10-01)

The reports (`metrics/demo/`). The image `fix3` has the fix for the empty answer (`app/agent/middleware.py`, `app/service.py`).

| Report | End | Questions | App image | Load | Note |
|---|---|---|---|---|---|
| `demo-check-20261002T020050Z` | 02:00:50 UTC (19:00:50 PDT) | all | `companion/app:dev` | idle | The full check. SIE loaded its models during the run. |
| `demo-check-20261002T020134Z` | 02:01:34 UTC (19:01:34 PDT) | D-07, D-08 | `companion/app:dev` | idle | The two questions with no answer, again. |
| `demo-check-20261002T022021Z` | 02:20:21 UTC (19:20:21 PDT) | D-07 | `companion/app:fix1` | `e3-c32-50` | Debug run. The image had the old code: an error in the copy step. |
| `demo-check-20261002T022451Z` | 02:24:51 UTC (19:24:51 PDT) | D-07 | `companion/app:fix2` | `e3-c32-50` | Debug run. The image had the old code again, and the router shed the call (503 timeout_queue). |
| `demo-check-20261002T023006Z` | 02:30:06 UTC (19:30:06 PDT) | D-07 | `companion/app:fix3` | `e3-c32-100` | Debug run. The image has the fix, but the router shed the call (503 timeout_queue). |
| `demo-check-20261002T050815Z` | 05:08:15 UTC (22:08:15 PDT) | D-03, D-07, D-08, D-09 | `companion/app:fix3` | idle | The four failed questions, after E9. |

| Report | Question | Result | Rule ok | Time ok | First token (s) | Total (s) | Errors | Claims (status, reason) |
|---|---|---|---|---|---|---|---|---|
| `demo-check-20261002T020050Z` | D-01 | pass | yes | yes | 2.38 | 10.45 | - | - |
| `demo-check-20261002T020050Z` | D-02 | fail | yes | no | 7.12 | 17.17 | - | - |
| `demo-check-20261002T020050Z` | D-03 | fail | no | yes | 8.13 | 58.94 | - | C3 verified (supported), C1 verified (supported), C2 not_verified (timeout) |
| `demo-check-20261002T020050Z` | D-04 | pass | yes | yes | 7.40 | 52.90 | - | C1 verified (supported), C3 verified (supported), C2 not_verified (timeout) |
| `demo-check-20261002T020050Z` | D-05 | pass | yes | yes | 6.95 | 35.83 | - | C2 not_verified (no_evidence), C1 verified (supported) |
| `demo-check-20261002T020050Z` | D-06 | pass | yes | yes | 9.12 | 55.71 | - | C2 verified (supported), C1 verified (supported), C3 not_verified (timeout) |
| `demo-check-20261002T020050Z` | D-07 | fail | no | yes | - | 16.28 | - | - |
| `demo-check-20261002T020050Z` | D-08 | fail | no | yes | - | 17.11 | - | - |
| `demo-check-20261002T020050Z` | D-09 | fail | no | yes | 13.81 | 53.42 | - | C1 not_verified (no_evidence), C2 not_verified (timeout) |
| `demo-check-20261002T020050Z` | D-10 | fail | no | yes | 7.64 | 48.91 | - | C1 verified (supported), C2 not_verified (no_evidence) |
| `demo-check-20261002T020050Z` | D-11 | pass | yes | yes | - | 0.26 | - | - |
| `demo-check-20261002T020050Z` | D-12 | pass | yes | yes | - | 4.61 | - | - |
| `demo-check-20261002T020134Z` | D-07 | fail | no | yes | - | 8.84 | - | - |
| `demo-check-20261002T020134Z` | D-08 | pass | yes | yes | 8.27 | 11.46 | - | - |
| `demo-check-20261002T022021Z` | D-07 | fail | no | yes | - | 42.20 | - | - |
| `demo-check-20261002T022451Z` | D-07 | fail | no | yes | - | 10.68 | 503 timeout_queue | - |
| `demo-check-20261002T023006Z` | D-07 | fail | no | yes | - | 10.40 | 503 timeout_queue | - |
| `demo-check-20261002T050815Z` | D-03 | fail | no | yes | - | 5.52 | 500 upstream_error | - |
| `demo-check-20261002T050815Z` | D-07 | fail | no | yes | 18.44 | 56.83 | - | C2 not_verified (no_evidence), C1 not_verified (no_evidence), C3 not_verified (timeout) |
| `demo-check-20261002T050815Z` | D-08 | pass | yes | yes | 15.11 | 17.18 | - | - |
| `demo-check-20261002T050815Z` | D-09 | fail | no | yes | 13.79 | 58.25 | - | C1 not_verified (no_evidence), C2 not_verified (timeout) |
