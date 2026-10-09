# ADR-005: Topology

Status: accepted (2026-09-27). Revision 2 (2026-09-30): one node, when no node 2 shape has stock. The owner accepted revision 2 on 2026-09-30 (about 20:20 PDT). Experiment E3 gives the numbers. The debate is in `DEBATE-LOG.md`, points 12 and 7. Owners: the project owner and Claude.

## Context

The handout asks us to choose two or three colocated replicas or a prefill/decode split, and what we used to slice and why (L601). It asks for at least two workers (L612). Its scale answer names two pools: uncached prefill tokens scale prefill, and busy decode slots scale decode (L624). The revision notes say: do not split P/D on one sliced GPU (R-01).

## Options for node 1 (2 x H100)

| Option | For | Against |
|---|---|---|
| A. Two replicas, each with prefill and decode | No hop. Both GPUs can run prefill. | Prefill chunks and decode streams share each step. For Gemma 4 31B, a 2,048-token chunk adds about 160 ms to a step (SLO-2 is 50 ms). Only one pool. |
| B. One prefill pod and one decode pod, every request splits | Stable inter-token latency. Two pools. | Each short request pays a hop and a second queue. |
| **C. B, plus the llm-d P/D decider** | Short or cached requests stay on the decode pod (same pod, no hop). Long uncached prompts split (two ids, a hop). Two pools, each with its own scorers and scale signal. | The threshold needs a measurement. |

## Decision

1. Node 1: option C. `vllm-prefill-0` on GPU 0 and `vllm-decode-0` on GPU 1. Each pod has a full GPU, TP 1, and no HAMi.
2. The `prefix-based-pd-decider` splits a request when 2,048 or more of its tokens are not cached on the decode pod. Experiment E5 tunes this value.
3. Node 2 (2 x A6000): the control plane and the data plane on the CPU. GPU 0 holds SIE on HAMi slices. GPU 1 holds the guard models on HAMi slices.
4. T4 (4 x H100) for one scale session: KEDA can add a prefill pod or a decode pod on GPU 2 and GPU 3.
5. Dev days: node 2 only. `gemma-4-E4B-it` runs a prefill pod on GPU 0 and a decode pod on GPU 1, on HAMi slices.

## Revision 2 (2026-09-30): one node, when no node 2 shape has stock

On 2026-09-30, the watcher ran from 12:37 PDT. No 2 x A6000 and no 1 x H100 had stock in any region. 4 x H100 had stock two times (13:05 and 17:03 PDT). Node 1 (4 x H100) waited 45 minutes for node 2 and then stopped (12.20 USD). Thursday 2026-10-01 is the last GPU day, and the three must-do items and E9 are not done.

New decision (the owner: "yes, start now"):

1. If no node 2 shape has stock on Thursday, one 4 x H100 node runs all pods. The overlay `one` is for the three must-do items. The overlay `one-e9` is for E9. The bring-up script is `cluster/one.sh`.
2. HAMi manages the four GPUs. Each engine pod asks for 100% of the cores (`nvidia.com/gpucores`) and the memory (`nvidia.com/gpumem-percentage`) of one GPU. HAMi gives such a pod only a GPU that no other pod uses.
3. The engine pods have `CUDA_DISABLE_CONTROL=true`. Thus the HAMi device plugin does not load its memory hook into vLLM, and vLLM sees the full GPU, as on node 1.
4. The node 2 GPU pods (SIE embed, SIE OCR, and the two guard models: 52,000 MiB) pack onto one GPU (`hami.io/gpu-scheduler-policy: binpack`). On 2026-09-29, node 2 (1 x H100) had the same pods on one GPU.
5. E9 has three GPUs for the engine. KEDA can add one pod to one pool at a time. The E9 script holds the other pool at one replica with the KEDA pause annotation.
6. Grafana and Prometheus run on the same node. Thus the Grafana images, the Prometheus snapshot, and the backup come before the terminate step.

Source check: HAMi v2.10.0 (`pkg/device/nvidia/device.go`, `pkg/device-plugin/nvidiadevice/nvinternal/plugin/server.go`, and `charts/hami/values.yaml`). The chart default GPU policy is `spread`, so the pod annotation is necessary.

Differences from the two-node runs (the report states them):

- The calls between the node 2 pods and the engine pods stay on one node. They do not cross the private network.
- The node has 104 vCPUs and 900 GiB of RAM. On 2026-09-29, node 1 had 52 vCPUs and 450 GiB, and node 2 had 26 vCPUs and 225 GiB.
- The engine GPUs are the same type (H100 SXM5 80 GB), and the engine settings do not change.

VERIFY at the bring-up (`bash cluster/one.sh gpus`):

1. Each engine pod has its own GPU, and no other pod uses that GPU.
2. The node 2 GPU pods share one GPU.
3. The vLLM log shows the KV cache of a full GPU (173,657 tokens on 2026-09-29).

### Addendum (2026-10-01, about 18:23 PDT): one node of 8 x A100 80 GB

No H100 shape had stock in the evening, and 8 x A100 80 GB had stock. The owner: "take it now". The overlay `one` runs on it with no change. HAMi gives each engine pod a full GPU of 80 GB, and the node 2 GPU pods share one GPU.

Differences that the report states:

- The A100 has no FP8 tensor cores. vLLM runs the FP8 weights of the model through its Marlin kernels (weight-only FP8, BF16 compute). Thus the times are not comparable with the H100 runs of 2026-09-29.
- Comparisons inside the session are valid: both arms of E3 run on the same GPU type. E7 counts prefix hits, and the demo check tests the behavior of the agent.

## Consequences

- The router makes the split decision. We do not write route modes in our own code.
- Experiment E3 measures option A against option C on the M4 mix at three loads. `DESIGN.md` reports the goodput inside SLO-1 and SLO-2.
- The hop proof (E4) uses option C in all cases. The handout asks for a hop proof or a warmup proof, and we show both (E4 and E8).
