# Runbook: the experiment sessions (E1 to E17)

This runbook gives the exact commands for the experiments of `docs/spec/06-experiments.md`. Each load run saves its data in `metrics/<run-id>/`:

- `client.jsonl` and `summary.json` (the client side)
- `run.json` (the run record, with the variant)
- `prom/*.json` (the Prometheus range queries)
- `edge-access.jsonl`, `envoy-access.log`, `hops.jsonl`, and `kv-events.json`

## Approvals and cost

The owner approved a day plan on 2026-09-28: Monday G0, Tuesday session 1, Wednesday session 2, and Thursday E9 and reruns. The launch tool and the spend guard enforce it:

- Launches only from 07:00 to 22:00 PDT. Every GPU stops by 23:00 PDT.
- Node 1 launches only when node 2 is active.
- At most 17 hours of 2 x H100, 6 hours of 4 x H100 (3 until 2026-09-30, 16:15 PDT), and 275 USD in total. These limits apply from 2026-09-29, 22:00 PDT. Before that, the limits were 15 hours and 225 USD. Node 2: at most 10 hours for each block.
- 2026-09-29: no 2 x A6000 had stock. The owner approved node 2 on 1 x H100 80 GB (node key `node2-h100`, 4.29 USD/h) for that day only. The node 2 GPU pods need 52 GB, so one H100 holds them. Later, the owner approved `node2-h100` for 2026-09-30 and 2026-10-01 too.
- 2026-10-01: the one-node layout (node key `one-t4`), if no node 2 shape has stock. See the section "One node".

| Block | Nodes | Hours | Cost (estimate) |
|---|---|---|---|
| Session 1: E1, E2, E3, E5, E6, E15 (and G1, if it did not run before) | node 1 (2 x H100) and node 2 | node 1: 5.5, node 2: 7 | 46 + 15 = 61 USD |
| Session 2: E4, E7, E8, E10 to E14, E16 | node 1 (2 x H100) and node 2 | node 1: 5, node 2: 6.5 | 42 + 14 = 56 USD |
| E17 | node 2 only, in the bring-up of a session | 0.25 | in the session |
| E9 (recorded offline) | node 1 (4 x H100) and node 2 | 2.5 | about 47 USD (check the price with `make plan`) |
| One node, 2026-10-01: the three must-do items and E9 | one node (4 x H100) | 4 | about 65 USD |

## Rules

1. Launch node 2 first. Launch node 1 only when node 2 is ready and has its data. The H100 must not wait for node 2.
2. Use one region with stock for both shapes. k3s uses the private network. `lambda_ctl.py` shows the regions with both shapes.
3. Node 2 must stop by 23:00 PDT, and the tool refuses a node 2 launch after 20:00 PDT. Thus launch node 2 by 15:30 PDT for session 1, and by 16:00 PDT for session 2.
4. Apply each variant with `cluster/variant.sh`. Do not edit YAML while the GPUs run. Each variant sets the router, the engine, and the warm controller from the base files. Thus no variant keeps a setting of an earlier variant.
5. Each command runs in a new shell. Start each command with `source cluster/state/session.env`. This file sets `KUBECONFIG`, `CAP`, and the load levels `R50`, `R70`, `R100`, and `R150`.
6. If a step fails, record the failure and go to the next step. Do not stay on one step for more than its time budget.
7. After each step, write the time and the result in `metrics/session-<n>.md`.
8. Before each terminate step, run `bash cluster/g0.sh backup`. Terminate node 1 first. Record each launch and each terminate step in `docs/budget-ledger.md`.

## Bring-up (each session)

