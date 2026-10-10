# Spec index

Read the files in this order. The spec is the source of truth. If the code and the spec do not agree, change the spec first, then the code.

| File | Content |
|---|---|
| `01-handout-checklist.md` | One item for each ask in the handout, plus the owner's asks. We check the items off together. |
| `02-research-findings.md` | The facts that the spec uses, with sources and dates. |
| `03-product-spec.md` | The app: journeys, agent workflow, fact-check rules, request contract, SLOs. |
| `04-system-design.md` | Planes, nodes, engine flags, the six decisions, eviction, scaling, observability, security, tests. |
| `05-capacity-plan.md` | Part 1 on paper: KV bytes, concurrency, hop cost, GPU choice, limiter hypothesis. |
| `06-experiments.md` | Gates G0 to G2 and experiments E1 to E17. |
| `07-plan-and-budget.md` | Day plan, budget, actions for the owner, risks. |
| `08-question-bank.md` | The demo questions (acceptance test), the load prompts, and the questions for the talk. |
| `09-architecture.md` | The architecture: the slide diagram of one LLM call, and Mermaid diagrams of the context, planes, deployment, the KV path, and the sequence flows. All use the names of the slide diagram. |
| `10-talk.md` | The talk of 2026-10-10: the slides, the time of each slide, the evidence, and the steps. |
| `../decisions/DEBATE-LOG.md` | The debate on the first spec, point by point, with each result. |
| `../decisions/` | ADR-001 to ADR-012. |
| `../style/ste-guide.md` | The ASD-STE100 rules for all text. |

## Summary of the design (after the debate, 2026-09-27)

1. **App**: a learning companion over 998 Notion bookmarks. It answers from the bookmarks, checks volatile claims on the live web, and cites every source. It combines Track A (RAG) and Track B (agent loop). The agent runs on LangChain and LangGraph Deep Agents. Code decides when a live fact wins (rule F2).
2. **Engine**: vLLM v0.30.0 (fallback v0.26.0). Main model Gemma 4 31B FP8-dynamic. Challenger Muse Glimmer 30B. Gate G1 decides.
3. **Cluster**: our own k3s on two on-demand Lambda nodes.
   - Node 1 (2 x H100 SXM): one prefill pod and one decode pod, each on a full GPU. An LMCache server holds a 150 GiB CPU tier.
   - Node 2 (2 x A6000): the control plane, the app, and the data plane. SIE runs on GPU 0, and the guard models run on GPU 1.
4. **Admission control + routing (production components, our policy)**:
   - `edge` (ours): guard stage 1, the call to `guard`, `slice_oom`, and stay or leave.
   - `guard`: NeMo Guardrails with Llama Prompt Guard 2 and Nemotron 3.5 Content Safety. It fails closed.
   - Agent Router (Envoy AI Gateway): tenant token limits.
   - llm-d router: flow control with priority bands and tenant fairness (admit and queue). Scheduling profiles with different scorers for prefill and decode (place). The P/D decider (hop).
   - `warm-controller` (ours): the warmup routine, the warm label, and the recovery ramp.
5. **KV**: NIXL moves the KV for the hop. LMCache keeps evicted blocks in host RAM.
6. **Scale**: Prometheus rules compute the desired replicas for each pool. KEDA scales the prefill and decode Deployments.
7. **Proof**: app traces with the Class 7 mixes, eight Grafana dashboards, a Part 5 notebook, plots from the cluster, and recorded videos. The talk shows charts. A live demo is optional.
