# Handout checklist

This checklist has one item for each ask in the handout "Final Project - Design the cluster and Serve an app". It also has the asks that the owner gave in the session on 2026-09-26.

## How to use this checklist

1. Check an item only when its evidence exists in the repository or in a saved scrape.
2. Write the evidence path next to the item when you check it.
3. If an item changes, change the spec first. Then change the code.

Legend:

- `L###` is the line number in `docs/spec/source/handout.txt`. That file is course material. It stays out of Git.
- P0 = necessary for the grade. P1 = strong extra. P2 = only if time is available.
- Evidence types: code, test, scrape (Prometheus or `/metrics` text), plot, notebook, doc, demo.

Due date: Saturday 2026-10-10. The course moved it from 2026-10-03 (L512), the owner told us on 2026-10-02. The talk is on the same day, in the class slot (08:30 PDT, 15:30 UTC).

## A. Project frame (L511 to L525)

- [ ] **H-01** (L517) Invent an application. It does not have to be a real product. P0. Evidence: `docs/spec/03-product-spec.md`, `app/`.
- [ ] **H-02** (L517) Design the cluster that serves the application. P0. Evidence: `docs/spec/04-system-design.md`, `cluster/`.
- [ ] **H-03** (L517) Write the control plane that decides what work enters. P0. Evidence: `control/`.
- [ ] **H-04** (L517) Prove the decisions on a GPU that we ran. P0. Evidence: `metrics/`, `plots/`, notebook.
- [ ] **H-05** (L517) In the presentation, walk through the code. Explain each choice and the reason for it. P0. Evidence: slides, demo script.
- [ ] **H-06** (L517) In the presentation, walk briefly through the Grafana dashboards. P0. Evidence: `cluster/manifests/base/monitoring/dashboards/` (from `tools/dashboards.py`), screenshots in `plots/`.
- [ ] **H-07** (L519) Serve our application on our cluster. Give a design that we can defend with our own numbers. P0. Evidence: `DESIGN.md`.
- [ ] **H-08** (L521) Use a real NVIDIA GPU (local, cloud, or a serverless GPU job). P0. Evidence: `nvidia-smi` capture in `metrics/`.
- [ ] **H-09** (L521) Size the paper math for the GPU that we ran, not for a brochure H100. P0. Evidence: `docs/spec/05-capacity-plan.md`, `tools/capacity.py`.
- [ ] **H-10** (L521) Do not use a fake GPU for the proof. Only unit tests can use FakeWorker. P0. Evidence: test names, scrapes with `vllm:` metrics.
- [ ] **H-11** (L523) Keep the engine as ours. A hosted chat API is only an overflow target. We own the KV, the workers, and the warmup. P0. Evidence: `cluster/`, `control/edge/overflow.py`.
- [ ] **H-12** (L525) Build the control plane in code. P0. Evidence: `control/`.
- [ ] **H-13** (L525) Show a live `/metrics` scrape or Grafana. P0. Evidence: `metrics/scrape-*.txt`, dashboards.
- [ ] **H-14** (L525) Show a hop proof or a warmup proof. We plan to show both. P0. Evidence: `metrics/<run-id>/hops.jsonl` (`tools/hop_records.py`), `plots/warmup-ttft.png` (`notebook/proof.py`).

## B. What we ship: the request path (L527 to L555)