| Step | Command | Pass rule | Time | If it fails |
|---|---|---|---|---|
| B1. Launch node 2 | `printf 'gpu_2x_a6000\n' \| python3 cluster/lambda/lambda_ctl.py launch --node node2 --region <region> --approve` | The instance is active. | 10 min | Stop. A failed launch costs nothing. No 2 x A6000 stock: `--node node2-h100` with `gpu_1x_h100_sxm5`, only on an approved day. |
| B2. Node 2 | `bash cluster/g0.sh ips`, then `bootstrap`, `kubeconfig`, and `tunnel` | The k3s node is Ready. | 15 min | Terminate. |
| B3. Images and secrets | `bash cluster/g0.sh images` and, at the same time, `bash cluster/g0.sh secrets` | Five images are in k3s. | 30 min | Fix the Dockerfile and build again. |
| B4. Platform | `bash cluster/g0.sh install t2` | `edge` is ready. The engine pods wait for node 1. | 30 min | Use the chart fallbacks of `g0.md`, step 4. |
| B5. Data | `bash cluster/g0.sh restore` | The collection has its points. Session 2: the capture files are back. | 10 min | No snapshot: `bash cluster/g0.sh ingest` (30 min). |
| B6. Session file | `printf 'export KUBECONFIG=%s\nexport CAP=/data/capture/frozen.jsonl\n' "$PWD/cluster/state/kubeconfig" > cluster/state/session.env` (session 1 only) | The file exists. | 1 min | - |
| B7. E17 | the E17 commands below | `metrics/e17/*.json` exist. | 15 min | Record the error. E17 needs no H100. |
| B8. Launch node 1 | `printf 'gpu_2x_h100_sxm5\n' \| python3 cluster/lambda/lambda_ctl.py launch --node node1-t2 --region <the region of node 2> --approve` | The instance is active. | 10 min | Stop. Node 2 alone costs 2.18 USD/h. |
| B9. Join | `bash cluster/t2.sh ips`, then `bash cluster/t2.sh join`, then `bash cluster/t2.sh images` | Node 1 is Ready. | 15 min | "cannot reach": action A9 (firewall). |
| B10. Engine | `bash cluster/t2.sh wait`, `bash cluster/t2.sh smoke`, `bash cluster/t2.sh report` | Smoke passes. The logs give the KV cache size (E1). | 30 min | `OVERLAY=t2-k0` or `OVERLAY=t2-vllm026` (G1 decision rules). |
| B11. G1 (only if G1 did not run before) | `KUBECONFIG_G1=cluster/state/kubeconfig bash cluster/g1-solo.sh tests`, then `restart`, then `report` | The G1 pass rules of `g1-solo.md` | 40 min | Use the G1 decision rules. |

## Session 1 (after the bring-up, layout C)

### S1-1. Capture (first session only, 40 minutes)

```bash
source cluster/state/session.env
bash cluster/loadgen.sh cap-sets questions build --out /data/prompts
bash cluster/loadgen.sh cap-b2 questions drive --sets /data/prompts/b2.jsonl --concurrency 4
make sweep
bash cluster/loadgen.sh cap-b1 questions drive --sets /data/prompts/b1.jsonl --concurrency 6
bash cluster/loadgen.sh cap-b3 questions drive --sets /data/prompts/b3.jsonl --concurrency 4
kubectl -n companion get jobs
bash cluster/g0.sh freeze
python3 tools/grafana_shots.py check --runs metrics/cap-b1
```

The last command checks the Grafana panels on real traffic of the H100 model. A panel with no data here is a problem to fix now, before the experiments.

Pass rule: 90% or more of the turns are "ok" in each `job.log`. The sweep Job is Complete. `frozen.jsonl` has 1,500 lines or more. A driver Job with some failed turns shows FAILED. That is not a stop: the capture keeps all the calls.

### S1-2 to S1-10

