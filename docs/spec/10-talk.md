# The talk (2026-10-10)

Status: built on 2026-10-03 as a private claude.ai slide deck. `tools/make_deck.py` writes its files. The owner chose option A (one request, end to end) with the findings of option C (what the data changed).

## 1. Frame

| Item | Value |
|---|---|
| Date and slot | Saturday 2026-10-10, the class slot: 15:30 UTC, 08:30 PDT. |
| Length | 10 minutes at most. We plan 8 minutes of slides and 1 minute of video, so 1 minute stays free. |
| Speaker | Chetan Anand (the name on the title slide). |
| What the handout asks | Walk through the code and give the reason for each choice (L517, L732). Walk briefly through the eight dashboards (L734 to L756). Say if the limiter hypothesis was right (L588). The design needs a scrape of a live engine and a proof of a hop or a warmup (L525). |
| Format | A claude.ai slide deck, 16:9. The submission is the GitHub link and the Part 5 notebook (L834), not the slides. |
| Live demo | No. GPU stock is not certain on the day. The owner plays two short clips of the recordings from the laptop, with a shared screen. |
| Language | ASD-STE100 for all slide text and all speaker notes (U-01). |

## 2. The spine: one request, end to end

Slide 2 shows the architecture, so the owner does not switch to GitHub in the talk. Then the talk follows one app request along the course map (Class 9a): guard, stay or leave, admit, place, hop, warm, and scale. Each stop has the code, the reason, the proof, and one dashboard. Where the data changed a decision, the stop has one more line: "the data changed this". These lines are the findings of option C. Slide 11 collects them.

| # | Stop | Code | Reason | Proof | Dashboard | The data changed this | Time |
|---|---|---|---|---|---|---|---|
| 1 | Title | - | - | - | - | - | 0:10 |
| 2 | The architecture: our cluster on the course map | the system diagram of `README.md`, `cluster/` | Admission control decides what enters, and the router decides where it goes. vLLM runs the model. Two nodes, and one A100 node as the fallback. | The GPU plan of each layout | Cluster | - | 0:40 |
| 3 | The app and its tokens | `app/agent/`, `app/factcheck/` | RAG and an agent loop. The app has one LLM URL: `edge`. | An agent step finds 64% of its prompt in the cache. 60% of the calls have fewer than 2,048 new tokens. Clip 1: a verified answer. | - | - | 0:40 and clip 0:27 |
| 4 | Guard, and stay or leave | `control/edge/guard.py:105`, `control/edge/overflow.py:19` | The first "no" costs no GPU. 429, 500, and `slice_oom` stay. 503 and 529 may leave, to a named model with a limiter. | E17: 22 of 22 attacks blocked, 0 of 178 benign turns blocked. E13: the 124 interactive `timeout_queue` sheds may leave. No 429 left (E10). | Success and failures | - | 0:45 |
| 5 | Admit | `control/router/policy.yaml`: tenants and flow control | Token windows for each tenant. We shed at the door before the KV fills. | E10: the noisy tenant got 429 `tenant_tokens`. No run preempted a request. | Gateway and admission (sheds by reason) | - | 0:40 |
| 6 | Place, and the ghost | `policy.yaml` weights, `control/router/render.py:50` | Prefix first, then load. Queue depth is a scorer. Stale metrics count as saturated. | E11: a frozen snapshot pulled 80% of the work to one pod. | Router, and queue depth by pod | E7: after a cache clear, the router still sent each warm session to the cleared pod (15 of 15, and 10 of 10). | 0:50 |
| 7 | Queue and hop | `policy.yaml` (2,048 uncached tokens), `control/barrier/proxy.py:96`, `tools/hop_records.py` | Split only long uncached prompts. The LMCache server is the hop store: a copy of the KV chunks in CPU RAM. The LMCache connector in vLLM moves the bytes, and our code records the hop. | E4: 0.52 to 0.78 s, against 4.1 to 4.3 s for NIXL over TCP. One hop record: source, destination, tokens, backend. | Hop store (LMCache) | The store barrier. With no barrier, the decode lookup had 0 hits in 10 tries (G1). Completion is not visibility. | 0:55 |
| 8 | Declare warm | `control/warm/logic.py:32` and `:36` | Weights on the GPU do not make a warm pod. Probe, label, then ramp while the p99 holds. | E8: the first-minute TTFT p95 fell from 10.9 s to 7.3 s. The warmup added 15 s to the outage. | vLLM | The ramp needs a cap. In the first 10 s, the empty pod got 75% of the calls. | 0:50 |
| 9 | The hypothesis and the topology | - | We expected prefill compute (RAG) and KV blocks (agents) to be the limit. | The decode pod was the limit: 16,200 against 4,550 prompt tokens each second. E3: TTFT p50 0.84 s for two colocated replicas, against 4.59 s for P/D (100% load). The A100 runs agree. | the E3 charts | Two colocated replicas for our traffic. Two or more pods in each pool (E8, fault 35). | 0:55 |
| 10 | Scale: the planner names the pool | `cluster/manifests/base/monitoring/rules.yaml:18` and `:22`, KEDA | Uncached prefill tokens go to the prefill pool. Running sequences go to the decode pool. | E9: KEDA made the pod 15 s after the request. The pod was warm after about 4 minutes. Clip 2: the replica panel in E9, at 8 times speed. | Pods, replicas, and KEDA | Values that depend on the GPU. With the H100 capacity, the A100 prefill pool did not grow. | 0:45 and clip 0:34 |
| 11 | What the data changed, 10 times the traffic, and the cost | `DESIGN.md` | - | The five changes. At 10 times the traffic: more decode capacity, 32 sequences and fp8 KV, and more CPU RAM for the LMCache server. Three wrong knobs: more prefill pods, longer queues, and a lower split threshold. 237 USD of the 400 USD credit. | - | the summary | 0:50 |

