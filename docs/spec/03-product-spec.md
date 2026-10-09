# Product spec: the learning companion

Working name: **Learning Companion**. The name can change.

## 1. Purpose

The owner saved 998 links in the Notion Bookmarks database from 2017 to 2026. The companion answers the owner's questions from these bookmarks. Then it checks the facts against the live web. When the live web gives a newer or better fact, the answer uses the live fact and shows the change. The answer always cites its sources.

The course grades the serving shape, not the retrieval quality (L559). Thus the app must be real and useful, but its main job in this project is to make realistic traffic for the cluster.

## 2. Users and tenants

| Tenant | Who | Request class | Notes |
|---|---|---|---|
| `owner` | The owner in the chat UI | interactive | The only human user. |
| `sweep` | The freshness sweep job | batch | Track B batch: a background agent crew. |
| `load-*` | Load generator users | interactive or batch | Replay of app traces. |
| `noisy` | A test tenant that sends too much | interactive | Used to prove tenant isolation. |

## 3. User journeys

| ID | Journey | Priority |
|---|---|---|
| J1 | The owner asks a question in "quick" mode. The companion answers from the bookmarks with citations. | P0 |
| J2 | The owner asks a question in "verified" mode. The companion answers, checks the volatile claims on the live web, and marks each claim. | P0 |
| J3 | A result page has no useful text. The companion takes a screenshot of the page and reads it with OCR. | P0 |
| J4 | The owner uploads a screenshot and asks about it. | P1 |
| J5 | The owner asks "Which of my bookmarks are out of date?". The companion shows the report of the freshness sweep. | P1 |
| J6 | The acceptance run sends the demo questions (`08-question-bank.md`) through the full path. Each question must pass from end to end. It is a pass or fail check, not a scored eval. | P0 |

## 4. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | The ingest job reads all rows of the Bookmarks data source through the Notion API. It reads the `URL` page and the Notion page body. | P0 |
| FR-02 | The ingest job fetches each URL, extracts the main text, splits it into chunks, embeds the chunks, and writes them to the vector database. | P0 |
| FR-03 | The ingest job runs again only on rows with a newer `Updated` time or a changed content hash. | P1 |
| FR-04 | The ingest job marks dead links. It does not delete them. | P1 |
| FR-05 | Retrieval uses hybrid search (dense vectors and BM25) and a reranker. It returns the top k chunks (k = 8 by default). | P0 |
| FR-06 | The agent follows the workflow in section 5. | P0 |
| FR-07 | The fact check follows the rules in section 6. | P0 |
| FR-08 | The screenshot tool renders a page in headless Chromium and returns a PNG. | P0 |
| FR-09 | The OCR tool changes a PNG into Markdown text. | P0 |
| FR-10 | The UI streams the answer. It shows each agent step, each source, and the verdict for each claim. | P0 |
| FR-11 | The UI has a switch for "quick" and "verified" modes. | P0 |
| FR-12 | The UI accepts an uploaded image. | P1 |
| FR-13 | The app sends every LLM call to `edge`. It never calls a router or a vLLM pod directly. | P0 |
| FR-14 | The app records each LLM call shape (step, prompt tokens, shared-prefix tokens, output tokens, class) to a trace file for the load generator. | P0 |

## 5. Agent workflow

The agent runs on LangChain and LangGraph (ADR-006). Quick mode uses a LangChain agent (`create_agent`). Verified mode first runs the quick agent for the draft, so the draft shows inside SLO-3. Then a Deep Agent (`create_deep_agent`) with a `fact-checker` subagent checks the draft. Both modes make the two traffic shapes of the handout.

```text
quick:     search_bookmarks -> (fetch_page or screenshot_page, if necessary) -> answer_now -> answer (streamed)
verified:  the quick steps: the draft (streamed, inside SLO-3)
           -> Deep Agent: write_todos -> task(fact-checker) for each volatile claim (maximum 3, in parallel)
           -> answer_now -> final answer (streamed)
fact-checker: web_search -> fetch_page -> ClaimCheck (structured output). Code applies rule F2 (resolve.py).
```

Streaming rule (ADR-006):

