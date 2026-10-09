# Spend ledger

Record each block of paid GPU or API use. Update the totals after each block.

Approvals (the owner):

- 2026-09-27, about 18:40 PDT, in advance: node 2 (2 x A6000, 2.18 USD/h) for Gate G0.
- The same approval: node 1 (2 x H100 SXM, 8.38 USD/h) for the engine-only Gate G1.
- 2026-09-27, evening: the experiment sessions 1 and 2, with no GPU overnight. E9 on 4 x H100 (about 47 USD) too.
- 2026-09-28, about 16:10 PDT: the day plan. Monday G0, Tuesday session 1, Wednesday session 2, Thursday E9 and reruns.
- 2026-09-29, about 09:42 PDT: no 2 x A6000 had stock. Node 2 can be 1 x H100 80 GB (4.29 USD/h) on 2026-09-29 only.
- 2026-09-29, about 10:56 PDT: session 2 runs on the same day, after session 1.
- 2026-09-29, about 11:06 PDT: this node 2 block may run 11 hours (until 20:44 PDT), for the teardown.
- 2026-09-29, about 17:50 PDT: this node 2 block may run until 22:30 PDT (12.77 hours). The 23:00 stop does not change.
- 2026-09-29, about 21:50 PDT: node 2 on 1 x H100 is also approved for 30 Sep and 1 Oct. E9 (4 x H100) may also run on 30 Sep.
- 2026-09-29, about 22:00 PDT: the limits are 275 USD in total and 17 hours of 2 x H100, for the Wednesday work and E9.
- 2026-09-30, 16:15 PDT (the question was at 13:07 PDT): take 4 x H100 before node 2, and wait at most 45 minutes for node 2. The 4 x H100 limit is 6 hours.
- This exception was for one launch. We used it at 17:04 PDT.
- 2026-10-01, about 18:23 PDT: "take it now". One node of 8 x A100 80 GB (22.32 USD/h) runs all pods, because no H100 shape had stock in the evening. The limit is 4.5 hours, and the total stays at 275 USD.

The limits now:

- No GPU overnight: launches only from 07:00 to 22:00 PDT, and every GPU stops by 23:00 PDT.
- Node 1 launches only when node 2 is active.
- Node 1 (2 x H100 SXM): at most 17 hours in total (15 until 29 Sep, 22:00 PDT). Node 1 (4 x H100 SXM, E9): at most 3 hours.
- Node 2 (2 x A6000, or 1 x H100 on 2026-09-29): at most 10 hours for each block and 30 hours in total.
- All of it: at most 275 USD (225 until 29 Sep, 22:00 PDT). No Lambda filesystem (the owner, 2026-09-28).

The spend guard (`cluster/lambda/spend_guard.py`) enforces these limits. Its log is `metrics/spend-guard.jsonl`.

| Date | Provider | Shape or service | Start (UTC) | End (UTC) | Hours | USD | Purpose | Terminated |
|---|---|---|---|---|---|---|---|---|
| 2026-09-27 PDT | Lambda | gpu_2x_h100_sxm5, us-southeast-1 (instance 8946a74e) | 2026-09-28 02:46 | 2026-09-28 03:08 | 0.37 | 3.07 | Gate G1, engine only. Stopped early: the owner asked for the Grafana screenshot plan first. | yes, by Claude |
| 2026-09-28 PDT | Lambda | gpu_2x_a6000, us-south-2 (instance f4143f81) | 2026-09-29 00:19 | 2026-09-29 02:14 | 1.92 | 4.17 | Gate G0 passed: platform, ingest 91%, dev P/D, Grafana images, E17 | yes, by Claude |
| 2026-09-29 PDT | Lambda | gpu_1x_h100_sxm5, us-southeast-1 (instance f420b3a8), node 2 | 2026-09-29 16:44 | 2026-09-30 04:08 | 11.40 | 48.91 | Sessions 1 and 2: control, data, guard, Grafana images | yes, by Claude |
| 2026-09-29 PDT | Lambda | gpu_2x_h100_sxm5, us-southeast-1 (instance 2432f46c), node 1 | 2026-09-29 16:47 | 2026-09-30 02:38 | 9.85 | 82.56 | Sessions 1 and 2: G1, E1 to E8, E10 to E16, the demo | yes, by Claude |
| 2026-09-30 PDT | Lambda | gpu_4x_h100_sxm5, us-southeast-1 (instance e7bf6ad5), node 1 | 2026-10-01 00:04 | 2026-10-01 00:49 | 0.75 | 12.20 | Taken for E9 before node 2 (the owner's exception). No node 2 stock in 45 min, so terminated as agreed. | yes, by Claude |
| 2026-10-01 PDT | Lambda | gpu_8x_a100_80gb_sxm4, us-east-1 (instance 00e91367), one node | 2026-10-02 01:23 | 2026-10-02 05:16 | 3.88 | 86.51 | The three must-do items (demo check, E3 with 32 decode sequences, E7 again) and E9 on the one-node layout. No H100 shape had stock in the evening. | yes, by Claude |

Totals:

| Provider | Budget (USD) | Spent (USD) | Left (USD) |
|---|---|---|---|
| Lambda | 400 | 237.43 | 162.57 |
| Superlinked | 500 | 0 | 500 |
| Modal | not known | 0 | not known |
| Brave Search | 10 | 0 | 10 |
