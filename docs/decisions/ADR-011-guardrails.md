# ADR-011: Guardrails

Status: accepted (2026-09-27). The debate is in `DEBATE-LOG.md`, section "Guardrails". Owners: the project owner and Claude.

## Context

The handout puts the guardrails first on the request path: the "first no", and a rejected request never reaches a GPU (L536). The interface is `inspect(payload) -> Guard` (Part 3). The owner made a prompt-injection classifier a requirement. The owner also asked us to check NVIDIA NeMo Guardrails, and chose to run the guard models on a separate, lower GPU.

## Decision

Two stages.

| Stage | Where | What | Result on failure |
|---|---|---|---|
| 1 | `edge`, CPU | The rules: model allow list, tenant and class headers, `max_tokens`, empty prompt, prompt tokens, images, tool schemas | 4xx. The request reaches no GPU. |
| 2 | Two services on node 2 GPU 1, called by `edge` in parallel | `guard-injection`: Llama Prompt Guard 2 86M (injection and jailbreak), our small classifier service. `guard`: NeMo Guardrails 0.24.1 server (`/v1/checks`, IORails engine) with the built-in content-safety rail on Nemotron 3.5 Content Safety 4B (vLLM, fast mode). | 400 `prompt_injection` or 400 `unsafe_content`. The request never reaches a serving GPU. |

Rules:

1. `edge` checks only the new user content of a turn. Redis keeps each verdict by content hash for 1 hour.
2. The app sends each fetched page and each OCR text to `guard` in 512-token windows.
3. The guard fails closed: a timeout (1 s) or an error gives 503 `guard_unavailable`. That request never leaves to the overflow.
4. Target: SLO-7 (stage 2 p95 up to 300 ms for a new user turn).

## Why Prompt Guard 2 is not a NeMo custom action

NeMo has two engines. IORails is the fast one: it runs rails in parallel, has admission control, and calls the rail action directly. It does not run custom actions. A custom action moves the whole configuration to LLMRails, the full Colang runtime. Thus NeMo runs only the built-in content-safety rail on IORails, and `edge` calls `guard-injection` in parallel. Both calls fail closed.

## What we do not use, and why

| NeMo part | Why not |
|---|---|
| Jailbreak heuristics and JailbreakDetect | The docs say that the rail lets the request through when the detector is not available. Prompt Guard 2 does the same job, and we control how it fails. |
| Self-check rails | They call the serving model, so they use the H100 GPUs. |
| NemoGuard 8B Topic Control | Too large for the job. The 4B safety model accepts custom rules if we need topic rules. |

## Consequences

- Node 2 is 2 x A6000. GPU 1 holds the guard models (ADR-004, ADR-005).
- The owner accepted the Llama 4 Community License for Prompt Guard 2 on Hugging Face, and Meta approved it (A8, done 2026-09-27).
- The classifier is a partial defense. Experiment E17 measures the latency, the false-positive rate, and the missed injections, and it tunes the threshold.

## VERIFY at Gate G0

1. `/v1/checks` runs the content-safety rail on IORails (`Guardrails(config, require_iorails=True)`).
2. NeMo sends the image to Nemotron 3.5 through the content-safety rail.
3. Nemotron 3.5 runs on our vLLM image. If not, `guard-safety` pins vLLM 0.20.2.
4. `guard-injection` loads Prompt Guard 2 (or the fallback model until Meta approves action A8).