Total: 8 minutes of slides and 1 minute of clips. The talk stays below 10 minutes only with practice, because each slide has about 45 seconds.

### 2.1 The deck as built (2026-10-03)

Two changes from the outline: the deployment has its own slide (slide 3), and the hypothesis and the topology have one slide each. After the first review (2026-10-03), each result slide says what we measured, and the slides have no internal labels such as experiment ids. The architecture slide is a flow with numbered steps, in the Mermaid style.

After the fourth review (2026-10-03), `tools/arch_diagram.py` places each box by hand. The top frame is "Admission control + routing": the gateway admits, and the router decides where. The router sends back only the addresses of the picked pods, and Envoy sends the request. The sidecar and vLLM are two boxes, so each box has one action. The KV cache of each vLLM stays in GPU memory, and the LMCache server holds a copy of the KV in CPU RAM.

The steps are 1 to 6 for each call. Steps 5a to 5c are orange and dashed. They occur only when the router also picked a prefill pod, and a key on the diagram says so. The diagram has no overflow API, because the overflow was off in all runs.

The time of each slide comes from the words of its notes, at 130 words each minute.

| # | Slide id | Title | Dashboard or chart | Time |
|---|---|---|---|---|
| 1 | `cover` | A learning companion on a scarce GPU | - | 0:13 |
| 2 | `intro` | The app: a learning companion over my bookmarks | the flow of one user turn, clip 1 | 0:38 and 0:27 |
| 3 | `arch` | The path of one LLM call | a flow in the Mermaid style: each box says what it decides or does, each arrow what moves (`tools/arch_diagram.py`) | 2:03 |
| 4 | `models` | The models, and the job of each | a table: the job, the model, and where it runs | 1:10 |
| 5 | `search` | The bookmark search: ingest once, then search in each turn | two flows: the ingest and the search | 1:13 |
| 6 | `capacity` | KV on paper: bytes for each token, and how many sequences fit | a table: the KV of one sequence and the max sequences at three lengths, and the KV cache that vLLM measured | 0:39 |
| 7 | `deploy` | The deployment: two nodes, and an A100 fallback | Dashboard 1, Cluster: GPU use | 0:40 |
| 8 | `design` | The cluster design: each choice, its reason, and the proof | a table: each choice, its reason, and the proof | 2:53 |
| 9 | `app` | What the app sends: short agent steps with a cached prefix | the token table, with a key | 0:44 |
| 10 | `guard` | Guard and stay or leave happen before any GPU work | Dashboard 3: guard rejects. Dashboard 2: the leave gate. | 1:07 |
| 11 | `admit` | Admit: we refuse work at the door, not in the engine | Dashboard 3: tenant rejects in the tenant test | 1:44 |
| 12 | `place` | Place: prefix match first, then load | Dashboard 4: P/D decisions. Dashboard 5: queue depth for each pod. | 0:35 |
| 13 | `hop` | The hop: the KV moves through the LMCache server | Dashboard 7: LMCache lookups, and one hop record | 3:43 |
| 14 | `warm` | A pod with its weights on the GPU is not warm yet | the restart test chart | 0:39 |
| 15 | `scale` | Scale: the planner names the pool | Dashboard 8: desired against actual replicas, clip 2 | 0:47 and 0:34 |
| 16 | `hypothesis` | The decode pod was the limit, not prefill compute | Dashboard 6: prompt tokens each second | 0:33 |
| 17 | `topology` | For our traffic, two colocated replicas beat a P/D split | the layout test chart | 0:44 |
| 18 | `questions-1` | The handout questions: our answers and the evidence (1 of 2) | a table: the question, our answer, and a file or a scrape | 2:53 |
| 19 | `questions-2` | The handout questions: our answers and the evidence (2 of 2) | a table: the question, our answer, and a file or a scrape | 2:53 |
| 20 | `changed` | What the data changed in our design | - | 0:41 |

