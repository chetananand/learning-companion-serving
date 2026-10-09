# ADR-007: Data plane (vector database, embeddings, rerank, OCR)

Status: accepted for the structure (updated 2026-09-27 for node 2 and the SIE images). Gate G2 picks the OCR model and the image token budget. Owners: the project owner and Claude.

## Decision

| Part | Choice | Reason |
|---|---|---|
| Vector database | Qdrant v1.19.1 (Helm chart, one replica, persistent volume on node 2) | Hybrid search: dense vectors and server-side BM25 with RRF fusion. `qdrant-client` sends a `Document` with the model `Qdrant/bm25` to the server as text when the server is 1.15.3 or later. Payload filters for dates and domains. |
| Small-model server | Superlinked SIE on HAMi slices of node 2 GPU 0, as two servers: `sie-embed` (the `default` image: `bge-m3` and `qwen3-reranker`) and `sie-ocr` (the `sglang-vision-extract` image: the OCR model) | SIE images are bundle-specific. The OCR models run only on the vision image. Helm chart and Grafana dashboards exist. |
| SIE API | The native API: `/v1/encode/{model}`, `/v1/score/{model}`, and `/v1/extract/{model}`, with MessagePack bodies | The rerank and OCR tasks are not in the OpenAI API. Our client is small (httpx and `msgpack`). We do not use `sie-sdk` 0.8.3: it needs `websockets` below 15, and Deep Agents (through `langsmith`) needs 15 or later. |
| Embeddings | `bge-m3` (dense, 1024 dimensions, 8K input) | Strong retrieval, many languages. |
| Rerank | `qwen3-reranker` (`Qwen/Qwen3-Reranker-4B`) | Top 30 to top 8. |
| OCR | One of `paddleocr-vl`, `glm-ocr`, `lightonocr` | Gate G2 picks the best model on 10 real screenshots. The result is Markdown in `entities[0].text`. |
| Image token budget (Gemma 4) | 280, 560, or 1,120 | Gate G2 picks it from the screenshot result and the TTFT. |
| Text extraction | `trafilatura`, then Playwright and OCR when the text is too short | Fast for normal pages. The browser handles pages that need JavaScript. |
| Chunks | 500 to 800 tokens, split at headings, 64 tokens of overlap | Fits k = 8 chunks in an 8K prompt. |

Fallback: if SIE fails on a HAMi slice, serve `bge-m3` and the reranker with vLLM pooling models, and serve the OCR model with vLLM.

## Consequences

- The data plane does not go through admission control and routing. The Companion API applies its own limits to SIE calls.
- The ingest job is a batch workload for the data plane. Its embedding calls use node 2 GPU 0, not the H100 pods.
- The Qdrant data goes into the teardown backup (Lambda filesystem and laptop copy).
- GPU 0 of node 2 holds three slices: `sie-embed`, `sie-ocr`, and on dev days the dev model. Gate G0 measures the memory of each slice.
