# The talk: slides and speaker notes

The slides are in `deck.pdf`. `tools/make_deck.py` writes the deck, and `tools/deck_pdf.py` writes these two files. The talk is on 2026-10-10.

### 1. A learning companion on a scarce GPU

I built a learning companion over my bookmarks, on my own vLLM cluster. I walk one request through the code, and at each step I show what we measured.

### 2. The app: a learning companion over my bookmarks

First, the app. My Notion database has 998 bookmarks of pages that I want to learn from. The companion answers my questions from them and cites each source. In quick mode, one agent searches the bookmarks, reads pages, and reads figures with OCR. In verified mode, a second agent checks up to 3 claims on live web pages, and our code decides when a live fact wins.

Each LLM call of these agents goes to our own vLLM cluster. Now clip 1.

### 3. The path of one LLM call

This is the path of one LLM call. Admission control decides if the call may enter, and the router decides where it goes. Each box decides or does one thing, and each arrow names what moves. 1: the app sends the request to edge, our code. 2: edge asks the guard models if the prompt is safe.

3: edge sends the request to the Envoy AI Gateway, which checks the token budget of the tenant. 4: Envoy sends the prompt to the llm-d router, and the router sends back only the pod addresses. 5: Envoy sends the request to the decode pod. Each request enters this pod through the llm-d routing sidecar, a small proxy next to vLLM. The router only decides, so the sidecar runs the steps.

For most calls, the router picks no prefill pod, and the sidecar passes the request straight to vLLM. The orange steps occur only when the router also picked a prefill pod. 5a: the sidecar sends the prompt to the prefill pod and asks for only one token, so that pod only computes the KV. 5b: vLLM there sends a copy of the KV to the LMCache server. 5c: the store barrier replies only after the store.

6: the sidecar sends the request to its own vLLM, which loads the stored KV and generates the answer. The sidecar never moves the KV itself. In our mode, the KV goes through the LMCache server.

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

### 8. What the app sends: short agent steps with a cached prefix

This is what the app sends to the cluster. The second agent has two parts. The agent steps pick the claims and write the final answer. The verify steps check one claim each, on the web. An agent step found 64% of its prompt in the cache, and 60% of the calls had fewer than 2,048 new tokens.

In the load tests, we replay recorded app turns. A turn is one question with all its LLM calls. 100% load is 54 turns each minute, on average: the rate where the soak test refused its first call.

### 9. Guard and stay or leave happen before any GPU work

Stop one is the guard. `inspect()` runs fixed rules on the CPU, then Prompt Guard 2 and NeMo Guardrails. We sent 200 chat turns, 22 of them attacks. The guard blocked all 22 attacks and no normal turn. Stop two is stay or leave.

`should_leave()` keeps 429, 500, and slice_oom on our cluster. Only an interactive capacity refusal may leave. At 150% load the gate let only the 124 interactive 503 calls go, and no 429.

### 10. Admit: we refuse work at the door, not in the engine

Stop three is admit. The policy file holds our numbers. The Envoy AI Gateway counts the tokens of each tenant. The router keeps two priority bands with a time limit, and it stops the dispatch at 90% KV use. In all load tests the engine preempted nothing, because the router refused the extra work first.

In the tenant test the noisy tenant got 55 429 replies, and the others kept their service.

### 11. Place: prefix match first, then load

Stop four is place. The router scores each pod: the prefix match counts most, then the session, the queue depth, the KV use, and the ramp. A pod with metrics older than 2 seconds counts as full. In the stale-metrics test, a frozen copy of the metrics of an empty pod pulled 80% of the work to that pod. And after a cache clear, the router still sent the warm sessions to the cleared pod.

### 12. The hop: the KV moves through the LMCache server

Stop five is the hop. The router splits a request only when 2,048 or more of its tokens are not in a cache. The sidecar then sends the prompt to the prefill pod and asks for only one output token. There is no vLLM request for a prefill only, and one token is the smallest request. The pass that computes the KV of the prompt also gives this token, so it costs almost nothing.

