# Debate log

The owner disputes points 1, 2, 3, 5, 6, 7, and 12 of the first spec (2026-09-26). We debate them one at a time. Each settled result goes here first. After the debate, we change the spec files and the ADRs in one pass.

## Point 12: scope (closed)

| Question | Result | Date |
|---|---|---|
| What does "production system" mean on 2026-10-03? | (a) all app features in the spec, including P1. (b) Production operations. Not (c) access for other people. (d) was first yes, then dropped (see next row). | 2026-09-26 |
| Which levers pay for the extra work? | Claude does all engineering work. Skip evals: no eval job and no quality target. Skip security hardening. | 2026-09-26 |
| What replaces the evals? | A small set of demo questions that Claude picks. Each question must work from end to end (retrieve, fact check, cited answer). This set is an acceptance test, not a scored eval. The same set is the live demo in the presentation. Changed on 2026-09-27 (point 6): the talk shows recordings and charts, and a live demo is optional. | 2026-09-26 |
| Gateway replicas: one active and one standby (H1), or two active (H2)? | H2: two active replicas. Redis holds the shared state (tenant buckets, prefix index, dispatch credits). The design must be production ready. | 2026-09-26 |
| One node (N1) or two nodes (N2)? | N2. Node 1: 2 x H100 SXM, only the vLLM pods. Node 2: 1 x A6000 48 GB (not an A10, because the A6000 costs less and has more memory): gateways, Redis, app, databases, monitoring, and SIE on HAMi slices. **VERIFY**: both shapes in stock in one region, the A6000 price, and a network path between the nodes. Changed on 2026-09-27 (Guardrails): node 2 is now 2 x A6000. | 2026-09-26 |
| Use the Lambda managed cluster product? | No. Lambda Managed Kubernetes exists only on 1-Click Clusters: 16 GPUs minimum, 2 weeks minimum, H100 at 6.16 USD for each GPU-hour at 16 GPUs. The minimum cost is about 33,000 USD. We run our own k3s on two on-demand instances. The k3s server runs on node 2, because GPU nodes come and go. | 2026-09-26 |
| Always on (O2) or on demand (O1)? | O1. Both nodes come up only for a block of work or a test, and then we shut them down. A scripted bring-up and a scripted teardown are necessary. The state must survive between sessions. | 2026-09-26 |
| Where does the state live between sessions? | S3: a Lambda persistent filesystem (weights, vLLM compile cache, backups) and a second copy of each backup on the owner's laptop. Live database files stay on the local disk. Bring-up restores, and teardown backs up. If the filesystem region has no stock for our shapes, that session uses the laptop copy only. Redis starts empty each session. | 2026-09-26 |
| How do CI and CD work? | C3. CI in GitHub Actions: lint, type check, unit and contract tests, image build, push to GHCR. CD: `make deploy` from the owner's laptop through the SSH tunnel. GPU smoke tests run on the cluster after each deploy. GitOps can come later. | 2026-09-26 |
| Do we need GitHub Actions? | No. Dropped. `make deploy` runs the tests on the laptop, copies the code to node 2, builds our two images (`gateway`, `app`) on node 2, loads them into k3s, applies the manifests, and runs the smoke tests. We pull all other images. No registry, no registry tokens. | 2026-09-26 |

Notes:

1. "Skip evals" removes the offline eval job and the quality target. It does not remove the Gate G1 tests (tool calls, screenshots, prefix-cache correctness). Those tests check that the engine works. They do not score answer quality.
2. "Skip security hardening" removes NetworkPolicies, Pod Security Standards, non-root images, and encrypted secrets in Git. Two basic rules stay:
   - Bind the app, the gateway, and the engine to localhost or to the cluster network. The handout asks for this (L628, H-55).
   - Never commit a secret (NFR-01).
3. The owner still does the actions that Claude cannot do: accounts, API keys, payment details, and approval of each paid GPU launch (A1 to A7).
4. Batch traffic still exists without the eval job: the index sync (Track A batch) and the freshness sweep (Track B batch).

## Point 2: engine (closed)