1. A model call with tools does not stream. Each model object uses `disable_streaming="tool_calling"`. This avoids the streaming bug of the Glimmer tool parser (vLLM issue 55395).
2. An answer call has no tools, so it streams. The agent calls the tool `answer_now` when it has enough evidence. Then the answer gate (a middleware) removes all tools from the next model call.
3. The answer gate also removes the tools when the agent uses its step budget or the turn deadline comes near. Quick mode: 3 tool rounds. Verified mode: 3 fact checks, and the final answer starts before 50 s.
4. If the model answers without `answer_now`, the UI shows the answer as one block. The trace records `answer_gate: missed`.

| Step | Work | LLM call shape (estimate, replace with measured values) | Track |
|---|---|---|---|
| S1 plan | The quick agent picks its tool calls (no streaming). In verified mode, the main Deep Agent also reads the draft, writes its plan with `write_todos`, and picks the volatile claims (rule 9, maximum 3). | quick agent: about 3K tokens in, up to 100 out. Deep Agent: about 6K tokens in, up to 300 out. | A (quick), B (Deep Agent) |
| S2 retrieve | The `search_bookmarks` tool: hybrid search and rerank. No LLM call. | none | A |
| S3 draft | The answer from the chunks, with the citations [B1] to [B8]. It streams. | about 10K tokens in (prompt, tools, question, 8 chunks), up to 700 out | A |
| S4 verify loop | The `fact-checker` subagent, once for each volatile claim, in parallel: search, fetch, read, and give a ClaimCheck. The context grows at each step. | starts at about 3K tokens, grows to about 7K over 3 to 4 steps, up to 200 out each step | B |
| S5 final | The main agent writes the final answer with the verdicts that code resolved. It streams. | about 9K tokens in, up to 800 out | A and B |

The ClaimCheck of the `fact-checker` holds the claim, the bookmark citation, and one entry for each page that it read. Each entry has the URL, the verdict, a quote, and the corrected value. The LLM gives only these entries. Code adds the page date, the domain, and the tier from the pages that `fetch_page` read. Pages that `fetch_page` did not read do not count.

A middleware on the main agent applies rule F2 to each ClaimCheck. The main agent then sees only the resolved result.

Tools (all through the Companion API):

| Tool | Job | Guard |
|---|---|---|
| `search_bookmarks` | Hybrid search in Qdrant and rerank in SIE. Returns the top k chunks with the bookmark URL and dates. | Bookmark text was checked at ingest. |
| `web_search` | Tavily basic search (ADR-009). Brave is optional. | Result snippets go through the page check. |
| `fetch_page` | Fetch one URL from the search results or the bookmarks. Extract the main text and the page date (rule 7). If the text has less than 500 characters, use the OCR stage (section 7). Return the windows that match the claim best, up to about 1,500 tokens. | The page check (512-token windows) runs before the text returns. |
| `screenshot_page` | Headless Chromium screenshot of the page, or of the part of the page near a given text (J3). The OCR model changes the screenshot into Markdown. | The page check runs on the OCR text. |
| `answer_now` | Tell the answer gate that the agent has enough evidence. | none |

The OCR step (`ocr_image` in the Companion API) is not a tool for the model. `fetch_page`, `screenshot_page`, and the image upload (J4) use it.

The Deep Agents harness gives the main agent and the subagent more tools. We use these harness tools:

- `write_todos`. It comes from LangChain `TodoListMiddleware`, because Deep Agents 0.7.19 does not add it.
- The file tools `ls` and `read_file`. The harness moves a large tool result into a file, and the agent reads it back with these tools.
- `task`, for the subagent.

A harness profile for our model removes `execute`, `glob`, `grep`, `write_file`, `edit_file`, and `delete`, and it turns off the default `general-purpose` subagent. These changes keep the shared prefix small. Our `FlattenSystem` middleware sends the system prompt as one string, because the middleware adds text blocks to it.

Every LLM call starts with the same shared prefix: the system prompt and the tool schemas. It is about 2K tokens in quick mode and more in verified mode. The trace recorder measures the real size. The prefix text is byte-identical for one prompt version. The app sends the version in `X-Prompt-Version`.

Prompt layout rules (these rules increase prefix-cache hits):

1. Put the shared prefix first. Do not put dates, user names, or request ids in it.
2. Put the session context (the date and the mode) in the user message, before the question.
3. Sort the chunks in the `search_bookmarks` result by chunk id, so two turns with the same chunks share more tokens.
4. Keep the earlier turns of a session in the LangGraph checkpoint. When the context of the quick agent is more than 14,000 tokens, replace the oldest tool results with a placeholder (LangChain `ContextEditingMiddleware`).

