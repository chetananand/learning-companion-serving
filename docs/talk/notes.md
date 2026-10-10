# The talk: slides and speaker notes

The slides are in `deck.pdf`. `tools/make_deck.py` writes the deck, and `tools/deck_pdf.py` writes these two files. The talk is on 2026-10-10.

### 1. A learning companion on a scarce GPU

I built a learning companion over my bookmarks, on my own vLLM cluster. I walk one request through the code, and at each step I show what we measured.

### 2. The app: a learning companion over my bookmarks

First, the app. My Notion database has 998 bookmarks of pages that I want to learn from. The companion answers my questions from them and cites each source. In quick mode, one agent searches the bookmarks, reads pages, and reads figures with OCR. In verified mode, a second agent checks up to 3 claims on live web pages, and our code decides when a live fact wins.

Each LLM call of these agents goes to our own vLLM cluster. Now clip 1.

### 3. The path of one LLM call

This is the path of one LLM call. Admission control decides if the call may enter, and routing decides where it goes. Each box says what it decides or does, and each arrow names what moves. 1: the app sends the request to edge, our code. 2: edge asks the guard models if the prompt is safe.

3: edge sends the request to the Envoy AI Gateway, which checks the token budget of the tenant. 4: Envoy sends the prompt to llm-d. The llm-d box shows two jobs. Its flow control is the last admit check: it holds the call in a queue while the pods are full. Then its scheduler picks the pods, and llm-d sends back only the pod addresses.

5: Envoy sends the request to the decode pod. Each request enters this pod through the llm-d routing sidecar, a small proxy next to vLLM. The sidecar runs the steps, because llm-d only decides. For most calls, llm-d picks no prefill pod, and the sidecar passes the request straight to vLLM. The orange steps occur only when llm-d also picked a prefill pod.

5a: the sidecar sends the prompt to the prefill pod and asks for only one token, so that pod only computes the KV. 5b: vLLM there sends a copy of the KV to the LMCache server. 5c: the store barrier replies only after the store. 6: the sidecar sends the request to its own vLLM, which loads the stored KV and generates the answer. The sidecar never moves the KV itself.

In our mode, the KV goes through the LMCache server.

### 4. The models, and the job of each

These are the models. One LLM, Gemma 4 31B in FP8, runs all agent steps on vLLM. It fits one H100, and it passed all 7 of our gate tests, with 97.5% correct tool calls. Small models do the rest: two guard models, and the search and OCR models in SIE.

### 5. The bookmark search: ingest once, then search in each turn

This is the bookmark search. The ingest ran once, as a job in the cluster, before all tests. It fetched each bookmarked page and cut the text into chunks. The page check hid any part of a page that looked like an order to the LLM. The check has a limit.

In our test with 50 injected pages, it caught only half of them, even at a more sensitive setting. It flagged no real page. In the fact check, a second layer helps: code, not the LLM, decides when a live page wins over a bookmark. So a page cannot argue its way to a win. Then SIE turned each chunk into a vector in Qdrant.

In each turn, SIE turns the question into a vector. Qdrant finds 30 chunks by vector and by keyword, and SIE reranks them. The agent gets the top 8. SIE serves only the small models, so the search makes no LLM call.

### 6. KV on paper: bytes for each token, and how many sequences fit

Before the cluster, we did the KV math. Gemma 4 31B has two kinds of KV. The full-attention layers need 40,960 bytes for each token. The sliding-window layers add 800 MiB for each sequence, after 1,024 tokens. At the length that our app sends, about 5,000 tokens, one H100 fits 36 sequences, and 17 at the 32K max_len.

FP8 KV doubles both. We kept the model, because it passed all gate tests. With Muse Glimmer, the KV of an 8K sequence is 83% smaller.

### 7. The deployment: two nodes, and an A100 fallback

The deployment has two nodes on Lambda. Node 1 has two H100 GPUs for the engine: one prefill pod and one decode pod, each on a full GPU. The LMCache server may use up to 250 GiB of the 450 GiB of CPU RAM on node 1. In the A100 tests, it held up to 214 GiB. Node 2 runs everything else, and HAMi slices one GPU for the small models.