| Step | Exp. | Variant | Command (after `source cluster/state/session.env`) | Evidence | Time |
|---|---|---|---|---|---|
| S1-2 | E1 | `baseline` | `bash cluster/loadgen.sh e1-8k capacity --target http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1 --tokens 8000 --levels 1,2,4,8,16,24,32`, then the same as `e1-24k` with `--tokens 24000 --levels 1,2,4,8,12,16,24 --repeats 2`. E1 skips edge (note 7). | Preemptions by level, the KV cache size from B10 | 30 min |
| S1-3 | E2 | `baseline` | `bash cluster/loadgen.sh e2-soak replay --capture $CAP --mix soak --rate 0.05 --ramp 0.05 --duration 1200`, then `uv run python -m notebook.proof knee e2-soak --env >> cluster/state/session.env` | The soak plot and RATE100 | 25 min |
| S1-4 | E3 arm C | `baseline` | `bash cluster/loadgen.sh e3-c-50 replay --capture $CAP --mix m4 --rate $R50 --duration 600`, then the same for `e3-c-100` with `$R100` and `e3-c-150` with `$R150` | Goodput, TTFT p95 and p99, ITL p95 | 35 min |
| S1-5 | E5 | `baseline` (decode chunk 2560) | `bash cluster/loadgen.sh e5-d2560 tool -m tools.pd_probe split --decode {decode} --prefill {prefill} --capture $CAP` | `split.json` | 8 min |
| S1-6 | E6 on | `baseline` | `bash cluster/loadgen.sh e6-on replay --capture $CAP --mix m2 --rate $R100 --duration 420` | KV use, hit ratio, prefill tokens | 9 min |
| S1-7 | E5 | `e5-d4096` | `bash cluster/variant.sh e5-d4096 --arg vllm-decode:--max-num-batched-tokens=4096`, then S1-5 with the run id `e5-d4096` | `split.json` | 13 min |
| S1-8 | E15 | `seqs-16`, `seqs-32`, `mnbt-8k` | For each variant: `bash cluster/variant.sh <variant> <option>`, then `bash cluster/loadgen.sh e15-<variant> replay --capture $CAP --mix m4 --rate $R100 --duration 420`. The base arm is `e3-c-100`. | ITL p95, TTFT p95, preemptions | 40 min |
| S1-9 | E6 off, E6 fp8 | `noprefix`, `fp8kv` | For each variant: `bash cluster/variant.sh <variant> <options>`, then `bash cluster/loadgen.sh e6-<variant> replay --capture $CAP --mix m2 --rate $R100 --duration 420` | KV use, hit ratio, prefill tokens | 30 min |
| S1-10 | E3 arm A | `layout-a` | `bash cluster/variant.sh layout-a --router pd_mode=off --preset layout-a`, then `e3-a-50`, `e3-a-100`, and `e3-a-150` as in S1-4 | Goodput, TTFT p95 and p99, ITL p95 | 40 min |
| S1-11 | End | - | the teardown below | - | 10 min |

## Session 2 (after the bring-up, layout C)