| Question | Result | Date |
|---|---|---|
| Is vLLM a hard requirement? | No. The handout permits any provider. Live engine metrics are necessary (L626). | 2026-09-26 |
| E2 (Dynamo with vLLM workers) or E3 (Dynamo with TensorRT-LLM workers)? | E2: Dynamo 1.4.0 with vLLM v0.26.0 workers. Reasons: all `vllm:*` metrics pass through (E3 lacks waiting, running, prefix-cache hits, preemptions, and inter-token latency), Gemma 4 support is mature in vLLM, and NIXL supports P/D with sliding windows. Our gateway keeps guard, admit, place, and queue. The Dynamo frontend runs in `--router-mode direct` and carries out our choice of prefill and decode workers (`x-dynamo-prefill-instance-id`, `x-dynamo-worker-instance-id`). | 2026-09-26 |
| Integration defaults for Dynamo (checked against the handout and the Dynamo docs, because the owner asked Claude to verify them) | 1. Frontend: a sidecar in each worker pod with `--router-mode direct`. This is the documented layout for an external endpoint picker. Our gateway takes the endpoint-picker role and sends each request to the chosen worker pod. 2. Admission: only our gateway. The Dynamo busy thresholds are off by default, and we keep them off. 3. Autoscaling: KEDA scales each service through its `DynamoGraphDeploymentScalingAdapter`. We do not deploy the Dynamo Planner. One autoscaler for each service. 4. Discovery and transport: `DYN_DISCOVERY_BACKEND=kubernetes`, TCP request plane and ZMQ event plane (both defaults). No etcd, no NATS. Our gateway watches `DynamoWorkerMetadata` and EndpointSlices with a read-only service account. 5. Priority goes to vLLM through `nvext.agent_hints.priority`. Dynamo cancels a request in all workers when the client disconnects. | 2026-09-27 |

Still to verify at Gate G0 (point 2):

- The exact instance-id value in `DynamoWorkerMetadata` and the header flow for a split request (which sidecar receives it).
- The worker flags for prefill and decode in `python -m dynamo.vllm --help`.
- The image tag `nvcr.io/nvidia/ai-dynamo/vllm-runtime` for Dynamo 1.4.0.
- The priority range and polarity of `nvext.agent_hints.priority`.
- A client disconnect frees the KV on the decode worker, and the lease frees it on the prefill worker.

| Split the gateway? Write it by hand? | A router decides place (the owner). We do not write the gateway by hand, because the handout permits existing gateways (L626). The handout still asks us to own the decisions (L517, L553, Parts 3 and 4, L840). Direction: a production edge gateway (Envoy AI Gateway) and a production router (the Dynamo endpoint picker, which is a GAIE endpoint picker). Our policies live in them as configuration and small plugins. Our own code keeps the guard, the warm controller, the Go plugins, and the KEDA triggers. | 2026-09-27 |

Risks to check before we pick versions (point 2, direction above):

1. Does the Dynamo endpoint picker include the standard GAIE scorers and flow control?
2. Flow control rejects a full queue with 429. Our code rules need 503 or 529 for a full fleet.
3. Custom plugins must be in Go, in a custom image of the endpoint picker.
4. The AI Gateway fallback must act only on 503 and 529, never on 429.
5. Install order and version fit on k3s: Gateway API, GAIE, Envoy Gateway, Envoy AI Gateway, Dynamo operator.

| Risk 1 result: can the Dynamo endpoint picker host our policies? | No, not on the current path. From `dynamo-frontend:1.5.0` the picker is a native Rust program with only environment-variable configuration. The plugin chain (`eppConfig`) is legacy and pinned to the 1.4 line. So Dynamo's own algorithm decides place. | 2026-09-27 |
| E5 (llm-d) or E2 (Dynamo)? | **E5.** vLLM v0.30 as the engine, the llm-d router as the production router (endpoint picker with flow control and plugins), the Envoy AI Gateway as the edge proxy, and KEDA on the prefill and decode Deployments. This replaces E2 and the Dynamo defaults above. We lose KVBM, so point 3 changes. | 2026-09-27 |

Risks to check for E5:

1. Flow control rejects a full queue with 429. Our code rules need 503 or 529 for a full fleet.
2. The Envoy AI Gateway fallback must act only on 503 and 529.
3. Router v0.11.0 is new and removed some plugins. Pin it only after a smoke test, else use v0.10.
4. Install order and version fit on k3s: Gateway API, GAIE, Envoy Gateway, Envoy AI Gateway, llm-d charts.

Results of the E5 risk check (2026-09-27):

