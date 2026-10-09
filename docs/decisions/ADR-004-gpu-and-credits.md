# ADR-004: GPU, cloud, and credits

Status: accepted (2026-09-27). This version replaces the version of 2026-09-26. The debate is in `DEBATE-LOG.md`, points 12 and 6. Owners: the project owner and Claude.

## Context

Credits: Lambda 400 USD, Superlinked 500 USD, Modal (amount not known). The handout accepts any NVIDIA GPU, but the paper math must use the GPU that we ran (L521). Kubernetes, HAMi, and KEDA need a normal VM, so Modal cannot host the cluster. Lambda Managed Kubernetes needs 16 GPUs for 2 weeks (about 33,000 USD), so we run our own k3s.

## Decision

| Use | Where | Shape | Price on 2026-09-27 |
|---|---|---|---|
| Node 1: engine (T2) | Lambda | 2 x H100 SXM 80 GB, NVLink | 8.38 USD for each hour |
| Node 1: scale session (T4) | Lambda | 4 x H100 SXM 80 GB | 16.36 USD for each hour |
| Node 2: control plane, data plane, guard, dev days | Lambda | 2 x RTX A6000 48 GB, 28 vCPUs, 200 GiB RAM | 2.18 USD for each hour |
| Node 2 when no 2 x A6000 has stock (2026-09-29, the owner) | Lambda | 1 x H100 SXM 80 GB, 26 vCPUs, 225 GiB RAM | 4.29 USD for each hour |
| State between sessions | Lambda filesystem and a copy on the owner's laptop | about 150 GB | 0.20 USD for each GB each month |
| Overflow target | Superlinked | hosted API | credits, 15 USD each day at most |
| Search | Tavily (main), Brave (optional) | API | 0 USD inside the free credits |

Why the H100 SXM: see `05-capacity-plan.md`, section 7. It is the smallest Lambda GPU with FP8 compute for SLO-1, and it gives two GPUs with NVLink in one instance. The GH200 is cheaper, but it comes with one GPU for each instance and an ARM CPU.

## Budget

| Block | USD (estimate) |
|---|---|
| G0, G2, and dev days (node 2 only, 16 h) | 35 |
| Gate G1 (2 x H100 and node 2, 3 h) | 32 |
| Sessions 1 and 2 (2 x H100 and node 2, 11 h) | 116 |
| Scale session (4 x H100 and node 2, 2.5 h) | 46 |
| Filesystem | 8 |
| **Planned work** | **237** |
| Optional live demo (3 h, both nodes) | 32 |

## Rules for spend

1. Start the instances only for a planned block of work. Terminate them at the end of the block. Record each block in `docs/budget-ledger.md`.
2. Keep the model weights and the compile cache on a Lambda filesystem in the same region. A new instance then does not download 35 GB again. **VERIFY** the region stock for both shapes.
3. Run a watchdog. If the GPU use is below 5% for 45 minutes, it sends an alert.
4. Stop the work at 320 USD of Lambda spend. Keep 80 USD for the talk (the optional live demo, or a last rerun).

## Risk

Lambda has limited stock. If 2 x H100 SXM is not available, try another region, then 4 x H100 SXM for a shorter time. If no H100 shape is available, node 2 runs the dev model for the mechanics, and `DESIGN.md` gives the reason.