On 2026-10-01 no H100 had stock, so one node with eight A100 GPUs ran all pods.

### 8. The cluster design: each choice, its reason, and the proof

Each row gives a choice of the cluster design, the reason, and the proof. GPU: why not a cheaper GPU? An A6000 holds the weights, but it leaves only 6.8 GiB for KV, and it has no FP8 compute. An A100 has no FP8 compute either, so the prefill of 8K tokens takes 3.95 seconds on paper. The H100 SXM is the smallest GPU that meets the TTFT goal, and it gives two GPUs with NVLink on one node.

On 2026-10-01, no H100 had stock, so one A100 node ran the tests. Model: we kept Gemma 4 31B, because it passed all 7 gate tests. Topology: why a split, when two colocated replicas were faster? On paper, the split keeps long prompts away from the decode steps, and each pool gets its own scale signal. The data showed that most agent calls are short, so for our traffic two colocated replicas win.

Slices: vLLM gets full GPUs, because the KV needs all the free HBM. The course notes also say: do not split prefill and decode on one sliced GPU. Concurrency: why 24? At 8K tokens, 32 sequences fit, and at 24K tokens, about 20 fit. We chose 24, between them.

The data showed that 32 is better, and no pod preempted. Hop: why not Mooncake? The handout names it, but it had no Gemma 4 test, and our Lambda nodes had no RDMA. NIXL over TCP took more than 4 seconds. The LMCache server gave a hop TTFT of 0.52 to 0.78 seconds.

Overflow: only an interactive call that llm-d refused for capacity may leave. A 429 never leaves. The overflow was off in all runs, so these calls got a 503. Two boxes: the handout puts admit, place, and the queue in the gateway. So llm-d, with its flow control and its scheduler, is in the gateway box, and vLLM is the engine box.

Scale: we scale the pool that is the limit. Uncached prefill tokens grow the prefill pool, and running sequences grow the decode pool.

### 9. What the app sends: short agent steps with a cached prefix

This is what the app sends to the cluster. The second agent has two parts. The agent steps pick the claims and write the final answer. The verify steps check one claim each, on the web. An agent step found 64% of its prompt in the cache, and 60% of the calls had fewer than 2,048 new tokens.

In the load tests, we replay recorded app turns. A turn is one question with all its LLM calls. 100% load is 54 turns each minute, on average: the rate where the soak test refused its first call.

### 10. Guard and stay or leave happen before any GPU work

Stop one is the guard. `inspect()` runs fixed rules on the CPU, then Prompt Guard 2 and NeMo Guardrails. We sent 200 chat turns, 22 of them attacks. The guard blocked all 22 attacks and no normal turn. Edge keeps each verdict in Redis for 1 hour.

The key is a hash of the last user message. So the next LLM calls of the same turn do not call the guard models again. Edge never stores an outage. If Redis fails, edge calls the guard models. Stop two is stay or leave.

`should_leave()` keeps 429, 500, and slice_oom on our cluster. Only an interactive capacity refusal may leave. At 150% load the gate let only the 124 interactive 503 calls go, and no 429. Redis also holds the limits of the overflow API. The overflow was off in all runs, so these calls got a 503.

### 11. Admit: we refuse work at the door, not in the engine

Stop three is admit. The policy file holds our numbers. The Envoy AI Gateway counts the tokens and the requests of each tenant in each minute. Its rate-limit service keeps these counts in Redis. When a tenant is over its budget, it gets a 429 at once, with no wait.

Redis is not a queue. It has four jobs: the tenant counts, the guard verdicts, the overflow limits, and the web search results of the app. The queue is in the flow control of llm-d, in its own memory. Flow control is the admit part of llm-d: it decides if and when a call goes to a pod. Then the scheduler of llm-d decides which pod, and the place slide shows it.

The queue has two priority bands with a time limit: 10 seconds for an interactive call, and 120 seconds for a batch call. The pods are full at 5 queued requests or 90% KV use. Then the flow control holds the calls in the queue. In all load tests the engine preempted nothing, because the flow control refused the extra work first. In the tenant test the noisy tenant got 55 429 replies, and no other tenant got one.

