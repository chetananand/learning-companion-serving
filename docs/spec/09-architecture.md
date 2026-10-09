# Architecture diagrams

This file shows the system in diagrams: the context, the planes, the deployment, the KV path, and the main sequence flows. The text and the reasons are in `04-system-design.md` and in the ADRs (`docs/decisions/`). The diagrams show the system as it ran in the sessions of 2026-09-29 and 2026-10-01.

Each diagram is Mermaid, so GitHub renders it. The names in the diagrams are the names of the Kubernetes objects and of the code.

## 1. Context

The owner uses the app in a browser. The app reads the Notion bookmarks and searches the live web. Each LLM call passes admission control and routing. Then it goes to our own vLLM engine on Lambda GPUs.

```mermaid
flowchart LR
  owner([Owner<br/>browser on the laptop])
  subgraph lambda[Lambda Cloud: our k3s cluster]
    app[Learning Companion<br/>UI, API, agents]
    acr[Admission control + routing<br/>edge, Envoy AI Gateway, llm-d router]
    eng[Engine<br/>vLLM pods and LMCache,<br/>Gemma 4 31B FP8]
    obs[Prometheus and Grafana]
  end
  notion[(Notion API<br/>the bookmarks)]
  web[Live web<br/>Tavily search, pages]
  hf[(Hugging Face Hub<br/>model weights)]
  ovf[Superlinked hosted API<br/>overflow, off in all runs]
  laptop[Laptop tools<br/>lambda_ctl, spend_guard, kubectl]
  lapi[Lambda Cloud API]
  owner -->|SSH tunnel, localhost ports| app
  app -->|each LLM call| acr -->|the request| eng
  acr -.->|a refused call that may leave| ovf
  app --> notion
  app --> web
  eng --> hf
  laptop -->|launch, terminate, stock| lapi
  laptop -->|SSH tunnel| obs
```

## 2. The planes

Admission control decides what enters and when it waits. Routing decides where it goes. Together they are the gateway of the handout (H-50, H-51). The engine does the work: vLLM keeps its own waiting queue, block table, preemption, and kernels (H-52).

```mermaid
flowchart TB
  subgraph appPlane[App plane: user turns]
    direction LR
    ui[companion-ui<br/>Streamlit] --> api[companion-api<br/>quick agent,<br/>Deep Agent fact check]
    jobs[ingest and sweep jobs<br/>load generator]
  end
  subgraph dataPlane[Data plane: bytes]
    direction TB
    qdrant[(Qdrant<br/>bookmark chunks)] ~~~ sie[SIE<br/>embed, rerank, OCR]
    browser[browser<br/>headless Chromium] ~~~ redis[(Redis<br/>token windows,<br/>overflow limiter)]
  end
  subgraph acr[Admission control + routing: decisions]
    direction LR
    edge[edge<br/>guard stage 1, admit,<br/>stay or leave] --> envoy[Envoy AI Gateway<br/>tenant token windows]
    edge --> guard[guard models<br/>NeMo Guardrails,<br/>Prompt Guard 2]
    envoy <-->|ext_proc| epp[llm-d router EPP<br/>flow control, scorers,<br/>P/D decider, warm gate, ramp]
    warm[warm-controller<br/>warm label, ramp label]
  end
  subgraph engine["Engine (node 1): vLLM pods and LMCache"]
    direction LR
    dec[vllm-decode pod<br/>routing sidecar + vLLM] -.->|if the router picked a prefill pod:<br/>the prompt, to compute its KV| pre[vllm-prefill pod<br/>store barrier + vLLM]
    pre -.->|a copy of the KV| lmc[(lmcache-server<br/>CPU RAM, 250 GiB)]
    lmc -->|the stored KV, if any| dec
  end
  subgraph ops[Operations]
    direction LR
    prom[(Prometheus<br/>planner rules, alerts)] --> keda[KEDA]
    prom --> graf[Grafana]
  end
  appPlane -->|search, embed, OCR, fetch| dataPlane
  appPlane -->|each LLM call| acr
  acr -.->|token windows, limiter| dataPlane
  acr -->|requests, labels| engine
  engine <-.->|metrics, replicas| ops
```

## 3. Deployment: the two-node layout

This layout ran on 2026-09-29. Node 2 was 1 x H100, because no 2 x A6000 had stock. The NodePorts listen on 127.0.0.1 only, and the laptop reaches them through an SSH tunnel.

