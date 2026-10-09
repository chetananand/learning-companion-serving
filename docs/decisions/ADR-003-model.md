# ADR-003: Model

Status: accepted (2026-09-27). Gate G1 can switch to the challenger. The debate is in `DEBATE-LOG.md`, point 1. Owners: the project owner and Claude.

## Context

The model must do tool calls and RAG well. It must read browser screenshots and fit our GPU budget. It must also work with our serving path: prefix caching, NIXL P/D, LMCache, KV events, and priority scheduling. The owner suggested Gemma 4 31B, Nemotron 3 Ultra, and Qwen3.8-27B.

## Candidates

| Model | For | Against |
|---|---|---|
| **Gemma 4 31B-it, FP8-dynamic** | Tested FP8 checkpoint (RedHatAI). LMCache setup for 31B. vLLM parser on the new parser engine. Dev model of the same family (E4B). | Two head sizes, the hardest KV layout for NIXL and LMCache. Heavy KV (1.09 GiB for an 8K prompt). Weaker agent scores. |
| **Muse Glimmer 30B (Meta, 2026-08-10)** | Much higher agent scores (MCP Atlas 75.5 against 54.2). One head size on all layers. Small KV (0.18 GiB for an 8K prompt). | No FP8 checkpoint (online FP8 or BF16). Seven weeks old. Older vLLM parser with open bugs. No LMCache test. No small model of the same family. |
| Qwen3.8-27B | Strong GUI scores. | Open vLLM bugs: NaN after a prefix-cache hit (55766), prefix cache fails for image requests (43587), deadlock with FP8 and prefix caching (37729). Dropped. |
| Nemotron 3.5 Lightning 30B-A3B | Fast. | Text only. Dropped. |
| Gemma 4 26B-A4B | Fast prefill. | Lower quality, and the same two head sizes. Dropped. |
| Nemotron 3 Ultra | Large. | Needs about 8 H100. Dropped in the first spec. |

## Decision (M1)

1. Main model: **`RedHatAI/gemma-4-31B-it-FP8-dynamic`**. Not the FP8-block checkpoint. A stale bot closed issue 39407 without a fix.
2. Challenger at Gate G1: **`meta-models/Muse-Glimmer-30B`** with online FP8.
3. Dev model: `google/gemma-4-E4B-it`.

## Switch rule at Gate G1

1. G1 tests tool calls, screenshots, prefix-cache correctness, P/D startup with LMCache, and TTFT.
2. If Gemma 4 fails a test that Glimmer passes, Glimmer becomes the main model.
3. If both pass all tests, Glimmer wins only with a tool-call pass rate that is at least 10 points higher.

## Protections for Gemma 4

1. The same `--block-size` on the prefill and decode pods (issue 52234).
2. The `gemma4` tool parser and reasoning parser, always together (issues 57231 and 57232).
3. No MTP (issue 54926).
4. A progress check in the `warm-controller` (issue 53130).

## If Glimmer wins

- The engine flags change to `--quantization fp8 --tool-call-parser muse_glimmer --reasoning-parser muse_glimmer`.
- The capacity plan already has its numbers (H-40).
- The agent sends tool steps without streaming, because the Glimmer parser has a streaming bug (55395).
- The dev days test the app with Gemma 4 E4B, and G1 tests the tool calls again with Glimmer.

## Checkpoint source (checked on 2026-09-27)

Google publishes no FP8 checkpoint of Gemma 4 31B. Google publishes BF16 and 4-bit QAT checkpoints, which include `google/gemma-4-31B-it-qat-w4a16-ct` for vLLM. We use `RedHatAI/gemma-4-31B-it-FP8-dynamic`, because FP8 halves the weight memory and uses the FP8 tensor cores of the H100 for the prefill. Its card shows a recovery of 98.9% to 101.0% against Google BF16 on 8 benchmarks. The card gives no number for function calls, so the G1 tool-call suite checks it.

Fallback: if the FP8 checkpoint fails at G1, the official QAT checkpoint `google/gemma-4-31B-it-qat-w4a16-ct` is the next choice. It has more room for KV, but its prefill uses BF16 math.