But the TTFT p95 of the others stayed near 10 seconds, because at 100% load the P/D layout is above the limit of the engine.

### 12. Place: prefix match first, then load

Stop four is place. The llm-d scheduler scores each pod: the prefix match counts most, then the session, the queue depth, the KV use, and the ramp. A pod with metrics older than 2 seconds counts as full. In the stale-metrics test, a frozen copy of the metrics of an empty pod pulled 80% of the work to that pod. And after a cache clear, llm-d still sent the warm sessions to the cleared pod.

### 13. The hop: the KV moves through the LMCache server

Stop five is the hop. The llm-d scheduler splits a request only when 2,048 or more of its tokens are not in a cache. The sidecar then sends the prompt to the prefill pod and asks for only one output token. There is no vLLM request for a prefill only, and one token is the smallest request. The pass that computes the KV of the prompt also gives this token, so it costs almost nothing.

The decode pod does not use this token: it writes the whole answer itself. The LMCache connector in vLLM copies the KV chunks to the LMCache server in CPU RAM, and the decode pod loads them. Our code only records the hop, and the store barrier holds the prefill answer until the store ends. The barrier finds the prefill request by its shape: one token and no stream. In the hop test, the first token came in 0.52 to 0.78 seconds, against about 4 seconds for NIXL over TCP.

If someone asks if this is production quality: the one-token request is, and the barrier is not. Under load on the A100 node, the barrier hit its half-second cap on 72% to 99% of split calls. A production hop needs a store signal for each request, RDMA between nodes, and two or more pods in each pool.

### 14. A pod with its weights on the GPU is not warm yet

Stop six is declare warm. A pod with its weights on the GPU is not warm yet. The warm controller sends our system prompts, the shapes of our app, and a 4,000-token probe. The pod gets the warm label only if the probe is fast enough. Then it gets 10% of the traffic weight, and more while the TTFT holds.

In the restart test, the warmup cut the first-minute p95 from 10.9 to 7.3 seconds. The ramp cut it from 57.3 to 14.6 seconds.

### 15. Scale: the planner names the pool

Stop seven is scale. The planner rules name the pool: uncached prefill tokens for the prefill pool, and running requests for the decode pool. KEDA reads them. Dashboard 8 shows the decode pods that the planner asks for, and the ready pods. The test allowed at most 2 pods in each pool.

In the scale test, KEDA made the new pod 15 seconds after the request, in both pools. The pod was warm about 4 minutes later, so on a spike that is the real reaction time. The capacity value must match the GPU. Now clip 2, at 8 times speed.

### 16. The decode pod was the limit, not prefill compute

The handout asked which limit we expected first. We expected prefill compute for the RAG traffic and KV blocks for the agents. The answer: partly right. The decode pod was the limit. Most agent calls have fewer than 2,048 new tokens.

The llm-d scheduler does not split them, so the decode pod runs their prefill too. At 100% load it processed 16,200 prompt tokens each second, and the prefill pod 4,550.

### 17. For our traffic, two colocated replicas beat a P/D split

This test sends the same recorded app traffic to two layouts, at three loads. A colocated replica is one vLLM pod that does the prefill and the decode of its calls. Two colocated replicas with prefix routing beat one prefill pod and one decode pod at each load, on the H100 and on the A100. At 100% load the TTFT p50 was 0.84 seconds, against 4.59. The split still helps a long uncached prompt.

But a P/D layout needs two pods in each pool: when the one prefill engine restarted, 60 split calls got no endpoint.

### 18. The handout questions: our answers and the evidence (1 of 2)

These are the questions of the handout. Each answer points at a file or a scrape in the repo. The app is a learning companion over the bookmarks of its owner, with RAG and agent steps on Gemma 4 31B. The shared tokens are the system prompt, the tool schemas, and the history of a session. The unique tokens are the question, the retrieved chunks, the fetched pages, and the OCR text.

