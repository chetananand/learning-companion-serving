# ADR-008: Overflow target (who receives a 503 or a 529)

Status: accepted (updated 2026-09-27 for `edge`). **VERIFY** the hosted catalog with our key (action A3). Owners: the project owner and Claude.

## Context

The handout says: name who receives a 503 or a 529, not "another API", and give the model and the reason (L610). A bad answer names Superlinked or another API without a model and a limiter (L814). A 429 must never overflow (L820). In our design, `edge` makes the stay-or-leave decision, because the Agent Router fallback cannot act on a local reply of the llm-d router.

## Decision

| Item | Value |
|---|---|
| Provider | Superlinked hosted API (`https://api.superlinked.com/v1`), paid by the 500 USD credit |
| Model, first choice | `qwen3.8-27b`, if the hosted catalog has it |
| Model, second choice | `Qwen/Qwen3.5-4B` (the class10 default) |
| Who can leave | Interactive requests with `X-Allow-Overflow: true`, no image, prompt of 32K tokens or less, and enough deadline left. The local result is a router capacity reject (429 with a capacity reason), a 503, or a 529. |
| Who never leaves | Batch requests, tenant 429 results, 500 results, `slice_oom` results, `guard_unavailable` results, guard rejects, and requests with `X-Allow-Overflow: false` |
| Limiter (Redis, shared by the two `edge` replicas) | 20 requests each minute, 60,000 tokens each minute, 4 in flight, and 15 USD each day |
| Label | Each overflow response has the header `x-companion-via: overflow`, the model name, and the local reason. The UI marks the answer as "overflow". |

## Reasons for the model

1. The API is OpenAI-compatible, and tool calls use the OpenAI schema. The agent code does not change.
2. A model of a similar size (`qwen3.8-27b`) keeps the answer quality near our local quality. The small model is the fallback. It is fast and cheap, but its quality is lower, so the UI shows the label.
3. The credit pays for it. The limiter puts a hard bound on the cost.

## Privacy

The prompt can contain text from the owner's bookmarks. The switch `X-Allow-Overflow` lets the app keep a request on our cluster. The default for the UI is `true`. The default for the sweep job is `false`.