Total: 26 minutes 33 seconds of notes and 61 seconds of clips, so 27 minutes 34 seconds.

The appendix has 11 slides, in the order of the handout parts. First come place, queue, hop and warmth, the two hop slides, the scale test chart, and the traps of the handout. Then the evidence: a raw scrape, the faults, the demo check, and the cost.

On 2026-10-09, each Grafana panel caption got the name of its dashboard, 1 to 8, in the order of the handout. The `scale` slide shows dashboard 8 (desired against actual replicas) in place of the scale test chart, which moved to the appendix. The repo link is on the cover and on the last slide.

Slide 2 is now a plain introduction of the app, with clip 1, before the architecture. The token slide (`app`) explains its table. Its notes say what 100% load means and how the agent steps differ from the verify steps. The deck gives the load in turns each minute (100% load is 54), not in turns each second.

Slide 4 names each model, its job, and where it runs.

Slide 5 shows the bookmark search. The ingest ran once before all tests. In each turn, SIE embeds the question and reranks what Qdrant finds. The diagram on slide 3 names the LLM and the two guard models. `tools/deck_pdf.py` writes the deck to `docs/talk/deck.pdf` and the speaker notes to `docs/talk/notes.md`. Run it after each change to the deck.

Slide 6 (`capacity`) shows the KV math of Part 1. It comes after the bookmark search and before the deployment. It gives the bytes for each token. A table gives the KV of one sequence and the maximum number of sequences on one H100 at three lengths. The lengths are 5,121 tokens (the median prompt of the app), 8K, and the 32K max_len.

Slide 6 also shows the KV cache that vLLM gave the decode pod, and the effect of a model switch. The hypothesis slide (slide 16) says which limiter we expected and if our guess was right.

The admit slide has a box: "Redis holds the tenant counts. The queue is in the flow control of llm-d. Flow control is the admit part of llm-d." Redis is not a queue. The notes of the guard slide say that edge keeps each guard verdict in Redis for 1 hour, and that Redis holds the overflow limits. The notes of the admit slide give the four jobs of Redis and the two priority bands of the llm-d queue.

The llm-d box of the architecture diagram shows the two jobs of llm-d. Its flow control is the last admit check: it holds a call while the pods are full. Its scheduler decides where the call goes. The diagram, its notes, and the admit slide say "llm-d", not "the router". The README diagram shows the same two jobs.

All slides and notes now say "llm-d", not "the router". Where the job matters, they say "the llm-d flow control" (admit) or "the llm-d scheduler" (where). Only the file paths in `control/router/` and the handout name "Dashboard 4 · Router" keep the word. Appendix A1 names the Envoy AI Gateway, not the Agent Router.

The deck, the report, the README, the results, the notebook, and the E3 chart now say "two colocated replicas". This is the term of the handout, in place of "two whole pods". A colocated replica is one vLLM pod that does the prefill and the decode of its calls. The session logs in `metrics/` keep the old words.

