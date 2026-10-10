# DESIGN

This file is the design report of the project. It answers the questions of the handout with our own numbers. Each answer points at a file and a line, at a run in `metrics/`, or at a pasted scrape.

- The result tables: `docs/results.md`. The script `tools/report_numbers.py` makes them from the saved runs.
- The Part 5 answers, with plots: `notebook/part5_queue.ipynb`.
- The plots of each experiment, and the four resources: `notebook/experiments.ipynb`.
- The plots: `plots/`. The Grafana images of each run: `metrics/<run-id>/grafana/` (nine for each run).
- The session logs: `metrics/s1-session/session.md` and `metrics/s2-session.md`.

Status: draft of 2026-10-02. It has all runs: 2026-09-29 and 2026-09-30 on H100, and 2026-10-01 on one node with 8 x A100 80 GB. The due date and the talk are on 2026-10-10.

## Summary

1. The app is an agentic RAG learning companion over the Notion bookmarks of its owner (4,501 chunks in Qdrant). It has a quick mode (one agent) and a verified mode (a fact-check agent with a live web search).
2. The cluster has two nodes. Node 1 (2 x H100 SXM 80 GB) runs the engine: vLLM v0.30.0 with Gemma 4 31B FP8. Node 2 (1 x H100 80 GB) runs the control plane, the data plane, and the guard models. On 2026-10-01, no H100 had stock. Then one node with 8 x A100 80 GB ran all pods, and HAMi gave each engine pod a full GPU (ADR-005, revision 2).
3. Topology: two whole pods (layout A) beat one prefill pod and one decode pod (layout C) at each load of our app traffic. At 100% load, the interactive TTFT p50 was 0.84 s against 4.59 s (E3). On the A100 node, with 32 decode sequences in both layouts, layout A had about half of the TTFT p50 of layout C, or less.
4. The split still helps in two cases. It cuts the ITL of the other streams (E5, 8K tokens: p95 0.07 s against 0.24 s). Short agent steps also start sooner behind a long retrieve (E14: 0.25 s against 1.26 s). Even with the split, 16 decode streams had an ITL p95 above SLO-2 (50 ms).
5. The hop goes through the LMCache server (a copy of the KV in CPU RAM), with a store barrier. Its TTFT was 0.52 s (shared prefix) and 0.78 s (unshared). NIXL over TCP took 4.1 to 4.3 s (E4).
6. We shed at the door. No run preempted a request. The router stops the dispatch at a KV use of 90% (E2, E3).
7. The warmup routine cut the first-minute TTFT p95 of a returned decode pod from 10.9 s to 7.3 s. A ramp kept the TTFT p95 of a returned pod at 14.6 s, against 57.3 s for a jump (E8).
8. Layout C with one decode pod is a single point of failure. While that pod restarted, 182 to 184 calls got 503 `no_endpoints`. In layout A, 3 to 4 calls did (E8).
9. The guard blocked 22 of 22 attack turns and 0 of 178 benign turns. The page check missed about half of the injected pages (E17).
10. Scale (E9): the planner asked for a second pod of the right pool, and KEDA made it 15 s later. The new pod got the warm label about 4 minutes after the request. The prefill rule needs the capacity of the GPU in use. With the H100 value, the A100 prefill pool did not grow.
11. Ghosts (E7, no LMCache): after a clear of the prefix cache, vLLM sent one `AllBlocksCleared` event. The router did not act on it. The next call of each warm session went to the cleared pod and missed once.
12. The demo check passed 8 of 12 questions on the H100 and 6 of 12 on the A100. The open failures are in the fact check: claim checks that time out or find no evidence.
13. Cost: 237.43 USD of the 400 USD Lambda credit (to 2026-10-01).

## Part 0. The application

What it is (H-01, H-33): `docs/spec/03-product-spec.md` and `app/`. The agent runs on LangChain and LangGraph (ADR-006).