1. Codes: since llm-d v0.9, the router rejects capacity back-pressure, queue TTL expiry, and eviction with 429. It uses 503 for no endpoints, client disconnect, and shutdown. The header `x-llm-d-request-dropped-reason` gives the reason. So a router 429 is a capacity signal, not a tenant limit.
2. Fallback: the Envoy AI Gateway is now named Agent Router (v1.1). Its fallback runs through the retry policy, which needs the trigger `retriable-status-codes`. A retry policy acts on upstream responses. A reject from the endpoint picker is a local reply, so the fallback cannot act on it (**VERIFY** at Gate G0). The docs show pools and external backends in different rules of one route, not as a fallback pair in one rule.
3. Versions: llm-d documents an Agent Router path with Envoy Gateway v1.8.1, the token rate-limit add-on, and the InferencePool add-on. Kubernetes 1.32 or later is necessary. GAIE v1.0.x. Router v0.11.0 is new. We pin it only after a smoke test.
4. Install order from the llm-d guide: CRDs, Envoy Gateway with the AI Gateway values, and the gateway recipe. Then a model-server guide adds the vLLM pods, the InferencePool, the endpoint picker, and the HTTPRoute.

| A thin `edge` service of our own? | Yes. `edge` does guard, stay or leave, streaming pass-through, and shed metrics. It does not admit, place, or queue. It sits in front of the Agent Router. A router capacity reject (429 with `x-llm-d-request-dropped-reason`) may leave to the overflow if the request is interactive, the privacy switch is on, and the limiter permits it. Else the client gets 503 with the reason. A tenant 429 from the Agent Router always stays. | 2026-09-27 |

We close point 2 here.

## Guardrails (closed)

| Question | Result | Date |
|---|---|---|
| How do guardrails work? | `inspect(payload) -> Guard` runs in `edge`, before the Agent Router. Rules on the CPU: model allow list, tenant and class headers, `max_tokens` clamp, empty prompt, prompt tokens (model tokenizer and chat template) up to 30,000, images, tool schemas. A rejected request never reaches the proxy, the router, or a GPU. | 2026-09-27 |
| Is a prompt-injection classifier necessary on October 3? | Yes, it is a requirement (P0). Model: `meta-llama/Llama-Prompt-Guard-2-86M` (injection and jailbreak, many languages) on the CPU. `edge` checks the user message and rejects with 400 `prompt_injection`. The app checks each fetched page and OCR text in 512-token windows, and removes or marks the suspect windows before the text enters a prompt. The other defenses stay: data markers in the prompt, the fetch allow list, and no tool calls from page text. Fallback model if the license is a problem: `protectai/deberta-v3-base-prompt-injection-v2` (English only, no jailbreak detection). | 2026-09-27 |
| May the guard models use a GPU? | Yes (the owner). They run on a separate, lower GPU. It is not a serving GPU. The CPU rules stay first, because the handout calls the guard the first no that never reaches a GPU (L536). Open: where the guard GPU lives (G1, G2, or G3) and the list of models. | 2026-09-27 |
| Where does the guard GPU live? | G2 (the owner). Node 2 becomes 2 x A6000 48 GB. GPU 0 runs SIE. GPU 1 runs the guard models. The extra cost is 1.09 USD for each node 2 hour, about 35 USD this week. Node 2 gets 28 vCPUs and 200 GiB RAM. On dev days, a dev model can also run on GPU 1. **VERIFY**: stock for 2 x A6000 in the region of the H100 node. | 2026-09-27 |
| Which guard design? | Two stages (Claude proposed it with G2, and the owner did not object). Stage 1: the rules on the CPU in `edge`. This is the first no, and a request that fails here reaches no GPU. Stage 2: the model checks on the guard GPU, through the NeMo Guardrails 0.24.1 server (`guard` pod on the node 2 CPU, endpoint `/v1/checks`). Models: `meta-llama/Llama-Prompt-Guard-2-86M` (injection and jailbreak) and `nvidia/Nemotron-3.5-Content-Safety` (4B, vLLM, fast mode, harm in text and images). `edge` sends the user text and images. The app sends fetched pages and OCR text in 512-token windows. Redis keeps each verdict by content hash, so later agent steps of one turn do not run the models again. The guard fails closed: a timeout or an error gives 503 `guard_unavailable`, and that request never leaves to the overflow. | 2026-09-27 |
| Which NeMo parts do we not use? | The jailbreak heuristics and JailbreakDetect (they let the request through when the detector is down). The self-check rails (they call the serving model on the H100s). NemoGuard 8B Topic Control (too large for the job, and the 4B model accepts custom rules). | 2026-09-27 |

