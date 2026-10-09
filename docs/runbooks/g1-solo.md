# Runbook: engine-only Gate G1 on node 1 (2 x H100 SXM)

Use this runbook when node 1 has stock and node 2 does not (`DEBATE-LOG.md`, point 13). The approval in advance allows 4 hours of H100 at most (33.52 USD). The spend guard (`cluster/lambda/spend_guard.py`) terminates node 1 at 4 hours or at 100 USD in total.

## Before the launch (no cost)

These checks passed on 2026-09-27:

1. The HF token reads the main model and the challenger model.
2. The images `vllm/vllm-openai:v0.30.0` and `v0.26.0` and the sidecar `llm-d-router-disagg-sidecar:v0.11.0` exist.
3. Every flag of the engine manifests exists in vLLM v0.30.0 and v0.26.0.
4. The Glimmer parsers `muse_glimmer` exist in vLLM v0.30.0.
5. `make check` passes, and the spend guard runs.

## Steps

| Step | Command | Pass rule | Time budget | If it fails |
|---|---|---|---|---|
| 1. Launch | `printf 'gpu_2x_h100_sxm5\n' \| python3 cluster/lambda/lambda_ctl.py launch --node node1-t2 --region <region> --approve` | The instance is active. | 10 min | Stop. A failed launch costs nothing. |
| 2. Node | `bash cluster/g1-solo.sh ips`, then `bootstrap` | The k3s node is Ready. | 10 min | If the NVIDIA runtime is missing, terminate. |
| 3. Access | `bash cluster/g1-solo.sh kubeconfig`, then `tunnel` | `kubectl get nodes` works. | 2 min | Check the SSH key. |
| 4. Engine | `bash cluster/g1-solo.sh engine`, then `wait` | Both vLLM pods and the LMCache server are Ready. | 40 min | Connector error: `OVERLAY=t2-k0`. Engine error: `OVERLAY=t2-vllm026`. After both fallbacks, go to step 8. |
| 5. Tests | `bash cluster/g1-solo.sh tests` | The pass rules of `06-experiments.md`, Gate G1 | 40 min | Record the failure. Do not debug one test for more than 15 min. |
| 6. Restart | `bash cluster/g1-solo.sh restart` | The first request after the restart hits the CPU tier. | 15 min | Record the result. |
| 7. Challenger | `bash cluster/g1-solo.sh challenger` | The tool-call pass rate of Glimmer | 45 min | Do this step only if 60 min or more of the 4 hours are left. Else skip it. |
| 8. Report, stop | `bash cluster/g1-solo.sh report`, then `printf 'companion-node1\n' \| python3 cluster/lambda/lambda_ctl.py terminate --name companion-node1 --approve` | The files are in `metrics/g1-solo-*`. The instance is gone. | 10 min | The spend guard terminates at 4 hours. |

Expected time: 2 hours 50 minutes with the challenger (about 24 USD), or 2 hours 5 minutes without it (about 17.50 USD).

## Rules

1. Measure only warm pods. The first requests of each test are the warmup.
2. After each step, write the time and the result in the session log (`metrics/g1-solo-*/session.md`).
3. Record each launch and each terminate step in `docs/budget-ledger.md`.
4. The screenshot test needs the browser service on node 2. It moves to the first session with both nodes.