- Quick mode: one agent with the tools `search_bookmarks`, `fetch_page`, `screenshot_page` (OCR), and `answer_now` (`app/agent/tools.py`).
- Verified mode: the quick agent writes a draft. Then a Deep Agent with a `fact-checker` subagent checks up to 3 claims against live pages (`app/factcheck/`). Rule F2 decides when the live result wins (ADR-012).
- Bookmark search (`app/ingest/job.py`, `app/retrieval.py`, ADR-007): the ingest ran once, in G0 on 2026-09-28 (PDT), before all tests. It read 1,000 Notion rows, fetched each page, and cut the text into chunks of 500 to 800 tokens. The page check ran on each chunk. SIE embedded each chunk with bge-m3, and Qdrant stored the vector and a BM25 index. Result: 910 rows gave 4,112 chunks, and 33 links were dead (`metrics/g0-session/session.md`). Each later session restored the Qdrant snapshot: 4,501 points on 2026-09-29 and 4,502 on 2026-10-01. In each turn, SIE embeds the question, and Qdrant fuses a vector search and a BM25 search (RRF) into 30 chunks. SIE reranks them with Qwen3 Reranker 4B, and the agent gets the top 8. The search makes no LLM call.

We combine Track A (RAG) and Track B (agents). This combination is extra (L569).

### Shared tokens and unique tokens (H-95)

The capture runs of 2026-09-29 (`cap-b1`, `cap-b2`, `cap-b3`, `cap-b3-2`) sent real app turns. Their Envoy access logs give the tokens of each call (1,606 calls):

| Step | Calls | Prompt tokens p50 | Cached tokens p50 | Cached share | New tokens p50 | Output tokens p50 |
|---|---|---|---|---|---|---|
| quick | 978 | 5,121 | 512 | 0.19 | 4,515 | 25 |
| verify | 321 | 1,427 | 1,280 | 0.65 | 133 | 36 |
| agent | 303 | 2,121 | 1,280 | 0.64 | 459 | 166 |
| sweep (batch) | 3 | 2,923 | 0 | 0.00 | 2,923 | 73 |

- Each step starts with a fixed system prompt and the tool schemas. The cache held a median of 512 tokens for the quick step, and 1,280 tokens for the verify and agent steps.
- Each request has unique tokens: the question, the retrieved chunks, the fetched pages, and the OCR text.
- Only the calls of one session share the history of that session. Thus an agent step finds 64% of its prompt in the cache.
- In the capture, the engine had 31% of all prompt tokens in its cache. Of the calls, 60% had fewer than 2,048 new tokens.

## Part 1. Capacity on paper, and what we measured

The paper math is in `docs/spec/05-capacity-plan.md` (H-35 to H-41). `tools/capacity.py` computes it.

- The GPU that we ran: H100 SXM 80 GB (`metrics/t2-20260929T170159Z/nvidia-smi-node1.txt`). On 2026-10-01: A100 SXM4 80 GB (`metrics/one-20261002T015416Z/nvidia-smi.txt`). The A100 has no FP8 tensor cores, and the H100 has them.
- `kv_bytes_per_token` of Gemma 4 31B (BF16 KV) has two parts. The full-attention layers use 40,960 bytes for each token. The sliding-window layers use 819,200 bytes for each token, but only for the last 1,024 tokens. The fixed state for each sequence is 0 (capacity plan, section 3).
- Paper concurrency on one H100: 32 sequences at 8K tokens, 20 at 24K, and 17 at 32K (`max_len`). FP8 KV doubles these values (section 4).
- The app sends 5,121 tokens (the median prompt of a quick step). At this length, one sequence needs 0.98 GiB of KV, so one H100 fits 36 sequences, and 72 with FP8 KV. `python tools/capacity.py --lengths 5121,8192,32768` computes these values.
- Model switch (H-40): we kept Gemma 4 31B, because it passed all 7 G1 tests. With the challenger Muse Glimmer 30B, the KV of one 8K sequence is 83% smaller (0.18 GiB against 1.09 GiB). At 8K, one H100 fits 189 of its sequences, not 32 (capacity plan, section 5). We changed the KV to FP8 instead (E6). It halves the bytes for each token, and the decode pod's KV cache grew from 173,657 to 345,235 tokens.
- Measured: vLLM gave the decode pod a GPU KV cache of 173,657 tokens (38.35 GiB), and 345,235 tokens with fp8 KV (E6). The prefill pod got 43,094 tokens (36.23 GiB). It uses chunks of 16,384 tokens (`metrics/t2-20260929T170159Z/vllm-*.log`). On the A100, the decode pod got 175,381 tokens (38.73 GiB), and the prefill pod got 43,047 tokens (36.2 GiB) (`metrics/one-20261002T015416Z/vllm-*.log`).