Notes:

1. Hugging Face gates the Llama Prompt Guard 2 repository under the Llama 4 Community License. The owner must accept the license with their Hugging Face account (new action A8).
2. The classifier is a partial defense. A public benchmark tests it on indirect injections in agent tool outputs. We tune the threshold on a small set of real pages and injected pages, and we record the false-positive rate.
3. This section changes the N2 row of point 12: node 2 is now 2 x A6000.
5. Change on 2026-09-27 (Claude, from the NeMo 0.24.1 source): Prompt Guard 2 runs in its own classifier service (`guard-injection`), and `edge` calls it in parallel with NeMo. A NeMo custom action forces the slow LLMRails engine, because IORails does not run custom actions.
4. Gate G0 checks for the guard:
   - The guard latency on GPU 1 (p50 and p95) for text, and for text with one image.
   - NeMo sends the image to Nemotron 3.5 through the content-safety rail.
   - Nemotron 3.5 runs on our vLLM image. If not, the guard pod pins vLLM 0.20.2.
   - The server for Prompt Guard 2 on GPU 1: the vLLM classify runner, or a small FastAPI service.

## Point 3: KV layer (closed)

| Question | Result | Date |
|---|---|---|
| How does the KV move between prefill and decode? | No change: the vLLM `NixlConnector`, as in the llm-d P/D path. Our hop backend name is `nixl`. | 2026-09-27 |
| Which KV tier sits behind the GPU prefix cache? | K2 (the owner chose it and asked Claude to own the decision). LMCache in multiprocess (MP) mode: one `lmcache server` process on node 1, a CPU tier of 150 GiB, LRU eviction. The vLLM pods connect through `LMCacheMPConnector` from the `lmcache` package. Each pod joins it to `NixlConnector` through `MultiConnector`. The tier is outside the vLLM pods, so it stays when a pod restarts, and all pods on node 1 share it. | 2026-09-27 |
| Why not the vLLM native CPU offload (K1)? | Gemma 4 does not start with it unless HMA stays on. With HMA on, vLLM issue 56871 divides the CPU capacity by the number of KV groups, and the fix is not merged. | 2026-09-27 |
| Why not Mooncake Store (K3)? | No Gemma 4 test, more services, and no RDMA on Lambda. Over TCP from another node, a load can be slower than a new prefill. | 2026-09-27 |
| Source check after the choice (Claude, vLLM v0.30.0 and LMCache source) | 1. vLLM v0.30 loads `LMCacheMPConnector` from the `lmcache` package when the package has it. That class supports HMA in lmcache 0.5.5 and 0.5.6rc1. The fallback class inside vLLM does not support HMA. 2. `MultiConnector` keeps HMA on only if all child connectors support it. `NixlConnector` supports it. 3. The in-process `LMCacheConnectorV1` does not support HMA. llm-d tests this in-process path in its CI, so that path cannot run Gemma 4. We use MP mode, which LMCache tested with Gemma 4 31B and llm-d does not test. 4. llm-d main pins the image `vllm/vllm-openai:v0.26.0`. We plan v0.30.0. The G0 smoke test decides. 5. The vLLM image installs `lmcache >= 0.3.9` and `nixl == 1.4.1` when it is built with the KV connectors. | 2026-09-27 |

Gate G1 checks for K2:

1. The vLLM v0.30 image has `lmcache` 0.5.5 or later. If not, we add it in our own image layer.
2. Gemma 4 starts with `MultiConnector` (NIXL and LMCache MP) and HMA on.
3. A prefix that left the GPU comes back from the CPU tier. The LMCache hit metrics go up, and the TTFT is lower than a cold prefill.
4. After a vLLM pod restart, the first request hits the CPU tier.
5. The pod settings for the LMCache server on node 1 work (a DaemonSet or the LMCache operator, and the IPC settings).
6. If a check fails, we use K0 (GPU prefix cache only), and `DESIGN.md` gives the reason.

Notes:

1. Experiment E16 changes from a Mooncake Store pool to the LMCache tier for agent sessions.
2. We drop experiment E17 (NIXL compared with MooncakeConnector). There is no Gemma 4 test for Mooncake, and we have no time.
3. The "Hop store" dashboard shows the NIXL metrics, our hop records, and the LMCache metrics.
4. lmcache 0.5.6rc1 adds KV events for the MP connector. The router can then score the CPU tier. With one GPU node, all pods share one tier, so we leave this for later.