```mermaid
flowchart TB
  laptop[Laptop<br/>SSH tunnel to the 127.0.0.1 NodePorts:<br/>6443, 3000, 8501, 8000, 8080, 9090]
  subgraph n2[Node 2: control and data, 1 x H100, k3s server, HAMi]
    direction TB
    subgraph n2gpu[The GPU, shared through HAMi slices]
      direction TB
      sieE[sie-embed<br/>12,000 MiB] ~~~ sieO[sie-ocr<br/>20,000 MiB]
      gs[guard-safety<br/>16,000 MiB] ~~~ gi[guard-injection<br/>4,000 MiB]
    end
    subgraph n2cpu[CPU and RAM]
      direction TB
      edge2[edge x 2<br/>guard: NeMo server] ~~~ api2[companion-api<br/>companion-ui] ~~~ data2[Qdrant, Redis, browser]
      epp2[router EPP and tokenizer<br/>Envoy Gateway pods] ~~~ wc2[warm-controller] ~~~ mon2[Prometheus, Grafana, KEDA]
    end
  end
  subgraph n1[Node 1: engine, 2 x H100 SXM 80 GB, NVIDIA device plugin]
    direction TB
    p1[vllm-prefill<br/>GPU 0, one full GPU]
    d1[vllm-decode<br/>GPU 1, one full GPU]
    l1[(lmcache-server<br/>host network, 250 GiB RAM)]
  end
  laptop --> n2
  n2 <-->|private network, WireGuard flannel| n1
```

## 4. Deployment: the one-node layout

This layout ran on 2026-10-01 on 8 x A100 80 GB (ADR-005, revision 2). HAMi manages all GPUs. An engine pod asks for 100% of the cores and the memory of one GPU, so it gets a GPU that no other pod uses. The node 2 GPU pods pack onto one GPU.

```mermaid
flowchart TB
  subgraph node[One node: 8 x A100 80 GB, k3s server, HAMi on all GPUs]
    direction TB
    subgraph gpus[GPUs]
      direction LR
      g0[GPU A<br/>sie-embed, sie-ocr,<br/>guard-safety, guard-injection<br/>binpack] ~~~ g1[GPU B<br/>vllm-prefill<br/>exclusive] ~~~ g2[GPU C<br/>vllm-decode<br/>exclusive]
      g3[GPU D<br/>second decode pod<br/>E9 scale-out] ~~~ g4[GPU E<br/>second prefill pod<br/>E9 scale-out] ~~~ g5[GPUs F to H<br/>free]
    end
    cpu[CPU and RAM: edge, guard, API, UI, router, Envoy,<br/>Qdrant, Redis, monitoring, lmcache-server]
    note[CUDA_DISABLE_CONTROL=true in the engine pods:<br/>vLLM sees the full GPU, with no HAMi memory hook]
    gpus ~~~ cpu ~~~ note
  end
```

## 5. The KV path: prefix cache, hop, LMCache server, and the router index

Each vLLM pod keeps its KV cache and its prefix cache in GPU memory. Both pods use the LMCache server on the node to store and load a copy of the KV in CPU RAM. The vLLM pods publish KV events, and the router builds its prefix index from them.

```mermaid
flowchart LR
  subgraph prefillPod[vllm-prefill pod]
    bar[store barrier :8000]
    vp[vLLM :8200<br/>GPU prefix cache]
  end
  subgraph decodePod[vllm-decode pod]
    sc[routing sidecar :8000]
    vd[vLLM :8200<br/>GPU prefix cache]
  end
  lmc[(lmcache-server<br/>a copy of the KV in CPU RAM,<br/>LRU, evict at 90%,<br/>about 865 KB for each token)]
  idx[router prefix index<br/>precise: KV events]
  sc -->|if the router picked a prefill pod: the prompt,<br/>max_tokens 1, so vLLM only computes its KV| bar --> vp
  sc -->|the request| vd
  vp -->|a copy of the KV,<br/>in chunks of 256 tokens| lmc
  bar -.->|store counters, read until<br/>finished = submitted| lmc
  lmc -->|the stored KV chunks| vd
  vp -->|BlockStored, BlockRemoved on ZMQ :5556| idx
  vd -->|KV events| idx
```

## 6. Sequence: a quick turn

A quick turn runs one agent. Each LLM call of the agent passes admission control and routing. Most calls have fewer than 2,048 uncached tokens, so the decode pod does the whole call.