E1 sends fixed prompts straight to the gateway (`docs/results.md`, E1):

| Prompt | Concurrent | All ok | TTFT p50 | TTFT p95 | KV use max (decode pod) | Preemptions |
|---|---|---|---|---|---|---|
| 8,000 tokens | 32 | yes | 12.91 s | 20.04 s | 0.90 | 0 |
| 24,000 tokens | 24 | yes | 39.20 s | 53.73 s | 0.57 | 0 |

### The four scarce resources (H-23)

| Resource | Measured peak | Run |
|---|---|---|
| Decode slots | 24 running of 24 (`--max-num-seqs`) on the decode pod | `e3-c-150` |
| KV blocks | KV use 0.944 on the decode pod. The router holds the dispatch at 0.90. | `e13-gate` |
| Hop bandwidth | up to 9,938 tokens each second from the LMCache server. A split request: 0.78 s (unshared, median). | `e6-fp8kv`, `e4-hop` |
| Warmup time | 290 s with no decode pod (pod start and warmup routine), 275 s with no warmup routine | `e8-c-warmup`, `e8-c-immediate` |

Warmup is a budget (R-05). The warmup routine added about 15 s to the outage. In return, the first-minute TTFT p95 of the returned pod fell from 10.9 s to 7.3 s.

### The limiter hypothesis, and the result (H-41, H-42)

| Hypothesis (capacity plan, section 8) | Result |
|---|---|
| RAG mix: prefill compute on the prefill pod | Correct for long prompts. At 24K tokens, the TTFT grew with the prefill queue: 24 prompts of 24K tokens need about 55 s at 10,500 tokens each second (E1). |
| Agent mix: KV blocks on the decode pod | Partly correct. The KV use of the decode pod went to 90%, but no pod preempted. The router held the load at 90% (E2, E3). |
| Not the weights | Correct. The weights use 30.4 GiB of 71.7 GiB. |
| Not the interconnect | Correct for the LMCache hop (0.52 to 0.78 s). Wrong for NIXL between two pods: UCX used TCP (4.1 to 4.3 s, E4). |
| Not the scheduler: the vLLM waiting queue stays short | Wrong on the decode pod. Its waiting queue held up to 50 requests at 150% load (`e3-c-150`). |

What limited concurrency on this GPU for this app (H-104): the decode pod. Most agent calls have fewer than 2,048 new tokens, so the router does not split them. The decode pod then computes their prompts too, and it held 24 running sequences (`--max-num-seqs`). At 100% load, it computed up to 16,200 prompt tokens each second. The prefill pod computed up to 4,550 (Grafana, vLLM dashboard of `e3-c-100`). With 32 decode sequences, the TTFT p50 fell from 4.59 s to 2.10 s (E15).

## Part 2. Cluster design