Thinking mode: thinking stays off for all steps. We turn on a low reasoning effort for S4 only if the tool-call suite at Gate G1 shows a gain.

## 6. Fact-check rules: when the live result wins

A blanket "the live result always wins" rule is not safe. A low-quality page can then replace a curated source. The rules below define "better" in a way that code can test.

Inputs for each claim:

- Bookmark evidence: the chunk, the bookmark `Created` date, the page date (if the page gives one), and the domain.
- Live evidence: up to 3 fetched pages from the search results, with the page date and the domain.
- Verdict from the LLM (structured output): `SUPPORTED`, `CONTRADICTED`, `OUTDATED`, `NOT_FOUND`, or `UNCLEAR`, with a quote and a source URL.

Rules (code in `app/factcheck/resolve.py`, a unit test for each path). Code decides, not an LLM judge (`DEBATE-LOG.md`, point 5):

1. If the verdict is `SUPPORTED`, keep the claim. Add the live citation. Mark it "verified on DATE".
2. If the verdict is `NOT_FOUND` or `UNCLEAR`, keep the claim. Mark it "not verified".
3. If the verdict is `CONTRADICTED` or `OUTDATED`, two conditions decide. First, the live page must not be older than the bookmark evidence. Second, one of these must be true:
   - The live source tier is the same as the bookmark source tier or better.
   - Two or more independent live domains give the same correction.
4. A tier-1 page with no date counts as current, because live docs often show no date. A page with an older date never wins.
5. If rule 3 does not apply, show both facts with their dates. Mark the claim "disputed".
6. Independent domains: different registered domains, and text that is not a near copy of another result. Copies of one article count once.
7. Dates: the bookmark evidence uses the page date if the page gives one, else the Notion `Created` date. A live page uses the date in its content: JSON-LD `dateModified` or `datePublished`, then the meta tags, then a visible date near the title. We do not use the HTTP `Last-Modified` header. No date gives "unknown", and an unknown date is never newer, except on a tier-1 page (rule 4).
8. Source tiers come from `app/factcheck/tiers.yaml`. A deny list removes known spam domains.
   - Tier 1: primary sources. These are the official docs, repositories, and release notes of the project that the claim names, standards bodies, and original papers.
   - Tier 2: major publications and engineering blogs.
   - Tier 3: forums and aggregators.
9. Verify only volatile claims first: versions, numbers, dates, prices, benchmarks, API names, flags, and the words "latest", "current", or "new".
10. Mixed verdicts across the live pages. The code checks each correction with rule 3 first.
   - A `SUPPORTED` page blocks a correction if the page is not older than the correction and its tier is the same or better. The claim is then "disputed".
   - If two corrections win with the same tier but give different values, the claim is "disputed".
   - If no correction wins and a page is `SUPPORTED`, the claim is "verified".
   - If no correction wins and no page is `SUPPORTED`, the claim is "disputed" when a page contradicts it, else "not verified".
11. Unknown domains are tier 3. A page on the deny list does not count.

Safety rules for the live web:

1. Use a search API (see ADR-009). Do not scrape a search engine results page. Do not solve a CAPTCHA.
2. Fetch only URLs from the search results or from the bookmarks.
3. Obey `robots.txt`. Send one request each second to a domain at most. Use an 8 s timeout and a 2 MB limit.
4. Treat all fetched text as data, not as instructions. The verifier prompt puts the page text between fixed markers and tells the model to ignore instructions inside the markers.
5. Send each fetched page and each OCR text to `guard` in 512-token windows before the text enters a prompt. Remove or mark the windows that fail.
6. Never submit forms. Never log in to a site.

## 7. Screenshots and OCR

| Path | When | How | Priority |
|---|---|---|---|
| OCR stage | Text extraction of a page gives less than 500 characters, the page is an image or a scanned PDF, or the agent calls `screenshot_page` for a figure. The ingest job uses the same stage for a bookmark page with less than 500 characters of text. | Headless Chromium takes a screenshot of the page, or of the element near the given text. The OCR model on SIE (`/v1/extract`) changes it into Markdown. The page check runs on the Markdown. Then the agent reads it. | P0 |
| Vision stage | The question is about a chart, a diagram, or an uploaded image. | The main LLM reads the image. The router can split an image request. **VERIFY** at G1 that multimodal P/D gives correct output. If not, the vision stage uses the OCR path only. | P1 |