On the night of 2026-10-09, the deck got a new order: 20 main slides in four sections, and the appendix. The sections are the app and the system, capacity and the cluster design, one request end to end, and the results with the handout questions. Three slides moved from the appendix into the main deck. They are the cluster design (`design`, slide 8) and the two handout question slides (`questions-1` and `questions-2`, slides 18 and 19). The scale slide now comes before the results. `tools/make_deck.py` holds the order in two lists, `MAIN` and `APPENDIX`, and the cross-references take the slide numbers from them.

Two checks run before each publish. The STE lint checks all slide text and notes (0 errors, 0 warnings). A number check finds each number of a slide in the report, or in a file that the slide names.

## 3. A shorter talk

For a slot of about 7 minutes, make three cuts. Remove clip 2. Put slides 4 and 5 on one slide. Move the 10-times part of slide 11 to the appendix.

## 4. The appendix (for questions only)

| # | Slide id | Title | Evidence |
|---|---|---|---|
| A1 | `a-place` | How llm-d places a call: the policy and the scorers | `control/router/policy.yaml:15` and `:36`; `control/router/render.py:50`; `DESIGN.md`, Part 4 |
| A2 | `a-part5` | The queue questions: our answers and the proof | `notebook/part5_queue.ipynb` |
| A3 | `a-warm` | The hop record, and a cold pod against a warm pod | `metrics/e3-c-150/hops.jsonl`; `notebook/part5_queue.ipynb`, the restart test; `DESIGN.md`, Part 6 |
| A4 | `a-hop` | The hop: the LMCache server against NIXL | `plots/slides/hop.png` |
| A5 | `a-production` | The hop at production scale: what we keep, what we change | `DESIGN.md`, Part 6 |
| A6 | `a-scale` | The scale test: the planner against KEDA, in each pool | `plots/slides/e9.png` |
| A7 | `a-bad` | Traps that the handout names, and what our design does | section 5 of this file |
| A8 | `a-scrape` | A raw /metrics scrape of a live engine | `metrics/one-20261002T050552Z/scrape-*.txt` |
| A9 | `a-faults` | Faults that we found and fixed | `DESIGN.md`, faults |
| A10 | `a-demo` | The demo questions: 8 of 12 on the H100, 6 of 12 on the A100 | `docs/results.md`, the demo check |
| A11 | `a-cost` | The cost of each GPU block | `docs/budget-ledger.md` |

The main slide 8 (`design`) holds the defense of the cluster design. Each row has the choice, the reason, and the proof. The rows are the GPU, the model, the topology, the slices, the concurrency, the hop backend, the overflow, the two boxes, and the scale. Its notes give the answer to the question that each row can get, for example "why not a cheaper GPU?" or "why not Mooncake?".

The main slides 18 and 19 (`questions-1` and `questions-2`) answer the 13 questions of Part 8. Each row has a full answer and a file or a scrape. The notes give the details of each answer.

Slide A1 answers the place questions. All runs used the policy `prefix_then_load`. The decode profile and the prefill profile have different scorers. Queue depth is a scorer in both profiles, and the flow control also uses it as an admit input. The llm-d scheduler has no p2c picker.

Slide A2 answers the queue questions with a full sentence and a measurement for each. Its notes say that our queue is the queue in the flow control of llm-d, the admit part of llm-d. We did not write a second queue. The notes also give the fullness rule of llm-d, and why llm-d puts the queue before the pick.

Slide A3 answers the hop and warmth questions. It shows one hop record from the Envoy log. It also shows the restart test with and without the warmup: a first-minute TTFT p95 of 10.9 s against 7.3 s.

## 5. The bad answers of the handout, and our answers

Appendix slide A7 shows this table: "Traps that the handout names, and what our design does". Its notes explain each row (handout L799 to L832).