- GPU (H-43): H100 SXM 80 GB for the engine. A smaller GPU cannot hold the 31B model with room for KV (ADR-004, capacity plan section 7). Node 2 ran on 1 x H100 80 GB, because Lambda had no 2 x A6000 stock. The node 2 GPU pods need 52 GB. On 2026-10-01, no H100 had stock, and one node with 8 x A100 80 GB ran all pods (ADR-005, revision 2, and its A100 addendum). The node 2 GPU pods shared one GPU (HAMi binpack). Each engine pod asked for 100% of one GPU.
- Model (H-44): `RedHatAI/gemma-4-31B-it-FP8-dynamic` (ADR-003). Gate G1 passed: tool calls 97.5%, TTFT median 0.90 s, ITL p50 18.2 ms and p95 20.5 ms.
- Topology (H-45): ADR-005, option C: one prefill pod and one decode pod, each with a full GPU, TP 1, and no HAMi. The llm-d `prefix-based-pd-decider` splits a request when 2,048 or more of its tokens are not cached (`control/router/policy.yaml:19` and `:20`). E3 shows that layout A is better for our traffic (see "What the data changed").
- Concurrency (H-46): decode `--max-num-seqs=24`, prefill `--max-num-seqs=8`, and `--max-model-len=32768` on both pods (`cluster/manifests/base/engine/`).
- Hop backend (H-47): the LMCache server, with our name `lmcache` (ADR-002, revisions 2 and 3). The prefill pod stores a copy of the KV in the LMCache server (CPU RAM), and the decode pod loads it. The store barrier (`control/barrier/proxy.py`) holds the prefill answer until the store ends.
- Overflow (H-48): the Superlinked hosted API, model `qwen3.8-27b` (or `Qwen/Qwen3.5-4B`). The limiter (Redis): 20 requests and 60,000 tokens each minute, 4 in flight, and 15 USD each day (ADR-008).
- Two workers (H-49): the pods `vllm-prefill` and `vllm-decode` (`metrics/t2-20260930T022105Z/pods.txt`).
- The gateway and the engine (H-50 to H-52): our gateway is admission control + routing. It is `edge`, the Envoy AI Gateway (Agent Router), and the llm-d router. The gateway admits, and the router decides where. The engine is vLLM, with its waiting queue, block table, preemption, and kernels.
- Scale (H-53, H-106): the planner rules in `cluster/manifests/base/monitoring/rules.yaml` give the replicas of each pool. Prefill: `ceil(uncached prefill tokens each second / (0.7 x 10,500))`. Decode: `ceil(running sequences / (0.6 x 24))`. KEDA reads them.
- E9 (2026-10-01, one node, at most 2 pods for each pool): under a decode-heavy load, the planner asked for a second decode pod. KEDA made it 15 s later. Under a prefill-heavy load, the planner asked for a second prefill pod only after one change. We set its prefill capacity to the A100 value: 1,800 tokens each second. Each new pod got the warm label 225 to 235 s after the request (`docs/results.md`, E9, and `plots/e9-scale.png`).
- Live engine metrics (H-54): Prometheus scrapes each 5 s. The dashboards come from `tools/dashboards.py`.
- Binding (H-55): the NodePorts listen on 127.0.0.1 only (`cluster/bootstrap/k3s-server.sh`, `--kube-proxy-arg nodeport-addresses=127.0.0.1/32`). The laptop reaches them through an SSH tunnel. Evidence: `metrics/g0-20260929T021438Z/listen.txt`.

## Part 3. Guardrails, admit, stay or leave

- `inspect(payload) -> Guard` (H-56): `control/edge/guard.py:105` is stage 1 (the rules on the CPU). `GuardClient` (`control/edge/guard.py:178`) is stage 2: Llama Prompt Guard 2 (`guard-injection`) and the NeMo Guardrails server with Nemotron Content Safety (`guard`), in parallel (ADR-011). The guard models run on node 2, never on a serving GPU (U-19).
- E17: the guard blocked 22 of 22 attack turns and 0 of 178 benign turns. A check takes 0.19 s (p50) when it is cold, and a cached verdict takes 0.8 ms.
- E17 also found a limit. The page check (Prompt Guard 2 on page windows) missed 50% of the injected pages at the threshold 0.3, with no false positive. The NeMo safety rail is not a second layer for pages. The edge sends it only the new user message, not the page text in tool results. In the fact check, rule F2 limits the harm. Code, not the LLM, decides when a live page wins, by the source tier and the dates.
- `should_shed(req, snap) -> (shed, code, reason, retry_after_seconds)` (H-57): the rule table in `docs/spec/04-system-design.md`, section 6.2. Production components enforce it, and we own the values:
  - rules 1 and 2 (`tenant_tokens`, `tenant_requests`, 429): the Agent Router windows, `control/router/policy.yaml:61` to `:68`.
  - rule 3 (`slice_oom`, 413, stays): `control/edge/admit.py:23`.
  - rules 4 and 5 (`no_signal`, `kv_free`): the saturation detector, `control/router/policy.yaml:55` to `:58` (queue depth 5, KV use 0.90, metrics older than 2 s).
  - rule 6 (`timeout_queue`): the band TTLs, `control/router/policy.yaml:45` to `:48`. For each request, `edge` sets a TTL of half of the time left.
  - rule 7 (`p99_spread`): `priority-holdback-policy`, `control/router/policy.yaml:51`.
  - the router reasons to our codes: `control/edge/admit.py:48` and `:52`. A 429 without a router reason is a tenant limit, and it never leaves.
- The test `control/router/tests/test_decision_table.py` checks each row of the table against the rendered config and `edge`.
- Stay or leave: `control/edge/overflow.py:19`. E13 (M4 at 150%): the gate marked exactly the 124 interactive `timeout_queue` sheds as "may leave", and it kept all batch calls. The demo runs with the overflow off.

## Part 4. Place