```mermaid
sequenceDiagram
  autonumber
  actor U as Owner
  participant UI as companion-ui
  participant API as companion-api (quick agent)
  participant S as SIE and Qdrant
  participant E as edge
  participant G as guard models
  participant V as Envoy AI Gateway
  participant R as llm-d router EPP
  participant D as vllm-decode
  U->>UI: question (quick mode)
  UI->>API: POST /v1/turns (SSE stream)
  API->>S: search_bookmarks: embed, search, rerank
  S-->>API: chunks with bookmark refs
  API->>E: POST /v1/chat/completions (tenant, class, session, step, deadline)
  E->>E: guard stage 1 (CPU rules), slice_oom check
  E->>G: guard stage 2 (Prompt Guard 2 and NeMo, in parallel)
  G-->>E: allow (the verdict is cached for the session)
  E->>V: forward, with the TTL of the flow control
  V->>V: tenant token window (Redis)
  V->>R: ext_proc: pick an endpoint
  R->>R: flow control band, scorers, P/D decider: no split
  R-->>V: decode endpoint
  V->>D: request
  D-->>U: tokens stream back through V, E, API, and UI
  API->>API: answer_now, then the answer call with no tools
```

## 7. Sequence: a verified turn

A verified turn first runs the quick agent for a draft. Then a Deep Agent checks up to 3 claims of the draft against live pages. Rule F2 decides when the live result wins (ADR-012).

```mermaid
sequenceDiagram
  autonumber
  participant UI as companion-ui
  participant API as companion-api
  participant Q as quick agent
  participant DA as Deep Agent
  participant FC as fact-checker subagent
  participant W as live web (Tavily, pages)
  participant B as browser and SIE OCR
  participant LLM as admission control, routing, and engine
  UI->>API: POST /v1/turns (verified mode)
  API->>Q: question and session history
  Q->>LLM: agent steps (search, fetch, answer_now)
  LLM-->>UI: the draft streams (phase draft)
  API->>DA: question, draft, and bookmark sources
  DA->>FC: task: check one claim (at most 3)
  FC->>W: web_search, fetch_page
  FC->>B: screenshot_page for a figure
  W-->>FC: page text (after the page check)
  FC-->>DA: claim check (verdict, sources)
  DA->>DA: rule F2: resolve() decides the status of each claim
  DA->>LLM: final answer call (no tools)
  LLM-->>UI: the final answer streams (phase final), with the claims
  Note over API,LLM: AnswerGate: after answer_now, the step budget, or the deadline, the call has no tools. An empty forced answer gets one more call.
```

## 8. Sequence: a split request and the hop through LMCache

The P/D decider splits a request with 2,048 or more uncached tokens. The routing sidecar in the decode pod first sends the prompt to the prefill pod, with max_tokens 1. So vLLM there only computes the KV of the prompt, and the decode pod writes the answer. The store barrier holds the answer until LMCache has stored the KV (ADR-002, revision 3).

```mermaid
sequenceDiagram
  autonumber
  participant V as Envoy
  participant R as llm-d router EPP
  participant SC as decode routing sidecar
  participant BAR as store barrier (prefill pod)
  participant VP as vLLM prefill
  participant L as lmcache-server
  participant VD as vLLM decode
  V->>R: pick endpoints
  R-->>V: decode pod, plus the prefill host header (2,048+ uncached tokens)
  V->>SC: request
  SC->>BAR: the prompt (max_tokens 1, no stream): compute the KV only
  BAR->>VP: forward
  VP->>L: store the KV chunks
  VP-->>BAR: one token
  loop at least 0.1 s, at most 0.5 s
    BAR->>L: read the store counters
  end
  BAR-->>SC: answer (x-barrier-hold-ms, x-barrier-outcome)
  SC->>VD: the request
  VD->>L: look up and load the KV chunks
  L-->>VD: KV (only the tokens after the last full chunk are computed again)
  VD-->>V: tokens stream
```

## 9. Sequence: admission and shedding

A request can stop at each stage, with a code and a reason. A rejected request never reaches a serving GPU. A capacity reject of an interactive call can leave to the overflow provider, if the call permits it. A tenant 429 never leaves. The overflow was off in all our runs, so a call that the gate let go got a 503.

```mermaid
sequenceDiagram
  autonumber
  participant A as app or load generator
  participant E as edge
  participant G as guard
  participant V as Envoy AI Gateway
  participant R as llm-d router EPP
  participant O as overflow (Superlinked, off in all runs)
  A->>E: request
  alt guard stage 1 or 2 rejects
    E-->>A: 400 prompt_injection (or another guard reason)
  else prompt and max_tokens above the model length
    E-->>A: 413 slice_oom (stays)
  else tenant over its token window
    E->>V: forward
    V-->>E: 429 tenant_tokens
    E-->>A: 429 (never leaves)
  else the band TTL ends in the queue, or the pool is saturated
    E->>V: forward
    V->>R: pick
    R-->>V: 503 timeout_queue, or 503 no_endpoints
    alt interactive, overflow permitted, no image
      E->>O: the same request (limiter in Redis)
      O-->>A: answer, with x-companion-via overflow
    else
      E-->>A: 503 with the reason and retry-after
    end
  end
```