The Envoy logs of the capture runs give the numbers. An agent step finds 64% of its prompt in the cache, and 31% of all prompt tokens were in the cache. What dies where: the guard stops an unsafe prompt with a 400, before any GPU work. Admit stops a tenant over its budget with a 429. The queue stops a call that waits past its time limit with a 503.

Place gives a 503 when no pod is ready. Time limits: the llm-d queue has a time limit for each band. Edge sets the limit of each call to half of its time left. So a call that cannot finish in time leaves before it uses the GPU. KV: the llm-d flow control holds calls while the pods are full, so the KV of a pod stays near 90% or below.

vLLM preemption is only the last line, and no run preempted. Priority: interactive calls go before batch calls in the llm-d queue. Batch calls already wait at 70% fullness, and interactive calls only at 100%. vLLM also schedules by priority. At 100% load, llm-d shed 89 batch calls and 11 interactive calls.

One tenant: the Envoy AI Gateway counts the tokens and the requests of each tenant in Redis. A tenant over its budget gets a 429, and a 429 never leaves the cluster. In the tenant test, the noisy tenant got 55 429 replies, and no other tenant got one. But the TTFT p95 of the others stayed near 10 seconds, because 100% load in this layout is above the limit of the engine. The hop: llm-d splits a call at 2,048 or more uncached tokens.

The decode pod loads the KV from the LMCache server. It computes again only the tokens after the last full chunk of 256 tokens.

### 19. The handout questions: our answers and the evidence (2 of 2)

Evict and ghosts: vLLM evicts blocks of its GPU prefix cache when it needs space. The LMCache server evicts its oldest chunks at 90% of its 250 GiB cap. llm-d learns of each eviction from the KV events of vLLM. A ghost is a prefix that llm-d still places on a pod after the pod cleared it. We cleared the prefix cache of one pod during a run.

The pod sent one AllBlocksCleared event, and llm-d did not act on it. The next call of each warm session went to the cleared pod and missed the cache once, in 15 of 15 sessions. A ghost costs one prefill of the session history. The engine scheduler sits inside each vLLM pod, after our admit, place, and queue. The engine does continuous batching, chunked prefill, its waiting queue, preemption, and the KV blocks.

We only set its flags. The limit on concurrency was the decode pod. Most agent calls have fewer than 2,048 new tokens, so llm-d does not split them, and the decode pod also does their prefill. At 100% load it processed 16,200 prompt tokens each second, and the prefill pod 4,550. The alerts: an interactive TTFT budget burn, a shed rate above 5%, KV pressure, and hop failures.

KV pressure means a KV use above 92% for 5 minutes, or preemptions. We also alert when the guard is down or an engine stalls. Scale: decode first, because the decode pod is the limit. The planner asks for decode pods from the running sequences, and for prefill pods from the uncached prefill tokens. In the scale test, KEDA made a new decode pod 15 seconds after the request.

At 10 times the traffic: more decode capacity first, 32 sequences on each decode pod, and FP8 KV. With 32 sequences, the TTFT p50 fell from 4.59 to 2.10 seconds, and FP8 KV doubled the tokens of each pod. Also more CPU RAM for LMCache, because it keeps the prefixes of the sessions. The wrong knobs: more prefill pods, because the prefill pod had little work. A longer queue, because calls then wait longer and still miss the TTFT goal.

A lower split threshold, because each split pays the prefill, the hold, and the load from LMCache.

### 20. What the data changed in our design

Five things changed in the design because of the data. Two colocated replicas for our traffic. A store barrier for the hop. A cap for the ramp. Values like the planner capacity must come from the GPU.

And llm-d must apply a cache clear. At 10 times the traffic I add decode capacity first, with 32 sequences, fp8 KV, and more CPU RAM for the LMCache server. The wrong knobs are more prefill pods, longer queues, and a lower split threshold. The GPU time cost 237 dollars. Thank you.

## Appendix (for questions only)

### A1. How llm-d places a call: the policy and the scorers

For questions only. This slide shows how llm-d places a call. Two different things in llm-d answer two different questions. The queue order of the flow control answers: which waiting call goes next, and when? Interactive calls go before batch calls, tenants take turns, and the first in goes out first.