- `pick(req, workers, *, policy) -> Worker | Shed` (H-58): the llm-d scheduling profiles from `control/router/render.py`. The decode profile and the prefill profile have different scorers (`control/router/policy.yaml:36` and `:37`).
- Policies (H-59): `prefix_then_load` (the default), `least_loaded`, and `random` (`control/router/policy.yaml:15`). The router has no p2c picker. All runs used `prefix_then_load`.
- Queue depth is a scorer in both profiles (`queue-scorer`, H-61). The warm gate is a filter.
- No bounce (H-62): the router picks once, and the Agent Router route has no retries. If all pods shed, `edge` returns 503.
- E11 (stale metrics, M3, layout A): pod B serves real metrics, a frozen snapshot of an empty pod, or no answer. With real metrics, pod B did 51% of the work. With the frozen snapshot, the router believed that pod B was empty, and pod B did 80% of the work. With no answer, the router treated the metrics of pod B as stale, and pod B did 8%.
- The TTFT p95 was 1.20 s, 1.37 s, and 1.74 s. Each mode had only 257 calls, and one pod can carry that load. At a higher load, a frozen snapshot overloads one pod.

## Part 5. Queue

The answers to H-63 to H-73 are in `notebook/part5_queue.ipynb`, with plots from the runs. In short:

| Question | Answer (run) |
|---|---|
| Who sits in our queue, and who in the vLLM queue? (H-65) | Our queue holds a request before the pick, by band and tenant. The vLLM queue holds it after the pick. At 150% load: up to 48 interactive and 24 batch in our queue, up to 50 in the vLLM queue of the decode pod (`e3-c-150`). |
| Waiting, running, preempted (H-66) | Decode: up to 24 running and 50 waiting. Prefill: up to 4 running. 0 preemptions in all runs. |
| Queue depth for each pod and mix (H-67) | M1, M2, M3: 6 or fewer. M4: up to 36 in the router queue of the decode pod. |
| A 32K retrieve and a short agent step are ready. Who goes first? (H-68) | The agent step, in all rounds (E14). The split decides the cost: 0.25 s with the split, 1.0 to 1.26 s with no split. |
| PagedAttention or prefix cache: which saved memory? (H-69) | PagedAttention packs the KV in all arms. The prefix cache saved prefill work (TTFT p50 0.43 s against 0.60 s), not memory. fp8 KV saved memory (E6). |
| Engine flags and reasons (H-70) | Decode: 24 sequences and chunks of 2,560 tokens. Prefill: 8 sequences and chunks of 16,384 tokens. E15: 32 decode sequences are better. |
| KV full after admit: door or preempt? (H-71) | The door. The router sheds at a KV use of 90%, and no pod preempted (E2). |
| Client gone: who frees the KV? (H-72) | vLLM `abort_requests` stops the request, and the engine frees the blocks. Envoy closed all 46 aborted streams at the client time (E12). |
| After a worker returns: 100% at once or a ramp? (H-73) | A ramp (r10, r25, r50, r100) while the p99 holds. A jump gave a TTFT p95 of 57.3 s on the returned pod, and the ramp gave 14.6 s (E8). |

## Part 6. Hop and warmth