- [ ] **H-15** (L529) Put our app in front. The app sends all LLM work through `edge`. P0. Evidence: `app/`, `edge` access log.
- [ ] **H-16** (L536) Guardrails give the first "no". A rejected request never reaches a GPU. P0. Evidence: `control/edge/guard.py` (stage 1 on the CPU), `control/guard/` (stage 2 on node 2 GPU 1, never a serving GPU), tests, `orch_guard_reject_total`, ADR-011.
- [ ] **H-17** (L538) Put an overflow gate that decides "stay or leave" after the local result. P0. Evidence: `control/edge/overflow.py`, test.
- [ ] **H-18** (L540) Admit: decide if we accept the request. P0. Evidence: Agent Router and llm-d flow-control config in `control/router/`, `control/edge/admit.py`.
- [ ] **H-19** (L542) Place: decide "which worker?". P0. Evidence: llm-d scheduling profiles in `control/router/`.
- [ ] **H-20** (L544 to L545) Own a queue. Same pod means no hop. Two worker ids mean a KV hop. Ask "is the target warm?". P0. Evidence: llm-d flow control and the P/D decider in `control/router/`, `control/warm/`.
- [ ] **H-21** (L547) Use a real engine with prefill, decode, KV store, and waiting, running, and preempted states. P0. Evidence: vLLM pods, `vllm:` scrape.
- [ ] **H-22** (L549) Keep the code semantics: 429, 500, and `slice_oom` stay. 503 and 529 may leave. P0. Evidence: `control/edge/overflow.py`, test table.
- [ ] **H-23** (L553) Measure the four scarce resources: decode slots, KV blocks, hop bandwidth, and warmup time. P0. Evidence: notebook section "Four resources".
- [ ] **H-24** (L553) Own the six decisions: guard, admit, place, queue (hop), declare warm, stay or leave. P0. Evidence: the decision table in `04-system-design.md` section 6, and the config or code in `control/` for each decision.
- [ ] **H-25** (L555) Do not write our own version of the vLLM scheduler (FCFS, DRR, chunked prefill, preemption). Use engine arguments. P0. Evidence: `cluster/manifests/vllm-*.yaml`, `DESIGN.md` section "Engine boundary".

## C. Part 0: the application (L557 to L571)

- [ ] **H-26** (L559) Pick a track. We combine Track A and Track B. We say in `DESIGN.md` that the combination is extra. P0. Evidence: `DESIGN.md` Part 0.
- [ ] **H-27** (L559) Remember the grade is for the serving shape, not for retrieval quality. The proof comes first. If the app is late at the D3 exit rule, app work stops. P0. Evidence: `docs/spec/07-plan-and-budget.md` scope rule.
- [ ] **H-28** (L563) Track A shape: a question retrieves k chunks. The prompt is system + question + chunks. The workload is mostly prefill. Prefixes are unique for each document and shared for the system prompt. P0. Evidence: prompt builder, token-share table.
- [ ] **H-29** (L563) Track A SLO: interactive TTFT. Batch work is indexing, eval, or offline QA. P0. Evidence: SLO table, batch jobs.
- [ ] **H-30** (L567) Track B shape: a user turn starts a loop (think, tool, observe, answer). Context grows each step. System + tool schema is a shared prefix. The conversation is not shared. P0. Evidence: agent loop code, step trace.
- [ ] **H-31** (L567) Track B load: multi-step decode with a longer prefill each step. Interactive is the user turn. Batch is a background sweep, eval, or a crew of agents. P0. Evidence: freshness sweep job.
- [ ] **H-32** (L567) Track B may use an agent framework (Crew, LangChain, Pydantic). We use LangChain and LangGraph Deep Agents (ADR-006). P1. Evidence: `app/agent/`.
- [ ] **H-33** (L569) Combined track: an agent that retrieves. P0 for us. Evidence: `app/agent/`.
- [ ] **H-34** (L571) The app calls our serve path. A completion that skips guardrails, admit, place, or queue does not count. P0. Evidence: `edge` is the only LLM URL in the app config (`app/config.py`, `edge_url`). `app/tests/test_agent_flow.py` checks the headers of each call.

## D. Part 1: capacity on paper (L573 to L588)

- [ ] **H-35** (L575) Use the GPU that we run, not a brochure SKU. P0. Evidence: `docs/spec/05-capacity-plan.md`.
- [ ] **H-36** (L580) Compute `max_concurrent_seqs ~= (HBM - weights - activations) / (kv_bytes_per_token x max_len)`. P0. Evidence: `tools/capacity.py` output.
- [ ] **H-37** (L584) Compute it at `max_len`. P0. Evidence: capacity table.
- [ ] **H-38** (L584) Compute it at the length that our app sends. Use measured lengths from app traces. P0. Evidence: token-length histogram from traces.
- [ ] **H-39** (L586) Show `kv_bytes_per_token`. For hybrid models, also show fixed bytes for each sequence. P0. Evidence: capacity table.
- [ ] **H-40** (L586) If we switch the model, say what the switch did to bytes per token. P0. Evidence: comparison table (Gemma 4 31B against Muse Glimmer 30B), `05-capacity-plan.md` section 5.
- [ ] **H-41** (L588) State which limiter we expect first: weights, KV, compute, interconnect, or scheduler. P0. Evidence: hypothesis in `docs/spec/05-capacity-plan.md`.
- [ ] **H-42** (L588) In the presentation, say if the hypothesis was correct. P0. Evidence: notebook result cell, slide.