It works only while the pods are full and calls wait. The placement policy of the scheduler answers: to which pod does the call go? It is prefix_then_load, and it works for each call when the call leaves the queue. We set both in the policy file. They run one after the other: the queue order picks the next call, and then the scheduler picks its pod.

When the pods are not full, no call waits, and only the scheduler works. The policy file holds our numbers, and render.py turns them into the llm-d configuration. There are two scheduling profiles: one for the decode pool and one for the prefill pool. The decode profile runs first. The prefill profile runs only when llm-d splits the call.

Each profile first filters the pods: only warm pods, and only pods with its role. Then each scorer gives each pod a score, and the picker takes the pod with the highest weighted total. Load means how busy each pod is now, from the metrics of each vLLM pod. Queue depth gives 1 to the pod with the shortest queue and 0 to the pod with the longest queue. KV use gives 1 minus the KV use of the pod.

Token load gives 1 minus the tokens in flight, divided by a limit. Prefix match has weight 3, and each load scorer has weight 2. For example, pod A has the prompt in its KV cache, but it has the longest queue and 85% KV use: 3.3 points. Pod B has no prefix match, the shortest queue, and 30% KV use: 3.4 points. So pod B gets the call: the load beat the prefix.

Thus the name: prefix, then load. A return call of a session also gets the session score on its old pod. Prefix and session give up to 5 points, and the load at most 4. So a session almost always goes back to its pod, and only the flow control stops it when the pods are full. The two profiles use different scorers.

The decode pod keeps the KV of the running sequences, and the next call of a session can use it again. So the decode profile scores the session and the KV use. The prefill profile scores the token load, because a prefill costs compute for each new token. But the token-load scorer used the default limit of 4,194,304 tokens. Even our prefill test reached only about 14% of it.

So this score stayed high, and queue depth was the real load signal for prefill. Queue depth is a scorer in both profiles, and it is also an admit input. The flow control counts a pod with 5 queued requests as full. Why not p2c? The llm-d scheduler has no p2c picker.

With two pods in a pool, p2c compares both pods, so it is the same as least loaded. The stale-metrics test shows that the load scores matter. A frozen copy of the metrics of an empty pod pulled 80% of the work to that pod.

### A2. The queue questions: our answers and the proof

For questions only. Our queue is the queue in the flow control of llm-d, the admit part of llm-d. We did not write a second queue, and the handout does not ask for one. It puts admit, place, and the queue in the gateway. We set the rules of the queue in the policy file.

A call waits in our queue before llm-d picks a pod, and only while the pods are full. For this, llm-d gives each pod a fullness: its queued requests divided by 5, or its KV use divided by 90%, whichever is larger. The pods are full when the average fullness reaches 1. Batch calls already wait at 0.7. In our queue, interactive calls go before batch calls, and tenants take turns.

A call that waits longer than its time limit gets a 503: 10 seconds for interactive, 120 seconds for batch. The handout draws the queue after the pick. But llm-d puts it before the pick, so the pick uses the state of the pods at the moment that a pod has room. The vLLM waiting queue is inside each pod, after the pick. The engine moves a call from it into the running batch when a batch slot and KV blocks are free.

At 150% load, our queue held up to 48 interactive and 24 batch calls. The vLLM queue of the decode pod held up to 50. Two different things in llm-d answer two different questions. The queue order of the flow control answers: which waiting call goes next, and when? Interactive calls go before batch calls, tenants take turns, and the first in goes out first.

It works only while the pods are full and calls wait. The placement policy of the scheduler answers: to which pod does the call go? It is prefix_then_load, and it works for each call when the call leaves the queue. We set both in the policy file. They run one after the other: the queue order picks the next call, and then the scheduler picks its pod.

When the pods are not full, no call waits, and only the scheduler works. Batch calls in our app come from the freshness sweep: a job that checks the volatile claims of the bookmarks on the live web. All its calls use the batch class. Waiting, running, preempted: vLLM V1 has no swap. When the KV is short, it preempts a running call and computes its KV again later.