## Point 7: topology (closed)

| Question | Result | Date |
|---|---|---|
| How do we use the two H100 GPUs on node 1? | C (the owner). One prefill pod on GPU 0 and one decode pod on GPU 1. Each pod has a full GPU, TP 1, and no HAMi. The llm-d `disagg-profile-handler` with the `prefix-based-pd-decider` splits a request only when 2,048 or more of its tokens are not cached on the decode pod (start value, experiment E5 tunes it). Short or cached requests run on the decode pod only (same pod, no hop). Long uncached prompts go through the prefill pod and a NIXL hop (two pod IDs). | 2026-09-27 |
| Do we still compare layouts? | Yes. Experiment E3 measures option A (two replicas, each with prefill and decode) against option C on the M4 mix at three loads. `DESIGN.md` reports the numbers. | 2026-09-27 |
| What about scale and development? | T4 (4 x H100) stays for one KEDA session. On dev days only node 2 runs: `gemma-4-E4B-it` runs a prefill pod on GPU 0 and a decode pod on GPU 1, on HAMi slices next to SIE and the guard. This tests NIXL and LMCache across two GPUs before the H100 sessions. | 2026-09-27 |

Notes:

1. Option C replaces the three route modes of ADR-005. The router makes the split decision, so our gateway code for route modes goes away.
2. **VERIFY** at Gate G0: the source of each split decision for our hop records. A hop record has the source pod, the destination pod, the tokens, the prefix, and the backend. Possible sources: the router metrics, the routing sidecar logs, or the NIXL metrics.

## Point 1: model (closed)

| Question | Result | Date |
|---|---|---|
| Which model do we serve? | M1 (the owner). Main model: `RedHatAI/gemma-4-31B-it-FP8-dynamic`. Challenger at Gate G1: `meta-models/Muse-Glimmer-30B` (online FP8). Dev model: `google/gemma-4-E4B-it`. | 2026-09-27 |
| When does the challenger win? | G1 tests tool calls, screenshots, prefix-cache correctness, P/D startup with LMCache, and TTFT. If Gemma 4 fails a test that Glimmer passes, Glimmer becomes the main model. If both pass all tests, Glimmer wins only with a pass rate on our tool-call tests that is at least 10 points higher. | 2026-09-27 |
| Which models did we drop? | Qwen3.8-27B (open vLLM bugs 55766, 43587, and 37729 hit the prefix cache and FP8 under load). Nemotron 3.5 Lightning (text only). Gemma 4 26B-A4B (lower quality, same mixed head sizes). The FP8-block checkpoint of Gemma 4 (issue 39407 closed as stale, not fixed). | 2026-09-27 |
| How do we protect the Gemma 4 path? | The same `--block-size` on the prefill and decode pods (bug 52234). The `gemma4` tool parser and reasoning parser, always together (bugs 57231, 57232). No MTP (bug 54926). A health check that looks for progress, not only for a live process (bug 53130). | 2026-09-27 |

Notes:

1. G1 now needs two H100 GPUs, for the P/D and LMCache tests. Point 6 prices this.
2. If Glimmer wins, the dev model has no small model of the same family. Then the dev days test the app with Gemma 4 E4B, and G1 retests the tool calls with Glimmer.

## Point 6: GPUs and budget (closed)

| Question | Result | Date |
|---|---|---|
| Which GPU for the engine? | H100 SXM (no change). GH200 is cheaper, but it has an ARM CPU and only one GPU for each instance, so the P/D hop must cross the network (about 1 s for an 8K Gemma 4 hop at 10 Gb/s). | 2026-09-27 |
| Which budget plan? | P1 (the owner). Keep all blocks, including the scale session on 4 x H100. Planned work: about 237 USD. The stop for work stays at 320 USD, so about 83 USD stays free for reruns and mistakes. | 2026-09-27 |
| Do we show the scale? | Yes. The scale session (E9) runs one scale-out for each pool: a RAG mix with long uncached prompts scales the prefill pool, and an agent mix with many streams scales the decode pool. We record it offline (the owner): plots, scrapes, and a screen recording of the "Pods / replicas / KEDA" dashboard. | 2026-09-27 |
| Is a live demo necessary in the talk? | Not necessarily (the owner). The talk must show charts. We record the demo questions from end to end in session 2 as a screen recording. A live demo is optional. Its cost (about 32 USD for 3 h on both nodes) comes from the 80 USD reserve only if we decide to do it. | 2026-09-27 |