## E. Part 2: cluster design (L590 to L628)

- [ ] **H-43** (L595) GPU: say what we ran, its HBM, and why not a smaller or cheaper GPU. P0. Evidence: `docs/decisions/ADR-004-gpu-and-credits.md`.
- [ ] **H-44** (L598) Model: compare parameters, KV, and the app length. P0. Evidence: `docs/decisions/ADR-003-model.md`.
- [ ] **H-45** (L601) Topology: two or three colocated replicas, or a prefill/decode split. Say what we used to slice and why. P0. Evidence: topology experiment E3, ADR-005.
- [ ] **H-46** (L604) Concurrency: give `max_num_seqs` and the maximum length that the engine permits. P0. Evidence: engine args, capacity table.
- [ ] **H-47** (L607) Hop backend: Mooncake, or a named backend that we implement. P0. Evidence: ADR-002 revision 2 (the LMCache tier is the hop and the CPU tier, and E4 compares it with the NIXL hop), hop records.
- [ ] **H-48** (L610) Overflow: say who receives a 503 or a 529. Name the model and the reason. "Another API" is not an answer. P0. Evidence: ADR-008.
- [ ] **H-49** (L612) Use at least two workers. P0. Evidence: pod list, `orch_replica_*` series.
- [ ] **H-50** (L614) Show the gateway and the engine as two different boxes. P0. Evidence: architecture diagram.
- [ ] **H-51** (L617) Admit, place, and queue are in the gateway. P0. Evidence: admission control + routing (`edge`, the Agent Router, and the llm-d router), with our config in `control/router/`.
- [ ] **H-52** (L620) vLLM is the engine (waiting queue, block table, preemption, kernels). P0. Evidence: diagram, `DESIGN.md`.
- [ ] **H-53** (L622 to L624) If we scale, say which pool and why. Uncached prefill tokens go to the prefill pool. Hot decode slots go to the decode pool. Do not say "add a replica". P0. Evidence: planner rules, KEDA ScaledObjects, scale experiment E9 (recorded offline).
- [ ] **H-54** (L626) Keep live engine metrics. The vendor choice is free. P0. Evidence: Prometheus targets page capture.
- [ ] **H-55** (L628) Bind the app and the control plane to localhost or to the cluster network. Do not put the engine on a public IP. P0. Evidence: Service types, SSH tunnel config, firewall capture.

## F. Part 3: guardrails, admit, stay or leave (L630 to L638)

- [ ] **H-56** (L635) Implement `inspect(payload) -> Guard`. P0. Evidence: `control/edge/guard.py`, `control/guard/`, tests.
- [ ] **H-57** (L636) Implement `should_shed(req, snap) -> (shed, code, reason, retry_after_seconds)`. P0. Evidence: the rule table in `04-system-design.md` section 6.2, `control/router/`, `control/edge/admit.py`, tests.

## G. Part 4: place (L640 to L651)

- [ ] **H-58** (L645) Implement `pick(req, workers, *, policy) -> Worker | Shed`. P0. Evidence: llm-d scheduling profiles in `control/router/`.
- [ ] **H-59** (L649) Support the policies `least_loaded`, `p2c`, and `prefix_then_load`. P0. Evidence: a router config for `prefix_then_load`, `least_loaded`, and `random`. The router has no p2c picker, and `weighted-random-picker` is the nearest. E3.
- [ ] **H-60** (L649) Use different scorers for prefill and decode where it helps. P0. Evidence: the prefill profile and the decode profile in `control/router/`.
- [ ] **H-61** (L649) Use queue depth as a scorer, not only as an admit input. P0. Evidence: `queue-scorer` in both profiles.
- [ ] **H-62** (L651) Do not bounce a request between workers. If all workers shed the request, return `Shed(503, retry_after=2)`. P0. Evidence: no retries on the Agent Router route, `edge` returns 503, integration test.

## H. Part 5: queue (L653 to L691)