The decode pod ran at most 24 calls, its limit, and up to 50 waited. No pod preempted a call in any run. Queue depth for each pod: dashboard 5 shows the queue of each pod, as llm-d sees it and as vLLM reports it. At 100% load, the unique, shared-prefix, and stale-metrics mixes kept 6 or fewer. The mixed app traffic put up to 36 on the decode pod.

The long retrieve and the short steps: we sent one retrieve of about 27,400 tokens and five short agent steps at the same time. Each arm had five rounds. In all rounds, a short step got the first token first. With the split, the long prompt went to the prefill pod, and the short steps started in 0.25 seconds. Without the split, the long prompt shared the decode pod.

The engine computes a long prompt in chunks, so the short steps still went first. But they needed 1.0 to 1.26 seconds. The class of the retrieve, interactive or batch, did not change the order. At this low load, the pods were not full, so our queue did not hold the calls. PagedAttention and the prefix cache: PagedAttention packs the KV in blocks in all arms, and we cannot turn it off.

On the shared-prefix mix at 100% load, the KV use was 13% with and without the prefix cache. So the prefix cache did not save memory at this load. It saved compute: 44% of the prompt tokens were cache hits, and the TTFT p50 fell from 0.60 to 0.43 seconds. FP8 KV saved memory: the KV use fell to 5.5%, and each pod held twice the tokens. Engine flags: vLLM adds calls to the running batch at each step, up to max-num-seqs.

This is continuous batching. Chunked prefill cuts a long prompt into chunks of at most max-num-batched-tokens for each step. The decode pod has 24 sequences, because its KV holds about 20 sequences at 24K tokens and 32 at 8K. Its chunk of 2,560 tokens lets a prefill below the split threshold of 2,048 tokens run in one chunk. The engine does not accept less than 2,496 tokens, the largest image item of Gemma 4.

The prefill pod has 8 sequences, because its calls leave after the hop. Its chunks of 16,384 tokens give throughput. With 32 decode sequences, the TTFT p50 fell from 4.59 to 2.10 seconds. A prefill chunk of 8,192 tokens made it worse. KV full after admit: we refuse at the door.

The flow control holds calls before the KV of the pods is full. In the soak test, the load grew each minute. The first refusal came in minute 18, at 100% load. 3,645 calls were ok, and the flow control refused 43. The KV use stayed at 90% or less, and no pod preempted.

Client gone: no component sends a message. The close moves along the same connections, one hop at a time. The client closes its stream to edge, and edge closes its stream to Envoy. Envoy closes its stream to the routing sidecar, and the sidecar stops its request to vLLM. But llm-d is not on this path: it only picks the pod before the call goes.

The vLLM API server sees the closed connection and cancels the call. Its engine client then sends an abort for the call to the engine core. The scheduler marks the call as aborted and frees its KV blocks. We closed 20% of the streams in the middle: 46 calls. Envoy closed all 46 streams at the moment of the client close.

But vLLM v0.30 does not count these aborts in its success counter, so we have no vLLM metric for the last step. A pod returns: we ramp. The warm controller gives the pod a ramp label of 10%, then 25%, 50%, and 100%, while the TTFT p99 of the pod holds. The ramp scorer of llm-d reads the label. In the restart test, the first-minute TTFT p95 of the returned pod was 14.6 seconds with the ramp, and 57.3 seconds with a jump.

But the ramp is a score, not a cap. In the first 10 seconds, the empty pod got 75% of the calls, because the queue scorer likes an empty queue. So the ramp needs a cap.

### A3. The hop record, and a cold pod against a warm pod

For questions only. This slide answers the hop and warmth questions. Same pod: when llm-d picks only the decode pod, the KV is already there. The decode pod computes the prompt or finds it in its cache, so there is no hop and no record. Two pods: when llm-d splits a call, the prefill pod computes the KV and stores a copy in the LMCache server.

