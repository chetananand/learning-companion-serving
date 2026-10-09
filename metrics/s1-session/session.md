# Session 1 (2026-09-29): the log

Times are UTC (PDT = UTC - 7).

- 16:38 UTC (09:38 PDT): the owner: "start". Stock: 2 x H100 in us-southeast-1 only. No 2 x A6000 in any region.
- About 16:42 UTC: the owner approved node 2 on 1 x H100 80 GB (node key `node2-h100`), for this day only.
- 16:44 UTC: node 2 launched: gpu_1x_h100_sxm5, us-southeast-1, 4.29 USD/h (instance f420b3a8).
- 16:47 UTC: node 2 active. Node 1 launched at once, to hold the 2 x H100 stock: gpu_2x_h100_sxm5, us-southeast-1,
  8.38 USD/h (instance 2432f46c).
- 16:48 to 16:53 UTC: k3s server Ready, node 1 joined, five images on node 2, the edge image on node 1.
  I pulled the model (32 GB) and the vLLM image on node 1 before the install.
- 16:54 UTC: install t2 passed. The Qdrant snapshot restored: 4,501 points.
- FAULT 23: the four HAMi GPU pods stayed Pending ("Insufficient nvidia.com/gpumem" from the default scheduler).
  The Deployments made them before the HAMi webhook ran, so they kept the default scheduler. Fix: recreate them. The install
  now waits for `deploy/hami-scheduler` before our manifests.
- 16:57 UTC: the engine pods are ready. GPU KV cache: 173,657 tokens on each pod (38.35 GiB). E1 uses this number.
- 17:00 UTC: all node 2 GPU pods are ready. SIE OCR runs with 20,000 MiB and 32 GiB (G0: it died with 10,000 MiB
  and 16 GiB).
- 17:01 UTC: smoke 1: the full path gave 503 `guard_unavailable`. FINDING: the first call to a cold safety model
  took 0.99 s, and the edge waits 1.0 s. Smoke 2 (the model is warm): 12 of 12 PASS.
- 17:02 UTC: engine report: `metrics/t2-20260929T170159Z`.
- 17:03 UTC: G1 tests (B11) started on node 1.
- 17:08 UTC: G1 tests. Tool calls: pass (97.5%). TTFT: pass (median 0.90 s). ITL: pass (p50 18.2 ms, p95 20.5 ms).
  Prefix: a false fail. The 4 "bad" answers were good, and they quoted "NaN" from our own notes. Fixed in the test.
  Split and CPU tier: FAIL. The decode pod loaded 0 tokens from LMCache.
- FINDING (the hop race): the prefill engine answers before LMCache has stored the KV. The sidecar calls the decode
  pod at once, and the decode lookup misses, so the decode pod computes the prompt again. Direct test, 8,500
  tokens: no gap gave 0 hits (0.88 s), and a gap of 0.2 s gave 8,448 hits (0.37 s). During the capture load,
  gaps of 10 and 20 ms gave 0 hits in 10 tries. 50 ms gave 1 hit in 5, and 100 ms gave 5 hits in 5.
  FIX: `control/barrier/proxy.py`, a store barrier in the prefill pod. It holds the prefill leg for at least
  0.1 s, and then until the LMCache store counters show no store in progress (0.5 s at most).
- FINDING (the CPU tier size): LMCache keeps the sliding-window layers of Gemma 4 in full. It stored 1.42M tokens
  as 1.23 TB, which is about 865 KB for each token. The GPU cache needs about 237 KB for each token. The 150 GiB
  tier evicts at 80%, so it held about 149K tokens, fewer than the GPU cache (173,657). A prompt that left the GPU
  cache was also gone from the tier (the G1 CPU-tier test). FIX: 250 GiB with eviction at 90% (about 280K tokens).
- 17:24 UTC: FAULT 24: the G1 restart gave a Pending prefill pod. The HAMi webhook sent the new node 1 pod to the
  HAMi scheduler, and HAMi does not manage node 1. FIX: the label `hami.io/webhook: ignore` on node 1 GPU pods.
- 17:27 UTC: G1 restart: PASS. After the restart, the prefill pod loaded 7,680 tokens from LMCache (TTFT 0.20 s,
  cold 0.64 s).