- [ ] **H-63** (L658) Implement the order admit, place, our queue, then the engine waiting, running, and preempted states. P0. Evidence: llm-d flow control, then the vLLM states, in the notebook.
- [ ] **H-64** (L662) Answer each question below with a scrape in a notebook. P0. Evidence: `notebook/part5_queue.ipynb`.
- [ ] **H-65** (L665) Who sits in our queue and who sits in the vLLM waiting queue? P0.
- [ ] **H-66** (L668) Show waiting, running, and preempted counts (V1 has no swap). P0.
- [ ] **H-67** (L671) Show `orch_replica_queue_depth` for each pod under each traffic mix. P0.
- [ ] **H-68** (L674) If a 32k RAG retrieve and a short agent decode are both ready, who goes first: our queue or the engine? P0.
- [ ] **H-69** (L677) PagedAttention packs KV. Prefix cache reuses KV. Which one saved memory on our shared-prefix mix? P0.
- [ ] **H-70** (L680) Give the engine flags for chunked prefill and continuous batching (`max-num-batched-tokens`, `max-num-seqs`) and the reason for each value. P0.
- [ ] **H-71** (L683) If the KV is full after admit, do we shed at the door or does the engine preempt? P0.
- [ ] **H-72** (L686) If the client is gone (aborted), who frees the KV and how? P0.
- [ ] **H-73** (L689) After a worker returns, do we send 100% at once, or do we ramp while p99 holds? P0.
- [ ] **H-74** (L691) Do not write our own engine. P0. Evidence: same as H-25.

## I. Part 6: hop and warmth (L693 to L703)

- [ ] **H-75** (L695) Use two workers. Prefill can run on A and decode on B. The KV must move to B, or B computes the prompt again. P0. Evidence: P/D pods, NIXL metrics.
- [ ] **H-76** (L698) If `src == dst`, do nothing. P0. Evidence: router P/D decisions and hop records, E4.
- [ ] **H-77** (L701) If `src != dst`, record a hop with source, destination, token count, prefix, and backend. P0. Evidence: `metrics/<run-id>/hops.jsonl` from the Envoy access log (`tools/hop_records.py`), `vllm:nixl_*`.
- [ ] **H-78** (L703) Answer the question: is the new replica warm? Measure the first RAG token and the first agent step after a hop to a new decode replica. Find the part of the delay that comes from warmup work that was still left. P0. Evidence: experiment E8.
- [ ] **H-79** (L703) Warm the box. Then give the TTFT again. P0. Evidence: `plots/warmup-ttft.png` (`notebook/proof.py`, `warmup_ttft`).

## J. Part 7: wire the app to the cluster (L705 to L726)

- [ ] **H-80** (L710 to L722) Wire this order: app turn, `guardrails.inspect`, admit (tenant window), place (`pick`), queue (same pod or `transfer(src, dst)`), engine `/v1/chat/completions`, overflow gate. P0. Evidence: sequence diagram, code path.
- [ ] **H-81** (L714) Admit with a tenant window. P0. Evidence: Agent Router token limit for each tenant.
- [ ] **H-82** (L726) Smoke the engine before we debug our serve path. P0. Evidence: `cluster/smoke/smoke.sh` output (step 1 is the engine smoke).
- [ ] **H-83** (L726) Send completions through our serve path. P0. Evidence: same as H-34.

## K. Part 8: proof and dashboards (L728 to L756)

- [ ] **H-84** (L730) Drive the cluster with traffic that our app makes. P0. Evidence: trace files from the app, replayer.
- [ ] **H-85** (L730) Use the Class 7 mixes if useful (unique, shared-prefix, stale telemetry, mixed interactive and batch). The requests must look like RAG retrieves or agent steps. P0. Evidence: `app/loadgen/scripts.py` (`mix_picker`), `app/loadgen/replay.py`.
- [ ] **H-86** (L732) In the presentation, walk through the code and the choices. P0.
- [ ] **H-87** (L735) Dashboard: Cluster. P0.
- [ ] **H-88** (L738) Dashboard: Success and failures. P0.
- [ ] **H-89** (L741) Dashboard: Gateway and admission, with sheds by reason (`tenant_tokens`, `timeout_queue`, `kv_free`, and the other reasons). P0.
- [ ] **H-90** (L744) Dashboard: Router. P0.
- [ ] **H-91** (L747) Dashboard: Queue depth by pod. P0.
- [ ] **H-92** (L750) Dashboard: vLLM. P0.
- [ ] **H-93** (L753) Dashboard: Mooncake KV, or our hop store. P0. Evidence: the "Hop store" dashboard (NIXL and LMCache).
- [ ] **H-94** (L756) Dashboard: Pods, replicas, and KEDA. If we scale, show which pool. P0.