The decode pod loads it. The script hop_records.py writes one record for each split call, from the Envoy log. The record has the source pod, the destination pod, the tokens, the cached tokens, and the backend. In this record, the decode pod got 2,304 of 2,371 tokens from the cache. It computed the rest again: the tokens after the last full LMCache chunk of 256 tokens.

Is the new pod warm? A pod with its weights on the GPU is not warm yet. Its first calls can be slow, because its prefix cache is empty and some one-time work runs on the first calls. Warm the box and then re-quote the TTFT means this: send warmup calls first, and then measure the TTFT again. We did this as two arms of the restart test.

We deleted the one decode pod at 70% load. With no warmup, the first-minute TTFT p95 on the new pod was 10.9 seconds. With the warmup, it was 7.3 seconds. The warmup made the outage 15 seconds longer. In minutes 2 to 4, both arms had a p95 near 12.7 seconds, so the warmup helps only the first minute.

Our warmup routine sends our system prompts and the shapes of our app. A new decode pod also gets one split call through the prefill pod, so the hop path is warm too. Then a 4,000-token probe runs. The pod gets the warm label only if the probe TTFT is at most 1.5 times the warm baseline. Then the ramp starts.

In the scale test, each new pod got the warm label 225 to 235 seconds after the request.

### A4. The hop: the LMCache server against NIXL

For questions only. NIXL between two pods used TCP, because the nodes have no RDMA.

### A5. The hop at production scale: what we keep, what we change

For questions only. The one-token request is production quality, and the barrier is not. Under load, it hit its half-second cap on most split calls, so a production hop needs a store signal for each request.

### A6. The scale test: the planner against KEDA, in each pool

For questions only. The decode pool scaled 15 seconds after the planner asked. The prefill pool scaled only after we set its capacity value for the A100.

### A7. Traps that the handout names, and what our design does

For questions only. The handout names traps: bad answers that it marks down. This slide shows what our design does in place of each trap, and the proof. The benchmark: a fixed batch in a benchmark is not our SLO. Our SLO comes from the app.

The interactive TTFT p95 must be at most 1.5 seconds for prompts up to 8K tokens, on a warm pod. The load tests replay the recorded calls of our app. A full cache: a new replica of the same size starts with an empty cache, and it splits the prefixes between more pods. We first make the KV smaller: FP8 KV gave each pod twice the tokens. The LMCache server keeps the prefixes in CPU RAM.

Then we scale the pool that the planner names. The KV move: our code does not move KV bytes. The LMCache connector in vLLM moves them. Our code records the hop and holds the prefill answer until the store ends. Ready: a pod with its weights on the GPU is not warm.

The warmup cut the first-minute TTFT p95 from 10.9 to 7.3 seconds. The overflow: we name the model and the limiter. Only an interactive 503 or 529 may leave, and a Redis limiter caps the requests, the tokens, and the cost. The overflow was off in all runs. The scheduler: vLLM schedules inside each pod, and we only set its flags.

Our gateway decides what enters, the order of the waiting calls, and the pod. A 429: the leave gate keeps each 429 on our cluster. In the 150% test, it let only 124 interactive 503 calls go. OOM: the gateway does not fix the memory of the engine. vLLM manages the GPU memory.

Our gateway keeps the load below the point of preemption, and no run preempted. A prompt that is too long for the model gets a 413 at edge. RAG: the search is not a phase of the engine. It runs outside the LLM, with SIE and Qdrant. Its chunks become prompt tokens, so the engine sees only prefill and decode.

Wall seconds: we compare models for each token, with the KV bytes and the time between tokens. Each load test replays the same recorded traffic in each arm. A cold pod: our SLO runs use warm pods. The first minute of a new pod is a separate measure.

### A8. A raw /metrics scrape of a live engine

For questions only. These lines come from the live engine, the edge, and the store barrier.

### A9. Faults that we found and fixed

For questions only. The session logs list each fault with its fix and its test.

### A10. The demo questions: 8 of 12 on the H100, 6 of 12 on the A100

For questions only. Twelve fixed demo questions test the whole app.

### A11. The cost of each GPU block

For questions only. Each block is in docs/budget-ledger.md. A spend guard stopped each GPU at the limits.