| Step | Exp. | Variant | Command (after `source cluster/state/session.env`) | Evidence | Time |
|---|---|---|---|---|---|
| S2-1 | Demo | `baseline` | `make demo-check`. The owner records the screen of the UI (`http://127.0.0.1:8501`) for D-01 to D-12. | The acceptance record, the video | 15 min |
| S2-2 | E4 | `baseline`, then `nixl-hop` | `bash cluster/loadgen.sh e4-hop tool -m tools.pd_probe hop --decode {decode} --prefill {prefill} --lmcache {lmcache} --capture $CAP`, then `bash cluster/loadgen.sh e4-m2 replay --capture $CAP --mix m2 --rate $R50 --duration 300`. Then `bash cluster/variant.sh nixl-hop --preset nixl-hop` and the hop probe again as `e4-hop-nixl`. | `hop.json` of both hop media, `hops.jsonl` | 30 min |
| S2-3 | E10 | `baseline`, then `edf` | `bash cluster/variant.sh baseline` (S2-2 ends on `nixl-hop`), then `bash cluster/loadgen.sh e10-fcfs replay --capture $CAP --mix m4 --rate $R100 --duration 420 --extra noisy:0.25`, then `bash cluster/variant.sh edf --router flow_control.ordering=edf-ordering-policy` and the same run as `e10-edf` | 429 `tenant_tokens` for `noisy` only, TTFT p95 of the others | 22 min |
| S2-4 | E12 | `baseline` | `bash cluster/variant.sh baseline`, then `bash cluster/loadgen.sh e12-abort replay --capture $CAP --mix m4 --rate $R100 --duration 420 --abort 0.2` | Aborts, KV use back to the base level | 12 min |
| S2-5 | E14 | `baseline`, then `nosplit` | `bash cluster/loadgen.sh e14-on-int tool -m tools.queue_order --target <gateway> --long-class interactive --rounds 5 --capture $CAP` and `e14-on-batch` with `--long-class batch`. Then `bash cluster/variant.sh nosplit --router pd_non_cached_tokens=1000000`, and `e14-off-int` and `e14-off-batch`. | `queue-order.json` of each arm | 16 min |
| S2-6 | E8 arm B | `baseline` | `bash cluster/variant.sh baseline`. Then delete the decode pod at 180 s (note 1) during `bash cluster/loadgen.sh e8-c-warmup replay --capture $CAP --mix m4 --rate $R70 --duration 600` | Time to ready, time to warm, first TTFT | 12 min |
| S2-7 | E8 arm A | `warm-immediate` | `bash cluster/variant.sh warm-immediate --warm WARM_MODE=immediate`, then the run of S2-6 with the run id `e8-c-immediate` | The same | 12 min |
| S2-8 | E13 | `baseline` | `bash cluster/variant.sh baseline`, then `bash cluster/loadgen.sh e13-gate replay --capture $CAP --mix m4 --rate $R150 --duration 420` (no provider key: the gate records `would_leave`) | The gate decisions in `edge-access.jsonl` | 10 min |
| S2-9 | E16 arm B (K2) | `baseline` | `bash cluster/variant.sh baseline`, `bash cluster/loadgen.sh e16-k2 replay --capture $CAP --mix m2 --rate $R100 --duration 420`, then note 2 with the run id `e16-k2-restart` | Hit ratio (GPU and CPU), the first TTFT after the restart | 16 min |
| S2-10 | E16 arm A (K0) | `e16-k0` | `OVERLAY=t2-k0 bash cluster/variant.sh e16-k0`, then S2-9 with the run ids `e16-k0` and `e16-k0-restart`. K0 has no LMCache, so its hop is NIXL: compare the prefix hits, and read the TTFT with this in mind. | The same | 22 min |
| S2-11 | E7 | `devmode`, then `devmode-approx` | `bash cluster/variant.sh devmode --preset devmode`, then note 3 with the run id `e7-precise`. Then `bash cluster/variant.sh devmode-approx --preset devmode --router prefix_index=approx`, and note 3 with `e7-approx`. | `ghosts.json` of each arm | 24 min |
| S2-12 | E8 ramp | `e8-ramp`, then `e8-jump` | `bash cluster/variant.sh e8-ramp --router pd_mode=off --preset layout-a`, then the run of S2-6 with the run id `e8-a-ramp`. Then `bash cluster/variant.sh e8-jump --router pd_mode=off --preset layout-a --warm WARM_RAMP_MODE=jump`, and the run of S2-6 with `e8-a-jump`. | Fleet TTFT p99, the ramp labels | 30 min |
| S2-13 | E11 | `stale-b` | `bash cluster/variant.sh stale-b --router pd_mode=off --preset layout-a --preset stale-b`, then note 4 for each mode | Share of traffic to pod B, TTFT p99 | 25 min |
| S2-14 | End | - | the teardown below | - | 10 min |

## The variant options

| Variant | Options |
|---|---|
| `seqs-16` | `--arg vllm-decode:--max-num-seqs=16` |
| `seqs-32` | `--arg vllm-decode:--max-num-seqs=32` |
| `mnbt-8k` | `--arg vllm-prefill:--max-num-batched-tokens=8192` |
| `noprefix` | `--drop vllm-prefill:--enable-prefix-caching --arg vllm-prefill:--no-enable-prefix-caching --drop vllm-decode:--enable-prefix-caching --arg vllm-decode:--no-enable-prefix-caching` |
| `fp8kv` | `--arg vllm-prefill:--kv-cache-dtype=fp8 --arg vllm-decode:--kv-cache-dtype=fp8` |

The base values: prefill `--max-num-batched-tokens=16384` and `--max-num-seqs=8`, decode `--max-num-batched-tokens=2560` and `--max-num-seqs=24`. vLLM refuses a decode value below 2,496, the largest image item of Gemma 4 (G1). An LMCache key has no KV dtype, so `variant.sh` restarts the LMCache server when the KV format changes (`fp8kv` and the variant after it). The tier then starts empty.

## E17 (node 2, step B7)

```bash
source cluster/state/session.env
E=$(kubectl -n companion get pods -l app=edge -o jsonpath='{.items[0].metadata.name}')
mkdir -p metrics/e17
kubectl -n companion exec "$E" -- python -m control.guard.eval latency --out /tmp/e17/latency.json
kubectl -n companion exec "$E" -- python -m control.guard.eval pages --out /tmp/e17/pages.json
kubectl -n companion exec "$E" -- cat /tmp/e17/latency.json > metrics/e17/latency.json
kubectl -n companion exec "$E" -- cat /tmp/e17/pages.json > metrics/e17/pages.json
```