## L. Part 8: point at a file or a scrape (L758 to L797)

Each answer goes in `DESIGN.md` with a file path and line, or with a pasted scrape.

- [ ] **H-95** (L761) What is the app? Which tokens do all requests share, and which tokens are unique? P0.
- [ ] **H-96** (L764) What dies at guardrails, at admit, at place, and at queue? P0.
- [ ] **H-97** (L767) Where do we stop work that will time out (`timeout_queue`)? P0.
- [ ] **H-98** (L770) Where do we protect KV? P0.
- [ ] **H-99** (L773) Where do we give priority to interactive traffic (`p99_spread`)? P0.
- [ ] **H-100** (L776) Where do we stop one tenant that tries to take all of the GPU? P0.
- [ ] **H-101** (L779) Where do we hop, and what is not copied? P0.
- [ ] **H-102** (L782) Where do we evict, and what becomes a ghost if we skip the eviction? P0.
- [ ] **H-103** (L785) Where does the engine scheduler sit, compared to our admit, place, and queue? P0.
- [ ] **H-104** (L788) What limited concurrency on this GPU for this app? P0.
- [ ] **H-105** (L791) Give four production alerts. P0.
- [ ] **H-106** (L794) If we scale, which pool: prefill tokens or decode slots? P0.
- [ ] **H-107** (L797) What do we change at 10 times the traffic? Which three knobs are the wrong next move? P0.

## M. Part 8: bad answers that we must not give (L799 to L832)

- [ ] **H-108** (L802) Do not say "`bench latency` batch=8 is the production SLO". Our SLO comes from the app traffic.
- [ ] **H-109** (L805) Do not say "Cache is full, so add another replica of the same size".
- [ ] **H-110** (L808) Do not say "NCCL/NIXL in this repo moves KV tensors". Say exactly which process moves the bytes (the vLLM NixlConnector) and which process only records the hop (our hop records).
- [ ] **H-111** (L811) Do not say "A replica is ready when the weights are on the GPU". Use our "declare warm" rule.
- [ ] **H-112** (L814) Do not name Superlinked or another API as the overflow without the model and the limiter.
- [ ] **H-113** (L817) Do not say "I wrote my own version of the vLLM scheduler in the gateway".
- [ ] **H-114** (L820) Do not send a 429 to overflow.
- [ ] **H-115** (L823) Do not say "The gateway fixed OOM". Admission reduces load. It does not fix engine memory.
- [ ] **H-116** (L826) Do not say "RAG is a third phase". RAG is a prefill-heavy request shape.
- [ ] **H-117** (L829) Do not compare wall-clock seconds across models without token counts.
- [ ] **H-118** (L832) Do not quote TTFT from a cold replica as the SLO.

## N. Submission (L834 to L847)

- [ ] **H-119** (L834) Submit a GitHub link and the notebook from Part 5. P0.
- [ ] **H-120** (L839) `app/`: the RAG or agent app. It talks only to our serve path. P0.
- [ ] **H-121** (L840) `control/`: guard, admit, place, queue, hop (any layout). P0. Evidence: `control/edge/`, `control/guard/`, `control/router/`, `control/warm/`.
- [ ] **H-122** (L841) `cluster/`: how the engine comes up. P0.
- [ ] **H-123** (L842) `DESIGN.md`: the questions above, with pasted scrapes. P0.
- [ ] **H-124** (L843) `plots/`: soak, shared-prefix KV, or queue depth, from the cluster. P0.
- [ ] **H-125** (L844) `metrics/`: scrape, hop dict, evict count, 429 and 503 counts. P0.
- [ ] **H-126** (L845) `notebook/`. P0.
- [ ] **H-127** (L512) Submit before the due date: Saturday 2026-10-10 (moved from 2026-10-03). P0.
- [ ] **H-128** (L849 to L853) Use the Maven page: add the submission, post it to the project channel, and select "Submit project". P0.

## O. Asks from the owner in this session

