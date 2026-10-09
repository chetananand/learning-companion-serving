# ADR-010: Platform (Kubernetes, GPU sharing, autoscaling, observability, operations)

Status: accepted (updated 2026-09-27 for two nodes and the llm-d stack). The debate is in `DEBATE-LOG.md`, point 12. Owners: the project owner and Claude.

## Decision

| Part | Choice and pinned version | Reason |
|---|---|---|
| Kubernetes | k3s on two nodes: the server on node 2, an agent on node 1. NVIDIA container runtime as the default runtime. | GPU nodes come and go. The class stack uses k3s. |
| Node network | flannel `wireguard-native` | Encrypted traffic between the two instances. **VERIFY** the network path at G0. |
| GPU sharing | HAMi Helm chart 2.10.0 (fallback 2.9.0), on node 2 only | Memory and core slices for SIE, the guard models, and the dev model. The vLLM pods on node 1 use full GPUs. |
| Gateway API and router | Gateway API CRDs, GAIE v1.0.x CRDs, Envoy Gateway v1.8.1 with the Agent Router v1.1 values, llm-d router v0.11.0 (fallback v0.10) | The llm-d guide gives this install order. |
| Autoscaling | KEDA 2.19 with the Prometheus scaler | One ScaledObject for each LLM pool. The trigger is our planner rule. HPA behavior gives the hysteresis. |
| Metrics | kube-prometheus-stack (Prometheus, Grafana, Alertmanager, node exporter, kube-state-metrics) and the DCGM exporter | One chart gives the scrape, the dashboards, and the alert rules. |
| Traces (P1) | OpenTelemetry Collector and Grafana Tempo | Spans with plane labels (Case 04). |
| Manifests | Kustomize: one base and three overlays (`dev`, `t2`, `t4`) | The same manifests run on all layouts. |
| Access | SSH tunnel to 127.0.0.1. NodePorts only on 127.0.0.1. | Lambda opens only SSH. The handout asks for localhost or the cluster network (L628). |

## Operations

1. On demand (O1): scripts in stages, not one `make up`, because each stage has its own pass rule and time budget (`docs/runbooks/`). The owner approves each launch (A7). The spend guard enforces the limits of the approval.
   - Launch and terminate: `cluster/lambda/lambda_ctl.py`.
   - Node 2 first: `cluster/g0.sh` (bootstrap, images, install, `restore`).
   - Node 1 after node 2 is ready: `cluster/t2.sh` (join, wait, smoke). Thus the H100 never waits for node 2.
   - Before each terminate step: `cluster/g0.sh backup` (the Qdrant snapshot and the capture files).
2. State (S3): the Lambda filesystem holds the weights, the vLLM compile cache, and the backups. The owner's laptop keeps a second copy of each backup. Live database files stay on the local disk. Redis starts empty in each session.
   - The owner chose no Lambda filesystem (2026-09-28): the stock moves between regions, and a filesystem stays in one region. The sessions use the laptop copy only (`cluster/state/backups/`), and node 1 downloads the model in each session (about 32 GB, 3 minutes at G1).
3. Deploy (no GitHub Actions): `make deploy` does these steps:
   - Run the tests on the laptop, and copy the code to node 2.
   - Build our images on node 2, and load them into k3s.
   - Apply the manifests, and run the smoke tests.
   - We pull all other images. We use no registry.
4. Install order:
   - Cluster: k3s server, k3s agent, HAMi, the CRDs.
   - Platform: Envoy Gateway and the Agent Router, KEDA, kube-prometheus-stack, DCGM exporter, Redis.
   - Data and guard: Qdrant, SIE, the guard models, `guard`.
   - Engine: LMCache server, vLLM pools, InferencePool and router.
   - Ours: `edge`, `warm-controller`, the app.

## Consequences

- Each chart version and image digest is in `cluster/versions.env`.
- Our images: `edge`, `warm-controller`, and `app`. If the vLLM image lacks the right `lmcache` version, we add one more image layer for vLLM.