## 10. Sequence: a pod comes back, then warmup and ramp

The router sends traffic only to a pod with the warm label. The warm controller gives the label after its warmup routine, and then it raises the ramp label step by step while the TTFT p99 holds.

```mermaid
sequenceDiagram
  autonumber
  participant K as Kubernetes
  participant P as new vLLM pod
  participant WC as warm-controller
  participant R as llm-d router EPP
  participant PR as Prometheus
  K->>P: start (model load, CUDA graphs)
  WC->>P: readiness probe until the engine answers
  WC->>P: seed the system prompts (prefix cache)
  WC->>P: shape probes (prompt sizes of the app)
  WC->>P: split probe (decode pod only)
  WC->>P: 4K-token TTFT probe
  alt TTFT at most 1.5 x the baseline (0.35 s on H100, 1.0 s on A100)
    WC->>K: labels companion.io/warm=true, companion.io/ramp=r10
    R->>R: warm gate passes the pod, the ramp scorer gives it a low score
    loop while the TTFT p99 holds
      WC->>PR: read the TTFT p99 and the progress of the pod
      WC->>K: ramp label r25, then r50, then r100
    end
  else too slow
    WC->>K: state cold_failed, try again after 300 s
  end
```

## 11. Sequence: scale-out of a pool

Prometheus recording rules turn the load signals into the replicas that each pool needs (`cluster/manifests/base/monitoring/rules.yaml`). KEDA reads them. The decode pool scales on busy sequences. The prefill pool scales on uncached prefill tokens.

```mermaid
sequenceDiagram
  autonumber
  participant PR as Prometheus
  participant KE as KEDA and HPA
  participant K as Kubernetes scheduler
  participant GPU as device plugin (NVIDIA or HAMi)
  participant P as new vLLM pod
  participant WC as warm-controller
  PR->>PR: decode: ceil(running / (0.6 x 24))
  PR->>PR: prefill: ceil(uncached prefill tokens/s / (0.7 x capacity))
  KE->>PR: query companion:planner_desired_replicas
  KE->>K: set the replicas of the pool (at most maxReplicaCount)
  K->>GPU: a full GPU for the new pod
  K->>P: start
  P->>WC: the warm controller warms it, then ramps it (section 10)
  Note over PR,KE: E9 (2026-10-01): decode scaled out 15 s after the planner asked. Prefill scaled out only after the capacity value matched the A100.
```

## 12. Sequence: a measurement run and its evidence

Each run saves the same evidence, so each number in `DESIGN.md` has a source.

```mermaid
sequenceDiagram
  autonumber
  participant OP as laptop (session script)
  participant VS as variant.sh
  participant LG as loadgen Job (in the cluster)
  participant GW as admission control, routing, and engine
  participant PR as Prometheus
  participant GR as Grafana image renderer
  OP->>VS: apply the router and engine variant
  VS->>GW: wait until the full path answers 3 calls in a row
  OP->>LG: replay, capacity, or tool (for example tools.ghost_probe)
  LG->>GW: the load
  LG-->>OP: client.jsonl and summary.json (rsync to metrics/run-id/)
  OP->>GW: Envoy and edge access logs, raw /metrics scrapes
  OP->>PR: range queries (tools/prom_dump.py), the run record
  OP->>GR: nine dashboard images for the run window (tools/grafana_shots.py)
  OP->>OP: tools/report_numbers.py and the notebooks
```

## 13. Operations: launch, spend, and teardown

The launch tool and the spend guard enforce the approvals of the owner. These are the day plan, the launch window, the hour limits, and the USD limit. We report each launch and each terminate step at once, and we record it in `docs/budget-ledger.md`.

```mermaid
flowchart TB
  watch[capacity_watch<br/>read-only stock] --> decide{stock and day plan}
  decide -->|approved shape| launch[lambda_ctl launch<br/>approval checks]
  launch --> guardrun[spend_guard<br/>each 60 s]
  launch --> up[bring-up scripts<br/>g0.sh, t2.sh, one.sh]
  up --> runs[session scripts<br/>runs and evidence]
  runs --> capture[capture before the terminate<br/>report, Prometheus snapshot, Grafana images]
  capture --> term[lambda_ctl terminate]
  guardrun -->|23:00 PDT, hour limit, or USD limit| term
  term --> ledger[budget-ledger.md]
```
