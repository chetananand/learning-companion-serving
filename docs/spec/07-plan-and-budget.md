# Plan, budget, and risks

Updated on Sunday 2026-09-27, after the debate (`docs/decisions/DEBATE-LOG.md`). The first due date was Saturday 2026-10-03 (L512). On 2026-10-02, the owner told us that the course moved the due date and the talk to Saturday 2026-10-10. The talk is in the class slot: 15:30 to 18:00 UTC, or 08:30 to 11:00 PDT (America/Los_Angeles). We submit on Friday 2026-10-09 in the evening, Pacific time. The talk shows charts and recordings, and a live demo is optional.

## 1. Scope rule

The grade is for the serving shape (L559). Thus the control plane, the cluster, and the proof come first. The app gets the scope in the product spec. If the app is late at the D3 exit rule, we stop app work and keep the proof on time.

## 2. Day plan

| Day | Date | GPU | Work | Exit rule |
|---|---|---|---|---|
| D1 | Sun 09-27 | none (laptop) | Spec pass. Question banks (`08-question-bank.md`). Repo scaffold (uv, ruff, pytest). `edge` with unit tests (guard stage 1, reject mapping, stay or leave, overflow limiter). Fact-check rules (F2) with unit tests. `warm-controller` state machine with tests. Router config renderer. NeMo guard config. Cluster scripts (Lambda launch and terminate, k3s, WireGuard) and manifests. | All unit tests pass. Manifests render. |
| D2 | Mon 09-28 | node 2, about 6 h | Gate G0. Gate G2 (OCR model, image token budget). Ingest: Notion to Qdrant through SIE. Companion API and the agent workflow. | G0 passes, or the fallback is in `DEBATE-LOG.md`. The corpus is in Qdrant. |
| D3 | Tue 09-29 | 2 x H100 about 3 h (morning), node 2 about 6 h | Gate G1 (model and KV tier). Fact-check loop. Streamlit UI. Trace recorder and replayer. Dashboards as code, recording rules, alert rules. E17 (guard). | G1 decides the model and the KV tier. J1, J2, and J3 run end to end. All eight dashboards show data. |
| D4 | Wed 09-30 | 2 x H100, about 6 h | Session 1: E1, E2, E3, E5, E6, E15. | Plots and scrapes saved in `metrics/` and `plots/`. |
| D5 | Thu 10-01 | 2 x H100, about 5 h | Session 2: E4, E7, E8, E10, E11, E12, E13, E14, E16. Video of the demo questions. Part 5 notebook. | All Part 5 answers have a scrape. |
| D6 | Fri 10-02 | 4 x H100, about 2.5 h | Scale session: E9, recorded offline. `DESIGN.md` with scrapes. Plots. Slides (STE). Final review of the checklist with the owner. STE lint. README. Submit the GitHub link and the notebook. | Submitted on Friday evening, Pacific time. |
| D7 | Sat 10-03 | none (live demo optional) | Dry run of the talk early in the morning. The talk is at 08:30 PDT. | The talk is done. |

Node 2 runs in every GPU block, because it holds the k3s server, the control plane, the guard, and SIE.

## 3. Budget

### 3.1 Lambda (400 USD)

Prices on 2026-09-27, in USD for each hour of an instance:

- 2 x H100 SXM: 8.38
- 4 x H100 SXM: 16.36
- 2 x A6000 (node 2): 2.18

The filesystem costs 0.20 USD for each GB each month.

| Block | Hours | USD (estimate) |
|---|---|---|
| G0, G2, and dev days (node 2 only) | 16 | 35 |
| Gate G1 (2 x H100 and node 2) | 3 | 32 |
| Sessions 1 and 2 (2 x H100 and node 2) | 11 | 116 |
| Scale session (4 x H100 and node 2) | 2.5 | 46 |
| Filesystem (150 GB for 8 days) | - | 8 |
| **Planned work** | | **237** |
| Optional live demo (3 h, both nodes), from the reserve | | 32 |

Rules (ADR-004):

1. The stop for work is 320 USD. This leaves about 83 USD for reruns and mistakes.
2. We keep 80 USD above the stop for the talk (the optional live demo, or a last rerun).
3. We terminate each instance at the end of its block. We record each block in `docs/budget-ledger.md`.
4. A watchdog alerts if the GPU use stays below 5% for 45 minutes.

### 3.2 Other credits