- Two workers, and the KV moves (H-75): the prefill pod stores a copy of the KV in the LMCache server. The decode pod loads it. G1 test: the decode pod loaded 8,448 of 8,500 prompt tokens.
- Same pod: no hop (H-76). Two pods: a hop record (H-77). `tools/hop_records.py` writes `metrics/<run-id>/hops.jsonl` from the Envoy access log: source, destination, tokens, cached tokens, and the backend.
- Where we hop, and what is not copied (H-101): the P/D decider splits a request with 2,048 or more uncached tokens. In E4, for prompts with no shared prefix, the decode pod loaded 6,144 of 6,398 prompt tokens (median) from the LMCache server. It computes the rest again: the tokens after the last full LMCache chunk of 256 tokens.
- The store barrier is necessary. The prefill engine answers before LMCache ends the store. With no barrier, the decode lookup missed, and the decode pod computed the prompt again. In G1, gaps of 10 ms and 20 ms gave 0 hits in 10 tries.
- The store barrier does not hold up under load. It reads the global store counters of LMCache, and it waits until LMCache finishes every store that it had at that time. Under load, many stores are in progress, and they do not finish within the 0.5 s cap. On the A100 node, the barrier hit its 0.5 s cap on 170 of 237 holds (72%) in the P/D load tests. At the end of the session, the two prefill pods had hit the cap on 163 of 171 holds (95%) and 67 of 68 (99%). The raw scrapes are in `metrics/e3-c32-r030/scrape-mid/` and `metrics/one-20261002T050552Z/`. A capped hold adds 0.5 s and does not promise a hit. With one request at a time, the barrier worked: 3 holds of 0.19 s on average at the start of the session.
- What a production hop needs, from these limits:
  1. A store signal for each request, in place of the global counters. LMCache confirms the chunks of the request, or the transfer reports its own end (NIXL does).
  2. RDMA between nodes (InfiniBand or RoCE), with NIXL or a distributed KV store. Our LMCache path uses CUDA IPC, so it works only inside one node, and NIXL over TCP took about 4 s (E4).
  3. Two or more pods in each pool, and a fallback: if the prefill request fails, the decode pod does the prefill itself. Fault 35 stopped all split calls, because one prefill engine restarted.
  4. mTLS between pods. The sidecar ran with `--secure-proxy=false`.
  5. P/D only where the measured traffic has many long uncached prompts. For our traffic, two whole pods were faster (E3).
- Is the new replica warm? (H-78, H-79): yes, after the warmup routine. On a returned decode pod, the first-minute TTFT p95 was 7.3 s with the warmup and 10.9 s with none (`plots/warmup-ttft.png`, E8). The routine added about 15 s to the outage (290 s against 275 s).
- Evict and ghosts (H-102): vLLM evicts the GPU prefix cache, and the LMCache server evicts its oldest KV chunks (LRU, at 90% of 250 GiB). The 250 GiB is a cap of the 450 GiB of RAM on node 1. On the A100 node, LMCache held 40 GiB at the start of the session. In the E3 runs, it held up to 214 GiB (86% of the cap). At the end it held 196 GiB (raw scrapes in `metrics/e3-c32-r030/scrape-mid/` and `metrics/one-*/`). The router learns of each eviction from the vLLM KV events (`prefix_index: precise`). E7 cleared the GPU cache of the decode pod during a run. The token hit ratio did not fall (0.76 before, 0.79 after). The LMCache server still held the prefixes, so the decode pod loaded them again.
- E7 again, with no LMCache (2026-10-01, layout A): after the clear, pod B sent one `AllBlocksCleared` event and no `BlockRemoved` event (`metrics/e7b-*/kv-events.json`). The router still sent the next call of each warm session to pod B. This was true for 15 of 15 sessions with the precise index, and for 10 of 10 with the approximate index. Each of these calls missed the cache once (ghost ratio 0.10 and 0.09). Thus the precise index did not act on the clear. A ghost costs one prefill of the session history.

## Part 7. Wire the app to the cluster

- The request path: `docs/spec/04-system-design.md`, section 5, and `control/edge/server.py`.
- The app has one LLM URL: `edge` (`app/config.py`, `edge_url`). `app/tests/test_agent_flow.py` checks the headers of each call (H-34).
- Engine smoke (H-82): `cluster/smoke/smoke.sh`. Session 1: 12 of 12 checks passed when the safety model was warm.
- The demo check (journey J6, `app/demo_check.py`): 8 of 12 questions passed on the H100 (2026-09-30, judged with SLO-4 = 60 s). On the A100 (2026-10-01), 6 of 12 passed in the full run. The A100 is slower, and SIE loaded its models during the run.
- After the demo fixes, D-08 passes. D-07 now gives an answer, but the docs page gave 404, so its claims have no evidence. D-03 got HTTP 500 `upstream_error` from `edge`, and the claim checks of D-09 timed out (`docs/results.md`, the demo check).

## Part 8. The questions, with a file or a scrape

