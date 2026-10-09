# Session 2 (2026-09-29, after session 1): the log

Times are UTC (PDT = UTC - 7).

- 21:59 UTC: part 1 started. E4 LMCache hop: TTFT median 0.52 s (shared prefix) and 0.78 s (unshared). The decode
  pod loaded 5,376 to 9,472 tokens from the tier. 22:05 UTC: paused for the demo recording (the owner).
- Demo fixes during the pause: the live-fetch rule (D-07, D-09), the D-12 test bookmark (ingested again), no
  overflow switch in the UI, and SLO-4 relaxed to 60 s (the owner, option B). At 30 s, about 12 of 17 checks timed out.
- 22:22 UTC: resumed. E4 NIXL hop: TTFT about 4.0 to 4.2 s, with 1.4 to 1.6 GB for each transfer (TCP between the pods).
  FAULT (record): run_record failed on an empty env value (the barrier off). It is fixed, and the E4 NIXL record was rebuilt.
- E10: FCFS interactive TTFT p50 3.29 s, EDF 10.92 s. The noisy tenant got about 55 tenant_tokens rejects in both.
- E12: 46 client aborts, interactive TTFT p50 3.04 s.
- FAULT 31: the E14 tool counted no TTFT (the agent steps answer with a tool call first) and sent 30,965 tokens
  (the edge limit is 30,000). FAULT 32: the long E14 message failed the 8K-context guard at edge. E14 now sends
  to the gateway, like E1 and E9. E14: with the split, agent steps TTFT 0.25 s. With no split, 1.26 s.
- E8: the one decode pod was down for about 4 minutes. 154 to 155 interactive calls got 503 no_endpoints.
  The new pod got the default scheduler (the fault 24 fix works under load).
- E13: 124 interactive timeout_queue sheds, and the gate marked exactly those 124 as would_leave. Batch stays.
- E16: after the decode restart, K2 TTFT p50 0.58 s, and K0 3.11 s (K0 also has the slow NIXL hop).
- E7 LIMIT: the ghost count needs sessions that span the cache clear. No session did (74 before, 70 after, 0 in both),
  and with one decode pod the router has no choice of pod. The count is 0 by design. An offline count by prefix
  type replaces it, with no GPU time.
- E7 RESULT (offline, `ghosts-by-threshold.json` in each E7 run): the cache clear has almost no effect. The token
  hit ratio was 0.762 before the clear, 0.789 in the first 60 s after it, and 0.813 later (precise index). Approx
  index: 0.884, 0.851, and 0.871. The clear empties only the GPU prefix cache. The LMCache tier keeps the prefixes,
  and the decode pod loads them again at once. So the CPU tier masks the ghost effect that E7 looks for.
- E8 in layout A (two whole pods): during the outage, only 3 calls got no_endpoints (P/D with one decode pod: 155).
  Ramp: TTFT p50 1.84 s, p95 10.35 s. Jump: p50 1.02 s, p95 13.79 s. The cold pod with full traffic gives a worse tail.
- E11 (layout A, the stale proxy in pod B, 257 calls in each mode, all ok): TTFT p50 and p95. Pass: 0.63 s and
  1.20 s. Frozen: 0.64 s and 1.37 s. Stall: 0.74 s and 1.74 s.
- 01:26 UTC: session 2 experiments done. The E1 24K rerun (after fault 29) and demo-check follow.
- 01:36 to 01:41 UTC: E1 24K rerun: 48 of 48 at level 24 (p95 TTFT 53.7 s, the tail that the 60 s limit hid).
  demo-check: 8 of 12 pass with SLO-4 = 60 s (D-03, D-07, D-08, D-09 fail: agent choices, see
  `metrics/demo/demo-check-20260930T014113Z-rejudged.md`). The owner recorded the 8 verified questions again.
- 02:21 UTC: teardown A: the engine report and the backup (Qdrant snapshot 67.5 MB, capture files).
- 02:38 UTC: node 1 terminated (9.85 h, 82.56 USD).
- FAULT (backup): kubectl cp needs tar in the Prometheus container, and it has none. The snapshot (735 MB) was
  copied from the pod's emptyDir with rsync. `g0.sh backup` now does that.
- FAULT 33: two gateway panels were always empty (a wrong stage filter, and an Envoy counter that we do not
  scrape). Fixed in `tools/dashboards.py`. The gateway image was rendered again for all 46 runs of the day.
- 03:12 UTC: the session 2 images: 25 runs, 225 images, 0 errors. Final check: 44 to 53 of 56 panels have data.
- 04:08 UTC: node 2 terminated (11.40 h, 48.91 USD). 0 instances. The spend guard, the tunnel, and caffeinate
  stopped. Total spend: 138.72 USD of 400 (limit 225), and 10.22 of 15 H100 hours.
- 2026-09-30, offline: FAULT 34: the warm controller labels its series with the engine pod (`pod`). The scrape renamed that label to `exported_pod`, so the dumps and the Grafana ramp panel merged all engine pods. The E8 ramp and warmup evidence now comes from the Envoy access log (`notebook/proof.py`, `new_pod_window`). FIX: `honorLabels: true` on the warm-controller ServiceMonitor.
