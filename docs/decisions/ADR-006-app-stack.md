# ADR-006: App stack (UI, agent, sessions)

Status: accepted (2026-09-26, updated 2026-09-27 for `edge`, the guard, Deep Agents, and the answer gate). Owners: the project owner and Claude.

## Decision

| Part | Choice | Reason | Rejected |
|---|---|---|---|
| UI | Streamlit | The owner suggested it. Chat elements, `st.status` for agent steps, image display, and streaming are built in. | Chainlit (community-maintained since 2025-05-01), Open WebUI (not able to show our agent steps and verdicts well), a custom React app (too much work for one week). |
| Agent service | FastAPI "Companion API" | The UI stays thin. The load generator calls the same API. | Agent code inside Streamlit (hard to test and to load). |
| Agent framework | LangChain 1.4 and LangGraph 1.2. Verified mode: Deep Agents 0.7 (`create_deep_agent`) with a `fact-checker` subagent. Quick mode: `create_agent`. | The owner asked for Deep Agents (2026-09-27). It is a production agent harness: subagents (`task`), a virtual file system for page notes and large tool results, context summarization, and LangGraph checkpoints. `response_format` gives typed outputs. | PydanticAI v2 (the first choice), CrewAI (weaker typed outputs). |
| Model access | `ChatOpenAI` (`langchain-openai`) with `edge` as the base URL, one object for each role | Each role sends its own `X-Step`. An httpx event hook adds the headers of each turn. | A direct vLLM URL (it skips admission control and routing). |
| Streaming | Tool steps do not stream (`disable_streaming="tool_calling"`). Answer steps have no tools and stream. The tool `answer_now` and an answer-gate middleware start the answer step. | Glimmer has a streaming bug in its tool parser (vLLM issue 55395). A model call with tools cannot stream its answer safely, and a non-streamed answer misses SLO-3. | Streaming of all steps (parser bug). No streaming (SLO-3 fails, and a one-block answer is the class10 flaw). |
| Sessions | LangGraph checkpointer: in memory in P0, SQLite on the app volume in P1 (in the teardown backup). `ContextEditingMiddleware` replaces old tool results when the quick agent context is more than 14,000 tokens. | One user. The cluster runs on demand, so the state must survive a teardown. | PostgreSQL (one more database to back up). |
| Browser | Playwright with headless Chromium, in a separate pod | Screenshots and pages that need JavaScript. | A search-engine scraper (see ADR-009). |
| Page guard | The app sends each fetched page and each OCR text to `guard-injection` in 512-token windows | Indirect prompt injection is the main threat for a RAG agent. | Checks only in the prompt text. |

## Deep Agents settings (checked in the 0.7.19 source)

1. `create_deep_agent` does not add `write_todos` in 0.7.19. We add LangChain `TodoListMiddleware` to the main agent.
2. The harness adds `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`, and `task`. It also adds a `general-purpose` subagent. We register a `HarnessProfile` for the key `openai:companion`. It removes `execute`, `glob`, `grep`, `write_file`, `edit_file`, and `delete`, and it turns off the `general-purpose` subagent. The agents keep `ls` and `read_file` for large tool results.
3. Each `ChatOpenAI` object has a model profile with `max_input_tokens` = 30,000 (the `edge` limit). The summarization middleware then starts at 85% of this limit. Without a profile, it starts at 170,000 tokens, and `edge` rejects the request first.
4. The `fact-checker` subagent uses `response_format=ToolStrategy(ClaimCheck)`. The `task` tool returns the ClaimCheck as JSON. A middleware on the main agent (`awrap_tool_call`) applies rule F2 to it before the main agent sees it.
5. `ChatOpenAI` sends `max_completion_tokens`. `edge` changes it into a clamped `max_tokens` (guard stage 1).
6. `TodoListMiddleware` and the harness add text blocks to the system prompt. Our `FlattenSystem` middleware joins them into one string before each model call. We use short texts for `write_todos` (in `app/prompts.py`), because the default tool text has about 1,100 tokens.

## Consequences

- The app talks to the LLM only through `edge`. The app has no router or vLLM URL in its config (H-34).
- The agent sets the headers of the request contract (product spec, section 9).
- A LangChain callback handler writes the trace of each LLM call (FR-14).
- The page guard is a part of `fetch_page`, `screenshot_page`, and the OCR step, so no page text enters a prompt without the check. If `guard-injection` is not available, the tool returns no page text (fail closed).
- The Companion API streams the answer tokens from the LangGraph `messages` stream mode to the UI as server-sent events.