| Question | Answer | Evidence |
|---|---|---|
| What is the app? Shared and unique tokens? (H-95) | Part 0 | the capture runs, Envoy logs |
| What dies at the guard, admit, place, and queue? (H-96) | Guard: 400 `prompt_injection` (22 of 22 attacks). Admit: 429 `tenant_tokens` (55 calls of the noisy tenant in E10). Queue: 503 `timeout_queue` (273 interactive at 150%). Place: 503 `no_endpoints` when no pod is ready (E8). | `docs/results.md`, E3, E8, E10, E17 |
| Where do we stop work that will time out? (H-97) | The band TTLs of the router flow control: 10 s for interactive and 120 s for batch. `edge` sets a TTL of half of the time left. | `control/router/policy.yaml:45` to `:48` |
| Where do we protect KV? (H-98) | The saturation detector stops the dispatch at a KV use of 90%. Then vLLM preemption is the last line (0 preemptions in all runs). | `control/router/policy.yaml:57`, E2, E3 |
| Where do we give priority to interactive traffic? (H-99) | The priority bands and `priority-holdback-policy`: batch waits above 70% saturation, interactive only at 100%. vLLM also uses `--scheduling-policy=priority`. At 100% load, the router shed 89 batch and 11 interactive calls. | `control/router/policy.yaml:47` to `:53`, `e3-c-100` |
| Where do we stop one tenant from taking all of the GPU? (H-100) | The Agent Router token windows. The noisy tenant got 429 `tenant_tokens` (55 and 53 calls in E10), and no 429 left to the overflow. The other tenants still had a TTFT p95 of 10 s or more, because 100% load in layout C is above the limit of the engine. | `control/router/policy.yaml:61` to `:68`, E10 |
| Where do we hop, and what is not copied? (H-101) | Part 6 | E4, `hops.jsonl` |
| Where do we evict, and what becomes a ghost? (H-102) | Part 6 | E7 |
| Where does the engine scheduler sit? (H-103) | Inside each vLLM pod, after our admit, place, and queue. We set its flags, and we do not change its code. | "Engine boundary" below |
| What limited concurrency? (H-104) | The decode pod (Part 1) | E3, E15 |
| Four production alerts (H-105) | `InteractiveTTFTBudgetBurn` (SLO-1), `ShedRateHigh` (more than 5% shed), `KVPressure` (KV above 92% or preemptions), and `HopFailures` (store-barrier timeouts or fail-open). Also `GuardUnavailable` and `EngineStalled`. | `cluster/manifests/base/monitoring/rules.yaml` |
| If we scale, which pool? (H-106) | The decode pool first: the decode pod is the bottleneck for our traffic. The prefill pool scales on uncached prefill tokens. In E9, the planner asked for the right pool, and KEDA added a pod 15 s later. | the planner rules, E9, `plots/e9-scale.png` |
| What changes at 10 times the traffic? Which three knobs are wrong? (H-107) | See below. | |

### At 10 times the traffic (H-107)

What we change:

1. More decode capacity first: more decode pods, or layout A pods. Few of our calls split, so the decode pod does most of the work.
2. 32 decode sequences on each pod (E15), and fp8 KV (E6: twice the tokens, and TTFT p50 0.23 s).
3. More CPU RAM for the LMCache server on each node, because the server keeps the prefixes of the sessions.

The three wrong knobs:

1. More prefill pods. The prefill pod had little work: at most 4,550 prompt tokens each second, against 16,200 on the decode pod.
2. A larger router queue or longer band TTLs. The requests then wait longer, and they still miss SLO-1.
3. A lower split threshold to send more work to the prefill pod. Each split pays the prefill, the hold, and the load from LMCache (E5: the TTFT of the split was higher for each prompt size, `docs/results.md`).

## Engine boundary (H-25, H-74, H-103, H-113)

We do not write our own scheduler. vLLM does continuous batching, chunked prefill, the waiting queue, preemption, and the KV blocks. We set its flags: `--max-num-seqs`, `--max-num-batched-tokens`, `--scheduling-policy=priority`, `--enable-prefix-caching`, and `--block-size` (`cluster/manifests/base/engine/vllm-*.yaml`). Our code decides what enters, where it goes, and when it waits: `edge`, the router config, the warm controller, and the store barrier.

## What the data changed in our design