## One node (2026-10-01, if no node 2 shape has stock)

ADR-005, revision 2: one 4 x H100 node runs all pods. The launch tool refuses this layout when one region has stock for both a node 2 shape and a node 1 shape. Then use the two-node plan.

Steps:

1. Launch: `printf 'gpu_4x_h100_sxm5\n' | uv run python cluster/lambda/lambda_ctl.py launch --node one-t4 --region <region> --approve`. Report the launch to the owner at once.
2. Start the spend guard: `uv run python cluster/lambda/spend_guard.py --interval 60`.
3. Bring-up (about 40 minutes): `bash cluster/sessions/thu-one-up.sh`. A failed stage stops the script.
4. Session (about 3 hours): `bash cluster/sessions/thu-one.sh`. It runs `make demo-check`, then `cluster/sessions/wed-experiments.sh` (E3 with 32 decode sequences, E7 again), then E9, then the evidence capture.
5. Check the images in `metrics/<run-id>/grafana/` for each run.
6. Terminate: `printf 'companion-one\n' | uv run python cluster/lambda/lambda_ctl.py terminate --name companion-one --approve`. Report it to the owner at once, and record it in `docs/budget-ledger.md`.

Checks in the bring-up (stop if one fails):

1. `bash cluster/one.sh placed`: each engine pod has its own GPU, and the node 2 GPU pods share one GPU. If a node 2 GPU pod is on a second GPU, run `bash cluster/one.sh pin`, then `bash cluster/one.sh placed`. Then run the stages from `wait` on.
2. `bash cluster/one.sh gpus`: the KV cache memory of each engine pod is within 1 GiB of the value of 2026-09-29 (prefill 36.34 GiB, decode 38.36 GiB). A lower value shows that HAMi limits vLLM. Then stop, and check `CUDA_DISABLE_CONTROL` in the engine pods.
3. `bash cluster/one.sh smoke` passes.

Time: launch by about 18:45 PDT for all steps. Before a long step, `thu-one.sh` asks the spend guard for the minutes before its first stop rule (`spend_guard.py --minutes-left gpu_4x_h100_sxm5`). These rules are the 23:00 stop, the 4 x H100 hours, and the USD limit. The script skips E3 and E7, or E9, if the step and 45 minutes for the capture do not fit. The evidence capture always runs.

Videos (2026-10-01, the owner was away): `bash cluster/sessions/record.sh demo <name>` records the demo questions in the UI. `bash cluster/sessions/record.sh grafana <name> <seconds>` records the dashboard "Pods / replicas / KEDA". Both run a headless browser on the node, so the screen of the laptop has no part in them. The videos go to `metrics/videos/`.

E9 on one node: the engine has three GPUs, so only one pool can grow at a time. `bash cluster/one.sh hold <pool>` holds a pool at one pod (the KEDA pause annotation), and `release` removes the hold. If the owner is present, the owner records the screen of the dashboard "Pods / replicas / KEDA" for each load.

Evidence: Grafana and Prometheus run on the same node. Thus `bash cluster/one.sh capture` makes the report, the backup with the Prometheus snapshot, and the Grafana images before the terminate step. Each run record shows the overlay (`one` or `one-e9`) and the GPUs of each pod.

## E9 (4 x H100, a separate session)

The day plan approves `node1-t4` on 2026-09-30 and 2026-10-01. The bring-up is the same, with these changes:

1. B4: `bash cluster/g0.sh install t4` (KEDA can add one pod to each pool).
2. B8: `printf 'gpu_4x_h100_sxm5\n' | python3 cluster/lambda/lambda_ctl.py launch --node node1-t4 --region <region> --approve`.
3. B10: `OVERLAY=t4 bash cluster/t2.sh smoke`.

Then run the two loads. The owner records the screen of the Grafana dashboard "Pods / replicas / KEDA" for each load.

```bash
source cluster/state/session.env
bash cluster/loadgen.sh e9-decode capacity --target http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1 --tokens 1000 --max-tokens 4000 --levels 48 --repeats 4
bash cluster/loadgen.sh e9-prefill capacity --target http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1 --tokens 24000 --max-tokens 64 --levels 16 --repeats 20
```