| Credit | Use | Cap |
|---|---|---|
| Superlinked (500 USD) | Overflow target (ADR-008). | 15 USD each day |
| Tavily | Main search provider for the fact check (1,000 free credits each month). | 0 USD |
| Brave Search | Optional second provider. Not in use. | 0 USD |
| Modal | Not needed. | 0 USD |

## 4. Actions for the owner

These actions need the owner. They include accounts, keys, license approvals, and approvals of paid launches. Claude cannot do them.

| ID | Action | Needed by |
|---|---|---|
| A1 | Done (2026-09-27): `NOTION_API_KEY` in `~/.env` reads the Bookmarks database. | done |
| A2 | Done (2026-09-27): `TAVILY_API_KEY` works (1,000 free credits each month). A Brave Search API key is optional. | done |
| A3 | Dropped (the owner, 2026-09-28): the handout asks for the overflow gate, not for a live provider. E13 proves the gate with no key. | - |
| A4 | Done (2026-09-27): the key is `LAMBDA_API_KEY` in `~/.env`. The SSH key `~/.ssh/lambda_ai` is on Lambda as "Lambda AI". | done |
| A5 | Done (2026-09-27): a private GitHub repository. Commits use the GitHub no-reply address. Git ignores the course material in `docs/spec/source/`. | done |
| A6 | Done: the presentation date (2026-10-03, 15:30 UTC). | done |
| A7 | Approve each GPU launch and each terminate step. Claude never creates a GPU or another paid resource without this approval. Done in advance (2026-09-27, about 18:40 PDT) for node 2 (8 hours for each block, 16 hours in total, never overnight) and node 1 with 2 x H100 (4 hours in total), with 100 USD in total. `cluster/lambda/spend_guard.py` enforces these limits. Another shape or more hours needs a new approval. | each block |
| A9 | At Gate G0: if the two nodes cannot reach each other over their private IPs, add one Lambda firewall rule: TCP 6443, TCP 10250, and UDP 51820 from the private network only. Claude does not change firewall rules. | G0 |
| A8 | Done (2026-09-27): Meta approved Prompt Guard 2. The HF token reads `meta-llama/Llama-Prompt-Guard-2-86M`, and `guard-injection` uses it. The fallback `protectai/deberta-v3-base-prompt-injection-v2` stays in `cluster/versions.env`. | done |

## 5. Risks

| Risk | Effect | What we do |
|---|---|---|
| No H100 stock on Lambda | No measurement session | Observed: no 2 x H100 SXM stock in 24 checks on 2026-09-27. Options S1 to S4 and the recommendation are in `DEBATE-LOG.md`, point 13 (open). Last choice: node 2 with the dev model for the mechanics. |
| Both shapes are not in one region, or no network path between the nodes | No two-node cluster | WireGuard over the public IPs. Last choice: one GPU node, with Prompt Guard 2 on the CPU and the hosted Superlinked SIE. |
| vLLM v0.30.0 fails with the llm-d router | Engine errors | Pin v0.26.0 (the version that llm-d tests). |
| Router v0.11.0 regression | Router errors | Pin router v0.10. |
| `MultiConnector` with NIXL and LMCache fails for Gemma 4 | No CPU tier | K0 (GPU prefix cache only), with the reason in `DESIGN.md`. |
| Gemma 4 P/D fails | No hop proof with the main model | The same block size on both pods first. Else option A for the measurements, and the hop proof with the dev model. The handout accepts a hop proof or a warmup proof. |
| Guard latency above its budget | Slower first token | The verdict cache, and only the new user content. Else a smaller image budget for the safety check. |
| The app takes too long | No time for the proof | Stop app work at the D3 exit rule. |
| Surprise cost | Budget runs out | Watchdog alert, terminate after each block, ledger, stop at 320 USD. |

## 6. Repository layout

```text
learning-companion/
  README.md                 how to run, links to the spec
  DESIGN.md                 the handout questions with scrapes
  pyproject.toml, uv.lock   pinned Python packages
  app/                      api/, agent/, rag/, factcheck/, ui/, ingest/, sweep/, loadgen/, tests/
  control/                  edge/, warm/, guard/ (NeMo config), router/ (llm-d and Agent Router config), tests/
  cluster/                  lambda/, bootstrap/, manifests/ (kustomize), monitoring/, smoke/, versions.env
  notebook/                 part5_queue.ipynb, proof.ipynb
  plots/                    output of the notebook, videos
  metrics/                  scrapes, hop records, access logs, boot logs
  docs/                     spec, decisions, style, budget ledger
  tools/                    capacity.py, ste_lint.py
```