Budget by block (Lambda prices on 2026-09-27). Node 2 is 2 x A6000 at 2.18 USD for each hour in every block.

| Block | Hours | USD |
|---|---|---|
| G0 and three dev days (node 2 only) | 16 | 35 |
| Gate G1 (2 x H100 at 8.38 USD for each hour, and node 2) | 3 | 32 |
| Measurement sessions 1 and 2 (2 x H100 and node 2) | 11 | 116 |
| Scale session (4 x H100 at 16.36 USD for each hour, and node 2) | 2.5 | 46 |
| Filesystem (150 GB for 8 days at 0.20 USD for each GB each month) | - | 8 |
| Planned work | | 237 |
| Optional live demo (3 h, both nodes, from the 80 USD reserve) | | 32 |

Notes:

1. The scale session shows the warmup cost of a new pod, and how the LMCache tier on node 1 shortens it. Warmup time is one of the four resources in the handout.
2. This section changes the demo row of point 12: the demo questions stay the acceptance test, and the talk shows recordings and charts.
3. Other credits: Superlinked (500 USD) pays for the overflow, with a limit of 15 USD each day. Brave Search costs about 10 USD. We do not need Modal.

## Point 5: fact-check rule (closed)

| Question | Result | Date |
|---|---|---|
| When does the live fact win? | F2 (the owner). Code decides, not an LLM judge. For `CONTRADICTED` or `OUTDATED`, the live fact wins only if it is not older than the bookmark evidence, and one of these is true: (a) its source tier is the same as the bookmark tier or better, or (b) two or more independent domains give the same correction. A tier-1 page with no date counts as current. A page with an older date never wins. If no condition is true, the claim is "disputed", and the answer shows both facts with their dates. | 2026-09-27 |
| Why not the first rule? | Paths (b) and (c) of the first rule did not check the date. An old page could then correct a newer bookmark. | 2026-09-27 |
| What is an independent domain? | A different registered domain, and text that is not a near copy of another result. Copies of one article count once. | 2026-09-27 |
| Which dates do we compare? | Bookmark evidence: the page date if the page gives one, else the Notion `Created` date. Live page: the date in the page content (JSON-LD `dateModified` or `datePublished`, then the meta tags, then a visible date near the title). We do not use the HTTP `Last-Modified` header, because dynamic pages send the current time. No date gives "unknown". An unknown date is never newer, except on a tier-1 page. | 2026-09-27 |
| Which search provider? | Brave, with Tavily as the fallback (ADR-009). SerpAPI is the legal path to Google results if we need them. Changed later on 2026-09-27: Tavily is the main provider, because the owner's Tavily key works and a Brave Search API key needs its own subscription. | 2026-09-27 |

The debate on points 1, 2, 3, 5, 6, 7, and 12 is complete. Next: one pass over the spec files and the ADRs.

## App framework (closed)

| Question | Result | Date |
|---|---|---|
| Which framework runs the agent? | LangChain and LangGraph Deep Agents (the owner). It replaces PydanticAI in ADR-006. Versions on 2026-09-27: `deepagents` 0.7.19, `langchain` 1.4.2, `langgraph` 1.2.12, `langchain-openai` 1.6.6. | 2026-09-27 |
| How do the two modes map? | Verified mode: one Deep Agent (`create_deep_agent`) with our tools and a `fact-checker` subagent for the verify loop. Quick mode: a LangChain agent (`create_agent`, on LangGraph) with only the bookmark search tool, so the first token stays inside SLO-3 (Claude proposed this split). Changed later on 2026-09-27 (Claude, from the library source): quick mode also has `fetch_page` and `screenshot_page` (J3) and `answer_now` (the answer gate). Verified mode first runs the quick agent for the draft, then the Deep Agent checks it (product spec, section 5). | 2026-09-27 |
| How does the agent reach the model? | `ChatOpenAI` with `edge` as the base URL. One model object for each role, each with its own `X-Step` header. An httpx event hook adds the headers of each turn (tenant, class, session, request id, deadline, privacy switch). | 2026-09-27 |
| What stays in code? | Rule F2 decides each claim (ADR-012). The `fact-checker` subagent returns structured verdicts (`response_format`), and `app/factcheck/resolve.py` decides. | 2026-09-27 |
| Effect on the serving shape | The Deep Agents system prompt and its built-in tools (`write_todos`, the file tools, `task`) make the shared prefix larger. Each verified turn makes more LLM calls. The trace recorder measures both (FR-14), and the capacity plan uses the measured values. | 2026-09-27 |