Pass rule: `companion:planner_desired_replicas` asks for a second pod of the right pool, and KEDA adds it.

## Teardown (each session)

```bash
bash cluster/t2.sh report
bash cluster/g0.sh backup
printf 'companion-node1\n' | python3 cluster/lambda/lambda_ctl.py terminate --name companion-node1 --approve
PROM_SNAPSHOT=1 bash cluster/g0.sh backup
python3 tools/grafana_shots.py check --session
python3 tools/grafana_shots.py render --session
printf 'companion-node2\n' | python3 cluster/lambda/lambda_ctl.py terminate --name companion-node2 --approve
```

The Prometheus snapshot comes before the images (session 1). About 35 runs take about 25 minutes to render. If the node 2 block ends first, the data is safe. On a long day, render the runs of session 1 in a quiet time (for example, the demo slot) with `--runs`.

Grafana and Prometheus run on node 2. Thus the images render after node 1 stops: no H100 time for them, and no extra load during the runs. Each run gets nine images in the order of the handout walk, and `walk.md` (`metrics/<run-id>/grafana/`). The talk uses them (handout Part 8).

The terminate command asks for the instance name, so `printf` gives it. Then check that `python3 cluster/lambda/lambda_ctl.py list` shows no instance. Record the hours and the cost in `docs/budget-ledger.md`, and commit `metrics/`.

## Notes

1. Delete a pod at 180 s. Start this before the run, in the same shell:

   ```bash
   mkdir -p metrics/<run-id>
   (sleep 180; date -u +%Y-%m-%dT%H:%M:%SZ > metrics/<run-id>/delete-time.txt
    kubectl -n companion delete pod -l app=vllm-decode --wait=false) &
   ```

2. E16, the restart: `kubectl -n companion rollout restart deploy/vllm-decode`, then `kubectl -n companion rollout status deploy/vllm-decode --timeout=15m`, then `bash cluster/loadgen.sh <run-id> replay --capture $CAP --mix m2 --rate $R100 --duration 120`.
3. E7, the cache clear at 150 s. Start the clear, then the run, then count the ghosts:

   ```bash
   mkdir -p metrics/<run-id>
   (sleep 150; date -u +%Y-%m-%dT%H:%M:%SZ > metrics/<run-id>/clear-time.txt
    kubectl -n companion exec deploy/vllm-decode -c modelserver -- python3 -c \
      "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8200/reset_prefix_cache', method='POST')).status)") &
   bash cluster/loadgen.sh <run-id> replay --capture $CAP --mix m2 --rate $R50 --duration 300
   D=$(kubectl -n companion get pods -l app=vllm-decode -o jsonpath='{.items[0].status.podIP}')
   python3 tools/ghosts.py --log metrics/<run-id>/envoy-access.log --pod "$D" \
     --after "$(cat metrics/<run-id>/clear-time.txt)" --out metrics/<run-id>/ghosts.json
   ```

4. E11, one run for each proxy mode:

   ```bash
   source cluster/state/session.env
   for m in pass frozen stall; do
     kubectl -n companion exec deploy/vllm-decode -c stale-proxy -- python -c \
       "import httpx; print(httpx.post('http://127.0.0.1:8300/__stale', json={'mode': '$m', 'delay_s': 15}).text)"
     bash cluster/loadgen.sh "e11-$m" replay --capture $CAP --mix m3 --rate $R100 --duration 300
   done
   ```

5. `loadgen.sh` puts the pod IPs in place of `{decode}` and `{prefill}`. It adds `--out /data/runs/<run-id>` to `replay`, `capacity`, and `tool`. It stops waiting when the Job is Complete or Failed.
6. E2 gives no RATE100 if no capacity shed comes, or if the first shed comes in the first minute. Then run the soak again: `--rate 0.5 --ramp 0.1` (no shed), or `--rate 0.02 --ramp 0.02` (an early shed).
7. E1, E9, and E14 send long, unique user messages (8K to 27K tokens). Edge checks each new user message with the safety model, which has a context of 8,192 tokens. At G0, two to four such checks at once gave 503 `guard_unavailable`. E1, E9, and E14 measure the engine and the router, so they send to the gateway and skip edge. E14 sets its own class and tenant headers. App traffic sends one short question for each turn, and the later steps use the cached verdict.