- 17:28 UTC: capture S1-1 started. cap-b2: 19 of 20 turns ok. cap-b1: 187 of 200 turns ok (93.5%).
- FAULT 25: Envoy gave 413 for 10 of 660 capture calls (prompts of about 12K tokens). The AI Gateway buffers the
  whole body, and the Envoy default buffer is 32 KiB. FIX: a ClientTrafficPolicy with a 4 MiB buffer. After the
  fix, a prompt of 16,320 tokens (52 KB) passed. E1 needs this (8K and 24K tokens).
- The guard failed closed on 3 of 660 capture calls (`guard_unavailable` after 111 to 144 ms). This is 0.45%.
- 17:40 UTC: capture results. cap-b3: 26 of 50 turns ok (52%). The sweep failed. frozen.jsonl: 1,148 lines.
  The Grafana check on cap-b1: 47 of 56 panels have data. The 9 empty panels are for paths that did not run.
- FAULT 26: Prompt Guard gave HTTP 500 under concurrent page checks ("RuntimeError: Already borrowed", 35 times in
  30 minutes). FastAPI ran the sync handler in a thread pool, and the fast tokenizer fails when two threads use it.
  The page checks failed closed, so the verified turns and the sweep failed. FIX: one lock around the model work.
- 17:44 UTC: the new engine base is applied: the store barrier, the 250 GiB tier, and the HAMi skip labels.
- 17:47 UTC: G1 split: PASS, 3 of 3. The decode pod loaded 3,840 tokens from LMCache (TTFT 0.55 s).
  The barrier: 5 holds, 0 timeouts.
- 17:48 UTC: G1 CPU tier. A flood of 28 prompts (224K tokens) filled the tier (about 895 KB for each token). The
  eviction loop removed about 104K tokens in large batches, and the test prompt too. A flood of 23 prompts (184K
  tokens) is more than the GPU cache and less than the tier: PASS, 7,168 tokens from the tier, TTFT 0.19 s
  (cold 0.59 s, GPU cache 0.06 s). The test now needs real tier hits for a pass.
- 17:49 UTC: G1 report `metrics/g1-solo-20260929T174911Z`. All 7 G1 tests pass (prefix: re-judged, see the note).
- 17:50 UTC: capture top-up started: b3 again, and the sweep.
- 17:55 UTC: top-up: cap-b3-2 had 49 of 50 turns ok (98%), so the Prompt Guard fix works. The sweep is Complete.
  After a second freeze, frozen.jsonl has 1,775 lines. All S1-1 pass rules now pass.
  Grafana check on cap-b3-2: 49 of 56 panels have data (the verdict and removed-window panels now too).
- 17:56 UTC: the owner approved session 2 on the same day, after session 1.
- 17:57 UTC: session 1 experiments started (`cluster/sessions/s1-experiments.sh`).
- 17:57 to 18:02 UTC: FAULT 27: E1 (8K and 24K) got 599 ConnectError or ConnectTimeout for every call. The web-egress
  policy of the load generator did not allow the gateway namespace. My test from the node itself passed, because
  pod policies do not apply there. FIX: a policy that opens the gateway for app=loadgen only. The app pods still
  reach the model only through edge. A pod with the loadgen labels then got HTTP 200 at the first try.
  The failed runs are kept as `metrics/e1-*-blocked-by-policy`. `loadgen.sh` now deletes an old Job of the same run
  id before a rerun.
- RISK FOUND: an LMCache key has no KV dtype. `variant.sh` now restarts the LMCache server when the KV format
  changes (fp8kv, and the variant after it), so the tier starts empty.
- E2 knee: the rule now counts only capacity sheds. Guard blocks and 4xx rejects in the capture could give a false
  knee.
- 18:03 UTC: session 1 experiments restarted from E1.
- 18:03 to 18:21 UTC: E1 (after fault 27). 8K tokens: all 261 calls ok at levels 1 to 32. TTFT p50 0.98 s at 1,
  12.9 s at 32. 24K tokens: all ok up to level 16 (TTFT p50 20.9 s). Level 24: 41 of 48 ok, 7 streams closed.