- [ ] **U-01** Write all session text, the report, and the slides in ASD-STE100. P0. Evidence: `tools/ste_lint.py` output with zero errors.
- [ ] **U-02** Keep this checklist complete. Review it with the owner at the end. P0.
- [ ] **U-03** Build one app for Track A and Track B: an agentic RAG learning companion over the Notion Bookmarks database. P0.
- [ ] **U-04** Give a chat UI (Streamlit or another framework). P0.
- [ ] **U-05** Fetch, chunk, and embed the bookmark documents into a self-hosted vector database. P0.
- [ ] **U-06** Add a fact-check phase with a live web query. Define exactly when the live result wins. P0. Evidence: rule F2 (ADR-012), unit tests.
- [ ] **U-07** Summarize and output a factual answer with citations. P0.
- [ ] **U-08** Let the system read browser screenshots (vision and OCR) to help the agent. P0 for one path, P1 for both paths.
- [ ] **U-09** Pick an LLM that does tool calls and RAG well. Base the choice on research and on a measured gate. P0. Evidence: ADR-003, Gate G1.
- [ ] **U-10** Use a production-quality technology stack. P0.
- [ ] **U-11** Research vLLM, SGLang, NVIDIA Dynamo, Mooncake, LMCache, HAMi, Kubernetes, and KEDA. Record the facts with sources. P0 (done 2026-09-26, and 2026-09-27 for llm-d, NeMo Guardrails, LMCache, and the new models). Evidence: `docs/spec/02-research-findings.md`.
- [ ] **U-12** Use the credits well: Lambda (400 USD), Modal (amount not known), Superlinked (500 USD). P0. Evidence: `docs/spec/07-plan-and-budget.md`, spend ledger.
- [ ] **U-13** Follow a spec-driven process. Write the spec first. Implement from the spec. P0.
- [ ] **U-14** Write a report and slides. P0.
- [ ] **U-15** Read all course code and pull the latest `class-code`. P0 (done 2026-09-26, commit `bf42b32`).
- [ ] **U-16** Debate the disputed points one at a time (1, 2, 3, 5, 6, 7, 12). P0 (done 2026-09-27). Evidence: `docs/decisions/DEBATE-LOG.md`.
- [ ] **U-17** Build a production system, not a demo: production components and production operations. P0. Evidence: ADR-001, ADR-010.
- [ ] **U-18** Use a prompt-injection classifier. P0. Evidence: ADR-011, E17.
- [ ] **U-19** Run the guard models on a separate, lower GPU, not on a serving GPU. P0. Evidence: node 2 GPU 1, ADR-011.
- [ ] **U-20** Show the scale, recorded offline. P0. Evidence: E9 plots and videos.
- [ ] **U-21** Show charts in the talk. A live demo is optional. P0. Evidence: `plots/`, videos.
- [ ] **U-22** Make a question bank: the demo questions, the load prompts, and the questions for the talk. P0. Evidence: `08-question-bank.md`.
- [ ] **U-23** Build the app agent with LangChain and LangGraph Deep Agents. P0. Evidence: ADR-006, `app/agent/build.py`, `app/tests/test_agent_flow.py`.

## P. Guidance from the Class 10 revision notes that we adopt

These items are not asks in the handout. They come from the Class 10 revision notes, and they make the design stronger.

- [ ] **R-01** Do not split prefill and decode on one sliced GPU. A split needs a second GPU. P0.
- [ ] **R-02** Keep one front door. The UI never talks to vLLM directly. P0.
- [ ] **R-03** Do not swallow errors from the hop store. Count them and alert on them. P0.
- [ ] **R-04** Prevent the ghost cache. When the engine evicts a prefix, the router must learn about it. P0.
- [ ] **R-05** Treat warmup as a budget. Record who pays for the work that we skip. P0.
- [ ] **R-06** Put a bound on affinity (bounded-load prefix routing) and add hysteresis to each threshold. P1.
- [ ] **R-07** Couple the prefill and decode scaling loops. Let decode scale first. P1.
- [ ] **R-08** Meter what is scarce (KV bytes, batch rows, prefill share), not only tokens for each minute. P1.
- [ ] **R-09** Split the TTFT into spans with plane labels (data, control, GPU). P1.
- [ ] **R-10** Add a determinism canary to find silent corruption. P1.
- [ ] **R-11** Make cancellation a state machine and include tool-wait time in the agent SLO. P1.