The decode pod does not use this token: it writes the whole answer itself. The LMCache connector in vLLM copies the KV chunks to the LMCache server in CPU RAM, and the decode pod loads them. Our code only records the hop, and the store barrier holds the prefill answer until the store ends. The barrier finds the prefill request by its shape: one token and no stream. In the hop test, the first token came in 0.52 to 0.78 seconds, against about 4 seconds for NIXL over TCP.

If someone asks if this is production quality: the one-token request is, and the barrier is not. Under load on the A100 node, the barrier hit its half-second cap on 72% to 99% of split calls. A production hop needs a store signal for each request, RDMA between nodes, and two or more pods in each pool.

### 13. A pod with its weights on the GPU is not warm yet

Stop six is declare warm. A pod with its weights on the GPU is not warm yet. The warm controller sends our system prompts, the shapes of our app, and a 4,000-token probe. The pod gets the warm label only if the probe is fast enough. Then it gets 10% of the traffic weight, and more while the TTFT holds.

In the restart test, the warmup cut the first-minute p95 from 10.9 to 7.3 seconds. The ramp cut it from 57.3 to 14.6 seconds.

### 14. The decode pod was the limit, not prefill compute

The handout asked which limit we expected first. We expected prefill compute for the RAG traffic and KV blocks for the agents. The answer: partly right. The decode pod was the limit. Most agent calls have fewer than 2,048 new tokens.

The router does not split them, so the decode pod runs their prefill too. At 100% load it processed 16,200 prompt tokens each second, and the prefill pod 4,550.

### 15. For our traffic, two whole pods beat a P/D split

This test sends the same recorded app traffic to two layouts, at three loads. Two whole pods with prefix routing beat one prefill pod and one decode pod at each load, on the H100 and on the A100. At 100% load the TTFT p50 was 0.84 seconds, against 4.59. The split still helps a long uncached prompt. But a P/D layout needs two pods in each pool: when the one prefill engine restarted, 60 split calls got no endpoint.

### 16. Scale: the planner names the pool

Stop seven is scale. The planner rules name the pool: uncached prefill tokens for the prefill pool, and running requests for the decode pool. KEDA reads them. Dashboard 8 shows the decode pods that the planner asks for, and the ready pods. The test allowed at most 2 pods in each pool.

In the scale test, KEDA made the new pod 15 seconds after the request, in both pools. The pod was warm about 4 minutes later, so on a spike that is the real reaction time. The capacity value must match the GPU. Now clip 2, at 8 times speed.

### 17. What the data changed in our design

Five things changed in the design because of the data. Two whole pods for our traffic. A store barrier for the hop. A cap for the ramp. Values like the planner capacity must come from the GPU.

And the router must apply a cache clear. At 10 times the traffic I add decode capacity first, with 32 sequences, fp8 KV, and more CPU RAM for the LMCache server. The wrong knobs are more prefill pods, longer queues, and a lower split threshold. The GPU time cost 237 dollars. Thank you.

## Appendix (for questions only)

### A1. The handout questions and their evidence (1 of 2)

For questions only. Each answer points at a file in the repo.

### A2. The handout questions and their evidence (2 of 2)

For questions only. Each answer points at a file in the repo.

### A3. A raw /metrics scrape of a live engine

For questions only. These lines come from the live engine, the edge, and the store barrier.

### A4. Faults that we found and fixed

For questions only. The session logs list each fault with its fix and its test.

### A5. The demo questions: 8 of 12 on the H100, 6 of 12 on the A100

For questions only. Twelve fixed demo questions test the whole app.

### A6. The queue questions, with the notebook answers

For questions only. The answers, with plots, are in notebook/part5_queue.ipynb.

### A7. The cost of each GPU block

For questions only. Each block is in docs/budget-ledger.md. A spend guard stopped each GPU at the limits.

### A8. The hop: the LMCache server against NIXL

For questions only. NIXL between two pods used TCP, because the nodes have no RDMA.

### A9. The scale test: the planner against KEDA, in each pool

For questions only. The decode pool scaled 15 seconds after the planner asked. The prefill pool scaled only after we set its capacity value for the A100.

### A10. The hop at production scale: what we keep, what we change

For questions only. The one-token request is production quality, and the barrier is not. Under load, it hit its half-second cap on most split calls, so a production hop needs a store signal for each request.