1. Layout. For our traffic, two whole pods with prefix-aware routing beat one prefill pod and one decode pod. Most calls have a long cached prefix, so few calls split (10% to 13% in E3). We keep the split for long uncached prompts. The A100 runs gave the same result. A P/D layout needs two or more pods in each pool. One decode pod (E8) or one prefill pod (fault 35) is a single point of failure.
2. The replay is warmer than first-time traffic. In the capture, 40% of the calls had 2,048 or more new tokens. In the E3 replay, 12% had. First-time traffic gives the prefill pod more work.
3. A ramp needs a cap, not only a score. The ramp scorer did not stop the queue scorer, which sent 75% of the calls to an empty pod (E8).
4. The hop needs a store barrier (ADR-002, revision 3).
5. vLLM v0.30.0 does not count client aborts in `vllm:request_success_total{finished_reason="abort"}`. We use the Envoy log as the evidence.
6. Some values depend on the GPU. The prefill capacity of the planner is 10,500 tokens each second on the H100 and about 1,800 on the A100. The warm baseline (the TTFT of a 4K prompt) was 0.35 s on the H100. On the A100, the probe took about 1.17 s, and we set the baseline to 1.0 s. With the H100 values on the A100, no engine pod got the warm label, and the prefill pool did not grow (E9). These values must come from the GPU of the node.
7. A clear of the prefix cache is an event that the router must apply. vLLM sends `AllBlocksCleared`, and the router did not act on it (E7). Until the router applies it, each warm session pays one ghost.

## Faults that we found and fixed

The session logs list each fault with its fix and its test. Faults 23 to 34 came from the H100 sessions, and faults 35 to 37 came from the A100 session (`metrics/thu-one-session.log`). Fault 38 came from a check of the evidence for the talk. Examples:

| Fault | What happened | Fix |
|---|---|---|
| 24 | The HAMi webhook sent a new engine pod to the HAMi scheduler, and the pod stayed Pending. | The label `hami.io/webhook: ignore` on the engine pods. |
| 25 | Envoy gave 413 for prompts of about 12K tokens (a 32 KiB buffer). | A `ClientTrafficPolicy` with a 4 MiB buffer. |
| 26 | Prompt Guard gave HTTP 500 under concurrent page checks (the fast tokenizer in two threads). | A lock around the model work. |
| 29 | The AI Gateway stopped each request at 60 s. | `timeouts.request: 300s` on the route. |
| The hop race | The decode lookup ran before LMCache ended the store. | The store barrier. |
| 34 | The warm-controller series lost the engine pod label (Prometheus renamed it to `exported_pod`). | `honorLabels: true` on its ServiceMonitor. |
| 35 | Under overload on the A100 (`e3-c32-50`), the split calls got 503 `no_endpoints` from 86 s, and the metrics of the prefill engine stopped. At 122 s, the memory of its GPU fell to 0: the engine process stopped. A new engine loaded the model, and the pod was warm again at 280 s. In this time, 60 split calls failed. | Not fixed. The data agrees with a restart by the liveness probe (timeout 1 s), but we did not save the pod events. A second prefill pod prevents the 503s. |
| 36 | `POST /reset_prefix_cache` gave HTTP 200 with `{"success": false}` while blocks were in use. Thus the first E7 run with the approximate index had no clear. | `?reset_running_requests=true`, and the run records the answer (`clear-result.json`). |
| 37 | The capture skipped the Grafana images, because the panel check exited with 1 under `set -e`. | The capture continues after the check (`cluster/one.sh`). |
| 38 | The hop records (`tools/hop_records.py`) named the backend "nixl" for each run, but the LMCache server moved the KV in most runs. | The backend comes from the KV connector in `run.json`. We rebuilt the hop records of 37 runs from their Envoy logs, and only the backend field changed. |
| D-07 | The live page gave 404, the deadline gate stopped the agent, and the turn ended with no text. | One more call with no tools. If that call has no text too, the user gets a fixed message (`app/agent/middleware.py`, `app/service.py`). |

## Cost

The Lambda spend to 2026-10-01 was 237.43 USD of the 400 USD credit. The owner set a limit of 275 USD. The A100 block of 2026-10-01 took 3.88 hours and 86.51 USD. Each block is in `docs/budget-ledger.md`. The spend guard (`cluster/lambda/spend_guard.py`) stops each GPU at 23:00 PDT and at the hour and USD limits.

## Open items (2026-10-02)

1. Fault 35: a second prefill pod in layout C, and probes with a longer timeout. We have no GPU run for this change.
2. The fact check: claim checks that time out or find no evidence (D-03, D-07, D-09).
3. The values that depend on the GPU come from a manual change in the session. The bring-up must set them from the GPU type.
4. The router does not act on `AllBlocksCleared` (E7).
5. The slides, the public repo, the review of the checklist with the owner, and the submission before 2026-10-10.