| The trap | What our design does, and the proof |
|---|---|
| A benchmark at batch 8 is the production SLO. | Our SLO comes from the app: interactive TTFT p95 at most 1.5 s up to 8K tokens, on a warm pod, with recorded app traffic. |
| The cache is full, so add a replica of the same size. | First make the KV smaller (FP8 KV: twice the tokens) and keep the prefixes in LMCache. Then scale the pool that the planner names. |
| NCCL or NIXL in this repo moves the KV. | The LMCache connector in vLLM moves the KV. Our code records the hop and holds the prefill answer until the store ends. |
| A replica is ready when the weights are on the GPU. | A pod is warm only after the warmup and a probe. The warmup cut the first-minute TTFT p95 from 10.9 s to 7.3 s. |
| The overflow is another API, with no model and no limiter. | `qwen3.8-27b` on the Superlinked API, only for an interactive 503 or 529. A Redis limiter caps the requests, the tokens, and the cost. |
| I wrote my own vLLM scheduler in the gateway. | No. vLLM schedules inside each pod, and we only set its flags. The gateway decides what enters, the wait order, and the pod. |
| A 429 that left the cluster. | The leave gate keeps each 429. At 150% load, it let only 124 interactive 503 calls go. |
| The gateway fixed OOM. | No. vLLM manages the GPU memory. The gateway keeps the load below preemption, and a prompt that is too long gets a 413. |
| RAG is a third phase. | No. The search runs outside the LLM, with SIE and Qdrant. Its chunks are prompt tokens: the engine sees only prefill and decode. |
| Wall seconds across models, with no token counts. | We compare models for each token: KV bytes and the time between tokens. Each load test replays the same traffic in each arm. |
| The TTFT of a cold pod as the SLO. | Our SLO runs use warm pods. The first minute of a new pod is a separate measure: 10.9 s cold, 7.3 s warm. |

## 6. The clips

The recordings have no audio. The owner plays the clips from the laptop, with a shared screen, and speaks over them. We work on copies in a temporary folder and never change the recordings.

| Clip | Recording | Part | What it shows | Length |
|---|---|---|---|---|
| 1 | "Verified Claims Recording" (H100, 2026-09-29, 19:31 PDT) | 2:41 to 3:08 | The question about Character.AI, the draft, the claim check, and the final answer. Both claims show "verified", each with a live source. No claim in this recording timed out. | 27 s |
| 2 | "A100 with prefill decode scaling" (E9, 2026-10-01, 21:11 PDT) | 1:40 to 6:08, at 8 times speed | The panel "Desired against actual replicas". The planner asks for a second decode pod (1:57). The pod is available about 3.5 minutes later. The Grafana time axis shows the real clock. | 34 s |

Clip 2 has two versions: the full dashboard, and a zoom on the one panel. The zoom is easier to read on a shared screen. The speaker says "8 times speed".

The files are in the Google Drive folder of the owner, `Class 11 - Project/talk-clips/`, next to the recordings. They are not in the repo:

| File | Length | Note |
|---|---|---|
| `clip1-verified-answer.mov` | 27 s | 60 frames each second |
| `clip2-e9-scaleout-8x-zoom.mov` | 33.5 s | The panel only. During four moments of scrolling in the recording, the clip holds the last frame. |
| `clip2-e9-scaleout-8x-full.mov` | 33.5 s | The full dashboard, with the scrolling of the owner |

## 7. The steps

| Step | What I do | Time |
|---|---|---|
| 1 | This outline. The owner reviews it and says yes, or gives changes. | now |
| 2 | The two clips (done on 2026-10-02, for the review of the owner). | - |
| 3 | Slide versions of the plots: `tools/slide_plots.py` writes `plots/slides/` (E3, the hop, warmup and ramp, E9) from the same saved data, so no number changes. The system diagram and the dashboard images become slide images. | started |
| 4 | The deck: 11 slides and 6 appendix slides, with the speaker notes. | 2 hours |
| 5 | The checks before the first publish: the STE lint on all slide text and notes (0 errors). Each number on a slide is in `docs/results.md` or `DESIGN.md`, and each note names its source. The time of each slide comes from the words of its notes. | 30 minutes |
| 6 | I publish the deck (private) and give the link to the owner. The owner reviews it, and I make the changes. | - |
| 7 | A practice run: the owner reads the notes aloud with a timer, and I cut words where a slide is too long. | - |

I check the layout of the slides in the browser only if the owner says yes. Anyone who opens the deck can read the speaker notes, so they contain no secret.