- FAULT 29: with no route timeout, the AI Gateway stops a whole request at 60 s. Envoy logged the 7 streams as 200
  with 60.7 to 66.1 s, and the client saw RemoteProtocolError. FIX: `timeouts.request: 300s` on the route (the
  replay client waits 300 s). Applied at 18:43 UTC, at the start of E3 at 50% load, after E2.
- 18:21 to 18:42 UTC: E2 soak. The first capacity shed (503 timeout_queue) came in minute 18. RATE100 = 0.9
  scripts each second (R50 0.45, R70 0.63, R150 1.35).
- 18:43 UTC: E3 arm C started.
- 18:43 to 19:17 UTC: E3 arm C (P/D, the LMCache hop with the store barrier).
  50% (0.45): interactive 1,316 of 1,318 ok (2 guard blocks), TTFT p50 1.19 s, p95 2.04 s. Batch 400 of 400 ok.
  100% (0.9): interactive 2,387 of 2,402 ok (11 timeout_queue), TTFT p50 4.59 s, p95 15.95 s. Batch 418 of 511 ok
  (89 timeout_queue). The flow control sheds batch first.
  150% (1.35): interactive 2,639 of 2,917 ok (273 timeout_queue), TTFT p50 15.78 s, p95 21.45 s. Batch 45 of 195 ok.
- 19:17 UTC: E5 (decode chunk 2560) started.
- 19:17 to 20:05 UTC: E5 (split probe, chunk 2560 and 4096), E6 on, E15 seqs-16 and seqs-32. Results are in the runs.
- FAULT 30: each run after a variant switch began with 5 calls of 503 no_endpoints, all in the first 1.4 s. The new
  router pod did not know the engine pods yet. FIX: `variant.sh` waits until the full path answers 3 small calls
  in a row. The runs e15-seqs-16, e15-seqs-32, and e15-mnbt-8k have these 5 calls. The analysis skips the first minute.
- 20:05 to 21:22 UTC: E15 mnbt-8k, E6 off and fp8, E3 arm A. The KV format rule emptied the tier at the fp8 change
  and again after it. fp8 KV: 345,235 tokens on each pod (bf16: 173,657).

## Session 1 results (interactive TTFT, p50 and p95)

| Run | Load | Result |
|---|---|---|
| E3 arm C (P/D, the LMCache hop) | 50% | 1.19 s, 2.04 s. All ok except 2 guard blocks. |
| E3 arm A (two whole pods, no split) | 50% | 0.65 s, 1.05 s |
| E3 arm C | 100% | 4.59 s, 15.95 s. 11 interactive and 89 batch calls shed. |
| E3 arm A | 100% | 0.84 s, 1.57 s. No shed. |
| E3 arm C | 150% | 15.78 s, 21.45 s. 90% of interactive calls ok. |
| E3 arm A | 150% | 11.02 s, 21.10 s. 97% of interactive calls ok. |
| E6 on / off / fp8 KV (M2, 100%) | 100% | 0.43 s / 0.60 s / 0.23 s (p95: 1.28 / 1.29 / 1.77 s) |
| E15 decode seqs 16 / 24 (base) / 32 | 100% | 5.25 / 4.59 / 2.10 s (p95: 16.6 / 15.95 / 4.27 s) |
| E15 prefill chunk 8192 (base 16384) | 100% | 8.50 s, 17.4 s |

FINDING (corrected at 21:53 UTC from the Grafana vLLM dashboard of e3-c-100): the decode pod was the bottleneck,
not the prefill pod. At 100% load, the decode pod computed most prompt tokens (peak 16.2K tokens each second,
against 4.55K on the prefill pod). Its KV use went up to 92%, and its ITL p95 was about 200 ms. Most agent calls
have a long cached prefix, so fewer than 2,048 tokens are new, and the router does not split them. So the prefill
pod was often idle. Layout A spreads the prefill and the decode work over both GPUs, and it wins at each load.
The split helps one thing: the stall of the other decode streams. E5, 16 streams: a split cut the p95 ITL of the
other streams from 0.244 s to 0.072 s at 8K tokens.