## Point 13: GPU stock (open, for the owner)

The Lambda API shows the stock. A read-only watcher logs it every 5 minutes (`metrics/lambda-capacity.jsonl`).

| Shape | Checks with stock (24 checks, 2026-09-27 23:03 to 2026-09-28 00:49 UTC) | Regions | USD/h |
|---|---|---|---|
| 2 x H100 SXM (node 1, T2) | 0 | none | 8.38 |
| 4 x H100 SXM (node 1, T4) | 0 | none | 16.36 |
| 2 x A6000 (node 2) | 9, then none after 00:44 UTC | us-south-2 | 2.18 |
| 1 x H100 PCIe (80 GB) | 7 | us-west-3 | 3.29 |
| 1 x GH200 (96 GB, ARM CPU) | 2 | us-east-3 | 2.29 |
| 1 x A100 SXM4 (40 GB, too small for our model) | 14 | us-east-1, us-west-2 | 1.99 |

A window of stock can close in about one hour. An approval that comes later can miss the window. Update at 01:14 UTC: 2 x H100 SXM had stock in us-southeast-1 at 01:09 UTC, and the stock was gone 5 minutes later.

Runbooks that are ready (no GPU is necessary to prepare them):

- `cluster/g0.sh`: Gate G0 on node 2, first with the llm-d simulator, then with the dev model.
- `cluster/g1-solo.sh`: an engine-only Gate G1 on node 1 alone, if node 1 has stock and node 2 does not. The screenshot test then moves to the next two-node session.

Options:

| ID | Option | Cost | Effect |
|---|---|---|---|
| S1 | Wait for 2 x H100 SXM (the plan). | 8.38 USD/h | No change. Risk: no H100 session. |
| S2 | Two 1 x H100 PCIe instances in one region: the prefill pod on one, the decode pod on the other. NIXL crosses the network between them. | 6.58 USD/h | H100 PCIe has about 60% of the HBM bandwidth and about 76% of the compute of H100 SXM. The KV of an 8K prompt is 1.09 GiB, so the hop takes about 0.9 s on a 10 Gbit/s link. **VERIFY** the network speed. |
| S3 | One 1 x H100 PCIe: option A (no split) on the H100. The hop proof runs with the dev model on the two A6000 GPUs of node 2. | 3.29 USD/h | Cheapest. The H100 has no hop proof. The handout accepts a hop proof or a warmup proof. |
| S4 | Let node 1 and node 2 be in different regions: WireGuard over the public IPs, with firewall rules for the public IP of each node (A9). | no cost | Each LLM call from `edge` to vLLM gets one more round trip between the regions (about 30 to 60 ms). |

Recommendation (Claude):

1. An approval in advance for node 2: launch 2 x A6000 when stock appears, run the planned block, and terminate at its end. Limit: 8 hours for each block and 16 hours in total (35 USD). Claude reports each launch and each terminate step at once.
2. Accept S4 now, so the two nodes do not need one region.
3. Keep S1 until Monday 2026-09-28, 18:00 PDT. Then use S2 if two 1 x H100 PCIe instances have stock in one region, else S3.

Update 2026-09-30 (Claude): no node 2 shape had stock from 12:37 PDT. Node 1 (4 x H100) stopped after 45 minutes with no node 2 (12.20 USD). The stock data (`metrics/lambda-capacity.jsonl`) shows that the H100 stock of 2026-09-29 came from 09:00 to 11:00 PDT.

Options for Thursday 2026-10-01, the last GPU day:

| ID | Option | Effect |
|---|---|---|
| T1 | Wait for node 2 and node 1 in one region (the plan). | No change. On 2026-09-30, this did not occur. |
| T2 | One 4 x H100 node runs all pods (ADR-005, revision 2). | Only one shape is necessary. About 16.36 USD/h. The node 2 pods share one GPU, as on node 2 (1 x H100). |

Decisions of the owner (2026-09-30, about 20:20 PDT):

1. No watcher on the evening of 2026-09-30.
2. The watcher starts at 07:00 PDT on Thursday, when the owner says "start".
3. T2, if Thursday again has no node 2 stock. Claude prepares it on 2026-09-30.
4. A quick turn that reads a figure through OCR uses the SLO-4 limit (demo question D-08, `03-product-spec.md`).