## 8. Batch jobs

| Job | Shape | Class | Priority |
|---|---|---|---|
| Freshness sweep | For N bookmarks: extract volatile claims, run the S4 loop, and write a report. Do not write back to Notion. | batch | P1 (P0 for a 20-bookmark run that makes batch traffic) |
| Index sync | Notion sync, fetch, chunk, and embed. Uses the data plane, not the LLM. | batch | P0 |

## 9. Request contract to `edge`

The app sends these headers on every LLM call. `edge` maps them to the router headers (`04-system-design.md`, section 6.2):

| Header | Values | Use |
|---|---|---|
| `X-Request-Id` | UUID | Trace and abort. |
| `X-Tenant-Id` | `owner`, `sweep`, `load-*`, `noisy` | Tenant window (Agent Router) and fairness (router). |
| `X-Session-Id` | UUID for each chat session or agent run | Session affinity on the decode pool. |
| `X-Request-Class` | `interactive`, `batch` | Priority band, vLLM priority, overflow policy. |
| `X-Deadline-Ms` | integer | Overflow gate and the queue TTL check. |
| `X-Step` | `quick`, `agent` (the main Deep Agent), `verify` (the `fact-checker` subagent), `vision`, `sweep` | Metrics and traces. Each model object sends its own value. |
| `X-Prompt-Version` | string | Shared-prefix version. |
| `X-Allow-Overflow` | `true`, `false` | Privacy switch. If false, the request never leaves our cluster. |

## 10. Service levels (first targets)

These targets are first values. Gate G1 measures the warm baseline. Then we change the targets in this table and record the reason.

| ID | Target |
|---|---|
| SLO-1 | LLM call, interactive, warm pod, prompt up to 8K tokens: TTFT p95 up to 1.5 s. Prompt up to 32K tokens: TTFT p95 up to 3 s. |
| SLO-2 | LLM call, interactive: inter-token latency p95 up to 50 ms. |
| SLO-3 | User turn in quick mode: first answer token p95 up to 3 s. A quick turn that reads a figure through OCR uses the SLO-4 limit, because the OCR step alone takes 5 to 10 s. Changed on 2026-09-30 (the owner, demo question D-08). |
| SLO-4 | User turn in verified mode: complete answer p95 up to 60 s. The draft shows inside SLO-3. Changed from 30 s on 2026-09-29 (the owner): at 30 s, about 70% of the claim checks in the demo timed out. |
| SLO-5 | Batch work does not push the interactive TTFT p95 above SLO-1. |
| SLO-6 | At 1x load, 99% of interactive requests get a 200 (local or overflow). All sheds use the correct code and reason. |
| SLO-7 | Guard stage 2 on a new user turn (text): p95 up to 300 ms. A cache hit: p95 up to 10 ms. |

## 11. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | Keep all secrets in Kubernetes Secrets or in a local `.env` file. Never commit a secret. |
| NFR-02 | Bind the app, admission control + routing, and the engine to localhost or to the cluster network. Use the SSH tunnel for access. |
| NFR-03 | Keep the bookmark text on our cluster. Only requests with `X-Allow-Overflow: true` can go to the overflow provider. |
| NFR-04 | Emit Prometheus metrics from every service. Emit OpenTelemetry spans with a plane label (data, control, GPU) (P1). |
| NFR-05 | Pin every version: images by digest, Helm charts by version, Python packages by lock file. |
| NFR-06 | Write all text in ASD-STE100 (see `docs/style/ste-guide.md`). |

## 12. Scope

Point 12 of the debate puts P0 and P1 in scope for 10-03. We build P0 first, then P1. P2 is out. The offline eval job is out: the demo questions are the acceptance test (J6).

| In P0 | In P1 | In P2 |
|---|---|---|
| J1, J2, J3, J6, FR-01, FR-02, FR-05 to FR-11, FR-13, FR-14, all fact-check rules, OCR stage, freshness sweep (20 bookmarks) | J4, J5, FR-03, FR-04, FR-12, vision stage, full freshness sweep | YouTube transcripts, PDF files in Notion, write-back to Notion (only with approval) |
