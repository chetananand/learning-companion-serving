# Research findings (2026-09-26)

This file records the facts that the spec uses. Each fact has a source. If a fact changes, update this file first. Items marked **VERIFY** need a test on the GPU on Day 1.

## 1. Course sources that we read

| Source | What we took from it |
|---|---|
| Handout "Final Project - Design the cluster and Serve an app" (Class 11, due 2026-10-03) | All asks. See `01-handout-checklist.md`. |
| Class 10 revision notes (course material, not in this repository) | The lab is a teaching stack. Do not split P/D on one sliced GPU. Ghost cache. Warmup is a budget. Ten case studies (plane of symptom against plane of cause). |
| Class 10 quiz and short answers | Same themes: one front door, hop only when ids differ, completion is not visibility. |
| Class 7 and 8 "Error Codes" page | 429 means "slow down" (tenant). 503 `server_is_overloaded` and 529 `overloaded_error` mean "the fleet is full". Treat 529 like 503. |
| Previous cohort project rubric | Graders reward pinned versions, real engine-flag experiments, and model and GPU choices that use measured numbers. |
| `goabiaryan/class-code` at commit `bf42b32` (pulled 2026-09-26) | See section 2. |
| Course page data in the saved handout HTML | Presentation and wrap-up: 2026-10-03, 15:30 to 18:00 UTC. Superlinked project mentoring: 2026-09-27, 15:30 to 16:30 UTC (not optional). |

## 2. Class code: what is real and what is a teaching stub

| Class | Content | Use for us |
|---|---|---|
| class1 | Prefill and decode timing on CPU, TPU, and T4. | Background only. |
| class2 | Naive server, llama.cpp, RelayServe, and LiteLLM on Modal. | Modal app patterns. |
| class5 | `smol-vllm`: block manager, scheduler, preemption, engine loop. | Engine vocabulary only. We do not write an engine. |
| class7 | Async FastAPI gateway: token buckets, deadline-aware admission, priority queue with aging, prefix trie with 16-token block hashes, p2c, streaming with disconnect detection. | Base pattern for our async gateway. |
| class7/hw | Admission rules (tenant tokens before requests, queue wait > timeout/2, KV < 8% for a new prefix, p99 > 4 x p50 sheds batch), "unknown is not idle", "do not bounce", recovery ramp 10% to 100% in 30 s, traces `mixed`, `t1_unique`, `t2_shared`, `t3_stale`. | Contract tests and traffic mixes. |
| class9, class9b | Gateway, router, FakeWorker, k3s, HAMi, KEDA, Grafana dashboards, Open WebUI. | Earlier forms of class10. |
| class10 | Class 9b stack plus kernel cost model, KV eviction policies, warmup model, NCCL and NIXL stubs. | Starter pack. See the table below. |

Findings in class10 that change our design:

| Finding | Evidence | Our response |
|---|---|---|
| The live `VLLMWorker.enqueue` ignores the phase. A "hop" sends the full prompt to two different models. | `class10/router/pools.py`, `class10/gateway/queue.py` | We use a real P/D hop with the vLLM NixlConnector. |
| `router/mooncake.py` is a small HTTP store. `router/nccl.py` and `router/nixl.py` are empty. | Files, revision notes | We do not claim that our repository moves KV. The vLLM connector moves it. |
| The gateway is a synchronous `ThreadingHTTPServer`. SSE is fake: it sends the full answer as one chunk. | `class10/gateway/serve.py` | We use an async gateway with real streaming and cancellation. |
| `gateway/queue.py` has no queue. | File | We build per-pod bounded priority queues. |
| The Kubernetes manifest pins `vllm/vllm-openai:v0.11.0`, but the setup script installs `vllm>=0.28`. | `hami-lambda.yaml`, `lambda_setup.sh` | We pin one version everywhere. |
| Ten gateway behaviors B1 to B10 and six app-edge behaviors exist as tests. | `class10/gateway/harness.py`, `class10/app/harness.py` | We port them as contract tests. |

## 3. Engine: vLLM

| Fact | Source |
|---|---|
| Latest releases: v0.30.0 (2026-09-22), v0.29.0 (2026-09-09), v0.28.0 (2026-08-26). v0.30.0 adds encoder-cache sharing over NIXL and Mooncake. v0.30.0 needs `--enable-scale-out` for scale-out endpoints. | [vLLM releases](https://github.com/vllm-project/vllm/releases) |
| NixlConnector supports P/D for dense, MLA, MoE, sliding window (SWA), and hybrid SSM models. Multimodal support is "not known" (❔). Encoder-decoder is not supported. | [NixlConnector compatibility](https://docs.vllm.ai/en/latest/features/nixl_connector_compatibility/) |
| Chunked prefill, prefix caching (APC), data parallel, and CUDA graphs work with all NixlConnector P/D models. | Same |
| P and D must match in vLLM version, NIXL version, model, dtype, attention backend, and KV cache dtype. Hybrid SSM models need the same TP on P and D. | Same |
| `kv_both` is deprecated. Roles are `kv_producer` and `kv_consumer`. `kv_load_failure_policy` is `fail` or `recompute`. The prefiller keeps blocks for `kv_lease_duration` (example 30 s). `decoder_kv_blocks_ttl` example is 480. | [NixlConnector usage](https://docs.vllm.ai/en/stable/features/nixl_connector_usage/) |
| Optional bidirectional mode: in turn 2 and later, P reads the existing KV from D and computes only the new tokens. The proxy must keep `kv_transfer_params` for each `conversation_id`. | Same |
| NIXL Prometheus metrics: `vllm:nixl_xfer_time_seconds`, `vllm:nixl_post_time_seconds`, `vllm:nixl_bytes_transferred`, `vllm:nixl_num_descriptors`, `vllm:nixl_num_failed_transfers`, `vllm:nixl_num_failed_notifications`, `vllm:nixl_num_kv_expired_reqs`. | Same |
| Transport env: `UCX_TLS`, `UCX_NET_DEVICES`, `VLLM_NIXL_SIDE_CHANNEL_HOST`, `VLLM_NIXL_SIDE_CHANNEL_PORT` (default 5600, unique for each worker). | Same |
| Reference proxy flow: send the request to P with `max_tokens: 1`, `stream: false`, and `kv_transfer_params: {do_remote_decode: true, do_remote_prefill: false, remote_engine_id: null, remote_block_ids: null, remote_host: null, remote_port: null}`. Copy the returned `kv_transfer_params` into the request to D. Stream from D. Send `X-Request-Id`. | [toy_proxy_server.py](https://github.com/vllm-project/vllm/blob/main/tests/v1/kv_connector/nixl_integration/toy_proxy_server.py) |
| MooncakeConnector: package `mooncake-transfer-engine` (CUDA 12) or `mooncake-transfer-engine-cuda13`. Default protocol `rdma`. Env `VLLM_MOONCAKE_BOOTSTRAP_PORT` (8998), `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT` (480 s). | [MooncakeConnector usage](https://docs.vllm.ai/en/latest/features/mooncake_connector_usage/) |
| MooncakeStoreConnector: a distributed KV pool in CPU DRAM and SSD. Needs `mooncake_master` (default port 50051) and a JSON config (`MOONCAKE_CONFIG_PATH`). Protocol `rdma` or `tcp` ("tcp works as a fallback"). MultiConnector can join MooncakeConnector (P2P hop) and MooncakeStoreConnector (shared pool). | [MooncakeStoreConnector usage](https://docs.vllm.ai/en/stable/features/mooncake_store_connector_usage/) |
| vLLM with Mooncake Store for agent workloads: 1P1D on 12 GB200 GPUs gave 3.8 times throughput, 46 times lower P50 TTFT, and cache hit rate from 1.7% to 92.2%. | [vLLM blog 2026-05-06](https://vllm.ai/blog/2026-05-06-mooncake-store) |
| Encoder disaggregation (EPD) with ECConnector exists since v0.11.1. | [vLLM blog EPD](https://vllm.ai/blog/2025-12-15-vllm-epd), [docs](https://docs.vllm.ai/en/latest/features/disagg_encoder/) |
| KV events: `--kv-events-config` with `enable_kv_cache_events`, `publisher` (`zmq`), `endpoint` (default `tcp://*:5557`), `replay_endpoint`, `buffer_steps` (10000), `hwm` (100000), `topic`. Events: BlockStored, BlockRemoved, AllBlocksCleared. With the hybrid memory allocator (HMA), you must turn events on explicitly. | [KVEventsConfig](https://docs.vllm.ai/en/stable/api/vllm/config/kv_events/), [PR 39269](https://github.com/vllm-project/vllm/pull/39269) |
| V1 metrics: `vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:kv_cache_usage_perc`, `vllm:prefix_cache_queries`, `vllm:prefix_cache_hits`, `vllm:num_preemptions_total`, `vllm:request_queue_time_seconds`, `vllm:request_prefill_time_seconds`, `vllm:request_decode_time_seconds`, `vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`, `vllm:e2e_request_latency_seconds`, `vllm:request_success_total{finished_reason=stop,length,abort}`, `vllm:kv_block_lifetime_seconds`, `vllm:kv_block_idle_before_evict_seconds`, `vllm:kv_block_reuse_gap_seconds`. V1 has no swap metrics. | [vLLM metrics design](https://docs.vllm.ai/en/stable/design/metrics/) |

## 4. Other engines and control planes

| Fact | Source |
|---|---|
| SGLang: P/D through the Mooncake Transfer Engine. HiCache adds GPU, CPU, and Mooncake Store tiers to RadixAttention. EPD with Mooncake exists. | [Mooncake x SGLang](https://kvcache-ai.github.io/Mooncake/getting_started/examples/sglang-integration/index.html), [LMSYS EPD](https://www.lmsys.org/blog/2026-01-12-epd/) |
| NVIDIA Dynamo is at v1.x (v1.0.0 to v1.4.0). It has its own KV-aware router, a standalone router service, a Planner for autoscaling, and KVBM for KV tiers. | [Dynamo releases](https://docs.nvidia.com/dynamo/reference/releases/v1-4-0), [router](https://docs.nvidia.com/dynamo/components/router) |
| llm-d entered the CNCF Sandbox on 2026-03-24. It has a precise prefix-cache scorer fed by KV events, and P/D with NixlConnector and a routing sidecar. | [llm-d P/D](https://llm-d.ai/docs/dev/well-lit-paths/foundations/pd-disaggregation), [llm-d KV blog](https://llm-d.ai/blog/kvcache-wins-you-can-see) |
| LMCache is in production since January 2026 (GKE Inference, CoreWeave, Cohere). Backends: CPU, disk, Redis or Valkey, Mooncake, S3, NIXL, GDS. It has recipes for the Qwen3.5, 3.6, and 3.8 hybrid models. | [LMCache](https://github.com/lmcache/lmcache), [LMCache Qwen3.5 recipe](https://docs.lmcache.ai/recipes/qwen3_5.html) |
| Mooncake Transfer Engine has TCP, RDMA, EFA, NVMe-oF, NVLink, and intra-node NVLink transports (`MC_INTRA_NVLINK`). | [Mooncake Transfer Engine](https://kvcache-ai.github.io/Mooncake/design/transfer-engine/index.html) |

## 5. GPU sharing and autoscaling

| Fact | Source |
|---|---|
| HAMi v2.10.0 released 2026-08-21 (flexible MIG, composable scheduling). HAMi v2.9.0 or later is necessary for vLLM newer than 0.18 with multi-GPU TP. v2.10.0 fixed a high-cardinality leak in the memory metrics. | [HAMi v2.10.0](https://project-hami.io/blog/hami-v2-10-0-release), [HAMi FAQ](https://project-hami.io/docs/v2.9.0/faq) |
| HAMi-core hooks the CUDA driver and NVML calls. The container sees the slice memory (`CUDA_DEVICE_MEMORY_LIMIT_0`) and the core limit (`CUDA_DEVICE_SM_LIMIT`). | [HAMi vLLM lab](https://project-hami.io/tutorials/labs/hami-vllm), [HAMi-core](https://github.com/Project-HAMi/HAMi-core) |
| **VERIFY**: vLLM `--gpu-memory-utilization` applies to the slice memory that the container sees. | Day 1 test |
| KEDA 2.19 is current. `advanced.horizontalPodAutoscalerConfig.behavior` sets `stabilizationWindowSeconds` and scale policies. | [KEDA Prometheus scaler](https://keda.sh/docs/2.19/scalers/prometheus/) |

## 6. Models

| Fact | Source |
|---|---|
| Gemma 4 released 2026-04-02. Apache 2.0. Text and image input. The 31B dense model has 30.7B parameters, 60 layers, and a 256K context. | [Gemma 4 31B card](https://huggingface.co/google/gemma-4-31B) |
| Gemma 4 31B config: 50 sliding-window layers (16 KV heads, head dim 256, window 1024) and 10 global layers (4 KV heads, head dim 512). Global layers use K = V, so they keep one tensor. Vision gives 280 soft tokens for each image. | [config.json](https://huggingface.co/google/gemma-4-31B-it/raw/main/config.json), [Gemma 4 report](https://arxiv.org/html/2607.02770v1) |
| Gemma 4 26B-A4B: 30 layers (25 sliding, 5 global), 8 and 2 KV heads, 128 experts with top-8 routing. | [config.json](https://huggingface.co/google/gemma-4-26B-A4B-it/raw/main/config.json) |
| vLLM serves Gemma 4 with `--tool-call-parser gemma4 --enable-auto-tool-choice` and `--reasoning-parser gemma4`. Image budgets are 70, 140, 280, 560, or 1120 tokens. 31B BF16 needs one 80 GB GPU or TP=2. | [vLLM Gemma 4 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html) |
| FP8 checkpoints: `RedHatAI/gemma-4-31B-it-FP8-Dynamic` and `...-FP8-block`. The FP8-block checkpoint gives garbage output in vLLM (issue 39407). | [RedHatAI FP8-Dynamic](https://huggingface.co/RedHatAI/gemma-4-31B-it-FP8-Dynamic), [issue 39407](https://github.com/vllm-project/vllm/issues/39407) |
| Qwen3.8 family: 2.4T-A95B (2026-08-12), 27B dense (2026-08-14), Flash-Next 125B-A6B (2026-08-26). | [MarkTechPost](https://www.marktechpost.com/2026/08/26/alibabas-qwen-team-releases-qwen3-8-flash-next-a-125b-multimodal-moe-with-6b-active-parameters-previewing-the-qwen4-architecture/), [codersera lineup](https://codersera.com/blog/qwen-3-8-model-lineup-2026/) |
| Qwen3.8-27B: dense, Apache 2.0, text, image, and video. 64 layers: 48 Gated DeltaNet layers and 16 gated-attention layers (24 Q heads, 4 KV heads, head dim 256). Context 262,144. Thinking is on by default. OSWorld 84.3%, WebArena 64.8%. | [Qwen3.8-27B card](https://huggingface.co/Qwen/Qwen3.8-27B), [config.json](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json) |
| Qwen3.5 and later (qwen3_5 architecture) in vLLM: `--tool-call-parser qwen3_coder`, `--reasoning-parser qwen3`. Prefix caching in Mamba "align" mode is experimental. | [vLLM Qwen3.5 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Qwen/Qwen3.5.html) |
| Open bug: NaN logits after a prefix-cache hit on Qwen3.5 and Qwen3.8 hybrid models (v0.28.0, align mode). Workarounds: `cache_salt`, prepend padding, or no prefix caching. | [issue 55766](https://github.com/vllm-project/vllm/issues/55766) |
| Prefix caching fails for incremental multimodal requests on Qwen3.5 hybrid models. | [issue 43587](https://github.com/vllm-project/vllm/issues/43587) |
| Qwen3.6-35B-A3B (2026-04-16): MoE, 3B active, 40 layers (30 linear attention, 10 full attention with 2 KV heads), natively multimodal. | [Qwen3.6 card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B), [config.json](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/raw/main/config.json) |
| Nemotron 3 Ultra (2026-06-04): 550B total, 55B active, hybrid Mamba-Transformer MoE, pretrained in NVFP4. | [MarkTechPost](https://www.marktechpost.com/2026/06/04/nvidia-ai-releases-nemotron-3-ultra-an-open-550b-mixture-of-experts-hybrid-mamba-transformer-for-long-running-agents/), [NVIDIA research](https://research.nvidia.com/labs/nemotron/Nemotron-3-Ultra/) |

## 7. GPU clouds and credits

| Fact | Source |
|---|---|
| Lambda on-demand shapes: B200 (1x, 2x, 4x, 8x, 180 GB), GH200 (1x, 96 GB), H100 SXM (1x, 2x, 4x, 8x, 80 GB), H100 PCIe (1x), A100 SXM 80 GB (8x), A100 PCIe 40 GB (1x, 2x, 4x), A10 (1x), A6000 (1x, 2x, 4x, 48 GB), RTX 6000 (1x). Only port 22 is open by default. | [Lambda docs](https://docs.lambda.ai/public-cloud/on-demand/) |
| Lambda prices for each GPU-hour (pricing page, checked 2026-09-27): H100 SXM 4.29 (1 GPU), 4.19 (2 GPUs), 4.09 (4 GPUs), 3.99 (8 GPUs). H100 PCIe 3.29. A100 SXM 80 GB 2.79. B200 6.69 to 6.99. GH200 2.29. A10 24 GB 1.29. A6000 48 GB 1.09 at 1, 2, or 4 GPUs (the old value 0.80 is out of date). 1 x A6000 has 14 vCPUs and 100 GiB RAM. 2 x A6000 has 28 vCPUs and 200 GiB RAM. Stock is limited. | [Lambda pricing](https://lambda.ai/pricing) |
| Lambda persistent filesystem: 0.20 USD for each GB each month, billed by the hour. No charge for data in or out. GH200 (96 GB, 2.29 USD for each hour) comes only as 1 GPU for each instance, with an ARM CPU. | [Lambda filesystems](https://docs.lambda.ai/public-cloud/filesystems/), [Lambda pricing](https://lambda.ai/pricing) |
| Lambda API check on 2026-09-27 at 23:00 UTC (16:00 PDT): no free capacity for any of the 24 instance types, in any region. `cluster/lambda/capacity_watch.py` records the stock history in `metrics/lambda-capacity.jsonl`. | Lambda API `GET /instance-types` |
| Modal prices: H100 0.001097 USD/s (3.95 USD/h), A100 80 GB 2.50 USD/h, L40S 1.95 USD/h, A10 1.10 USD/h, L4 0.80 USD/h. Starter plan gives 30 USD each month. | [Modal pricing](https://modal.com/pricing) |
| Superlinked SIE: Apache 2.0 inference engine with OpenAI-compatible `/v1/embeddings`, `/v1/chat/completions`, `/v1/completions`, `/v1/responses`. Catalog includes `bge-m3`, `splade-v3`, `colbertv2`, `qwen3-reranker`, `paddleocr-vl`, `glm-ocr`, `lightonocr`, `qwen3.8-27b`, `qwen3.6-27b`. Ships Helm charts, KEDA autoscaling, and Grafana dashboards. | [SIE on GitHub](https://github.com/superlinked/sie) |
| The class10 overflow settings use `https://api.superlinked.com/v1` with `Qwen/Qwen3.5-4B`. **VERIFY**: the hosted catalog and the credit terms with our key. | `class10/.env.example` |

## 8. Live web search for the fact check

| Fact | Source |
|---|---|
| Google closed the Custom Search JSON API to new customers. The API ends on 2027-01-01. | [Google CSE overview](https://developers.google.com/custom-search/v1/overview), [heise](https://www.heise.de/en/news/Google-is-discontinuing-its-free-web-search-index-for-developers-11152411.html) |
| Brave Search API has no free tier since February 2026. It costs 5 USD for 1,000 requests, with a 5 USD monthly credit. It has an LLM-context endpoint. | [implicator](https://www.implicator.ai/brave-drops-free-search-api-tier-puts-all-developers-on-metered-billing/) |
| Tavily gives 1,000 free credits each month. A basic search costs 1 credit. Pay-as-you-go costs 0.008 USD for each credit. | [Tavily credits](https://docs.tavily.com/documentation/api-credits) |

## 9. App stack

| Fact | Source |
|---|---|
| PydanticAI v2.0 released 2026-06-23. v2.41.0 released 2026-09-07. `OpenAIChatModel` replaces `OpenAIModel`. Tracing is OpenTelemetry-native. | [PydanticAI releases](https://github.com/pydantic/pydantic-ai/releases) |
| Chainlit is community-maintained since 2025-05-01. The latest release is v2.12.0. | [Chainlit](https://github.com/chainlit/chainlit) |
| Qdrant converts text to BM25 sparse vectors on the server since 1.15.2. Hybrid search uses RRF. | [Qdrant hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/) |
| Deep Agents (`deepagents`) 0.7.19 was released on 2026-09-24. It needs `langchain` 1.4.2 or later (LangGraph 1.2). `create_deep_agent` takes a model object, tools, a system prompt, subagents, middleware, `response_format`, a checkpointer, and a file-system backend. Built-in tools: `write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, and `task`. A model object can be `ChatOpenAI` with `base_url` and `default_headers`. | [Deep Agents](https://github.com/langchain-ai/deepagents), [Customization](https://docs.langchain.com/oss/python/deepagents/customization), [PyPI](https://pypi.org/project/deepagents/) |
| A subagent is a dictionary: `name`, `description`, `system_prompt`, `tools`, and optional `model` and `middleware`. Middleware with the same name replaces a default: `FilesystemMiddleware`, `SubAgentMiddleware`, `SummarizationMiddleware`, `PatchToolCallsMiddleware`. | [Customization](https://docs.langchain.com/oss/python/deepagents/customization) |
| Deep Agents 0.7.19 source (read on 2026-09-27): `create_deep_agent` does not add `write_todos`. `TodoListMiddleware` is in LangChain (`langchain.agents.middleware`). The harness adds `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`, and `task`, and a `general-purpose` subagent. A `HarnessProfile` (`excluded_tools`, `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)`) goes into `register_harness_profile` under the key `provider:model`. For `ChatOpenAI`, the key is `openai:` and the model name. | `deepagents/graph.py`, `deepagents/profiles/harness/harness_profiles.py` |
| Deep Agents summarization: with a model profile that has `max_input_tokens`, it starts at 85% of the limit and keeps 10%. Without it, it starts at 170,000 tokens and keeps 6 messages. A subagent with `response_format` returns its `structured_response` as JSON in the `ToolMessage`. | `deepagents/middleware/summarization.py`, `deepagents/middleware/subagents.py` |
| `langchain-openai` 1.6.6: `ChatOpenAI` sends `max_completion_tokens`, not `max_tokens`. `disable_streaming="tool_calling"` turns off streaming only for a call with tools. The fields `default_headers`, `http_async_client`, `profile`, `metadata`, and `stream_usage` exist. | `langchain_openai/chat_models/base.py`, `langchain_core/language_models/chat_models.py` |
| LangChain 1.4.2 has `ContextEditingMiddleware` with `ClearToolUsesEdit(trigger, keep, placeholder)`. It replaces old tool results when the context is above the trigger. | `langchain/agents/middleware/context_editing.py` |
| SIE images are bundle-specific: `default` (embeddings and rerank), `sglang-vision-extract` (LightOnOCR, GLM-OCR, PaddleOCR-VL), `transformers5`. The native API is `/v1/encode/{model}`, `/v1/score/{model}`, and `/v1/extract/{model}` with MessagePack bodies. An OCR result is Markdown in `entities[0].text` with the label `markdown`. `sie-sdk` 0.8.3 needs `websockets` below 15. | [SIE README](https://github.com/superlinked/sie), SIE model configs, `sie_sdk` 0.8.3 source |
| Tavily search: `POST https://api.tavily.com/search` with `Authorization: Bearer`. `search_depth` is `basic`, `advanced`, `fast`, or `ultra-fast`. `time_range` is `day`, `week`, `month`, or `year`. Error codes: 429 (usage limit), 432 and 433 (plan limits), 401 (bad key). | `tavily-python` 0.8.4 source |
| Notion API version `2025-09-03`: `POST /v1/data_sources/{id}/query` and `GET /v1/blocks/{id}/children`. `notion-client` 3.1.0 uses this version. | `notion_client/client.py`, `notion_client/api_endpoints.py` |
| `qdrant-client` 1.19.1 sends a `Document` with the model `Qdrant/bm25` to a remote server as text (server 1.15.3 or later). The local mode (`:memory:`) needs `fastembed` for BM25. | `qdrant_client/embed/model_embedder.py`, `qdrant_client/embed/embedder.py` |
| `trafilatura` 2.2.0: `extract(..., output_format="markdown")` is supported. | `trafilatura.settings.SUPPORTED_FORMATS` |

## 10. The Notion Bookmarks database (read-only check on 2026-09-26)

- Data source: `collection://1449b9ed-d198-44ab-b6f4-e5c570d2e8fa`.
- Properties: `Name` (title), `URL` (url), `Tags` (multi-select, one option), `Github` (text), `Youtube` (url), `Files & media` (file), `Created`, `Updated`.
- Rows: 998 in total (2017 to 2026). 917 rows have a URL. 11 rows have a GitHub link. 3 rows have a YouTube link. 2 rows have files.

| Year | Rows | With URL |
|---|---|---|
| 2017 | 77 | 77 |
| 2018 | 111 | 80 |
| 2019 | 28 | 0 |
| 2020 | 18 | 14 |
| 2021 | 203 | 202 |
| 2022 | 80 | 80 |
| 2023 | 67 | 66 |
| 2024 | 136 | 131 |
| 2025 | 213 | 209 |
| 2026 | 65 | 58 |

The corpus is small. We can index all of it. About 80 rows have no URL, so their text is in the page body. The ingest job must read the page blocks too.

## 11. Guardrails (checked 2026-09-27)

| Fact | Source |
|---|---|
| NeMo Guardrails 0.24.1 is the latest release. 0.24.0 added `check()` and `check_async()`. They run input or output rails without generation. The result status is `PASSED`, `MODIFIED`, or `BLOCKED`, with the name of the rail that blocked. | [Release notes](https://docs.nvidia.com/nemo/guardrails/latest/about/release-notes.html), [Check messages](https://docs.nvidia.com/nemo/guardrails/run-guardrailed-inference/using-python-apis/check-messages) |
| The NeMo server has a `/v1/checks` endpoint that calls `check_async()`. It returns 422 for a rail type that is not in the configuration. 0.24.1 adds admission control (an overload response when a request is shed), `GET /v1/health`, and `GET /healthz`. | [Release notes](https://docs.nvidia.com/nemo/guardrails/latest/about/release-notes.html) |
| 0.24.1: the content-safety rails parse Nemotron 3.5 responses. Streaming output rails fail closed when an action fails. | [Release notes](https://docs.nvidia.com/nemo/guardrails/latest/about/release-notes.html) |
| NeMo jailbreak detection (heuristics and the model) lets the request through when the detector is not available. The heuristics use GPT-2 perplexity: about 2 to 3 s on a CPU, about 0.1 s on a GPU. | [Jailbreak protection](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/jailbreak-protection) |
| NeMo supports images in input rails (multimodal content safety). The docs use a vision model as the judge. They do not name Nemotron 3.5 as the judge. **VERIFY** at Gate G0 that the image reaches Nemotron 3.5 through the content-safety rail. | [Multimodal tutorial](https://docs.nvidia.com/nemo/guardrails/get-started/tutorials/multimodal) |
| NeMo has no built-in rail for Llama Prompt Guard 2. A custom action is possible, but see the next row. | [Content safety catalog](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/content-safety) |
| NeMo has two engines. IORails runs input and output rails fast: parallel rails, admission control, direct rail actions. It does not run custom actions, retrieval, or dialog flows, and it accepts Colang 1.0 only. LLMRails runs everything. The `Guardrails` facade picks IORails when the config allows it. | [Engine feature support](https://github.com/NVIDIA-NeMo/Guardrails/blob/v0.24.1/docs/reference/engine-feature-support.mdx) |
| NeMo `/v1/checks` (v0.24.1): the request is an OpenAI chat request plus `guardrails` (`config_ids`, `rail_types`). The response has `status` (`passed`, `modified`, or `blocked`), `content`, and `rail`. It returns 422 for Colang 2.0 configs and for a rail type with no flows. | [Server source](https://github.com/NVIDIA-NeMo/Guardrails/blob/v0.24.1/nemoguardrails/server/api.py) |
| Nemotron 3.5 Content Safety in NeMo: model type `content_safety`, `engine: openai` with `base_url` to a vLLM server, and `chat_template_kwargs` (`enable_thinking: false`, `request_categories: "/categories"`). The chat template adds the taxonomy, so the prompt sends only the turns, with no system message. | [NeMo example](https://github.com/NVIDIA-NeMo/Guardrails/tree/v0.24.1/examples/configs/nemotron-3.5-content-safety) |
| `nvidia/Nemotron-3.5-Content-Safety`: released June 2026. Base model Gemma 3 4B IT (LoRA merged into full weights), `Gemma3ForConditionalGeneration`, 4.30 B parameters in BF16. Input: user text, an optional image, an optional assistant response, an optional custom policy. Output: `User Safety`, `Response Safety`, `Safety Categories`. Fast mode or a reasoning mode. 12 languages. Tested engines: vLLM 0.11.0 to 0.20.2, SGLang, Transformers. License: OpenMDW 1.1 (Hugging Face metadata). Not gated. NVIDIA says it runs on GPUs with 8 GB or more. | [Model card](https://huggingface.co/nvidia/Nemotron-3.5-Content-Safety), [NVIDIA blog](https://huggingface.co/blog/nvidia/nemotron-3-5-content-safety) |
| `meta-llama/Llama-Prompt-Guard-2-86M`: gated on Hugging Face (manual approval), Llama 4 license. | [Model page](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M) |

## 12. KV tier behind the GPU prefix cache (checked 2026-09-27)

| Fact | Source |
|---|---|
| llm-d has a "tiered prefix cache" path: HBM, then CPU RAM, then a filesystem. It supports the vLLM native `OffloadingConnector` (recommended), the LMCache connector, and SGLang HiCache. llm-d runs end-to-end CI for the native path and the LMCache path on GPUs. The default example is Qwen3-32B, TP 2, H100, 100 GB CPU tier. | [llm-d guide](https://github.com/llm-d/llm-d/blob/main/guides/tiered-prefix-cache/README.md), [well-lit path](https://llm-d.ai/docs/well-lit-paths/foundations/tiered-prefix-cache) |
| The native `OffloadingConnector` turns off the vLLM hybrid KV cache manager (HMA). Gemma 4 then fails to start, because its sliding-window and full-attention layers have different head sizes. The fix is `--no-disable-hybrid-kv-cache-manager`. | [llm-d guide](https://github.com/llm-d/llm-d/blob/main/guides/tiered-prefix-cache/README.md) |
| The llm-d endpoint picker uses two prefix-cache scorers, one for the GPU tier and one for the CPU tier. The CPU capacity is set by hand (`lruCapacityPerServer`), because vLLM does not send CPU block metrics. The guide does not show P/D and CPU offload together. | [llm-d guide](https://github.com/llm-d/llm-d/blob/main/guides/tiered-prefix-cache/README.md) |
| vLLM issue 56871 (open, 2026-09-14): with HMA on, the native CPU offload divides its capacity by the number of KV cache groups. In the report, the CPU tier gave zero hits. The fix (PR 56953) is not merged. | [Issue 56871](https://github.com/vllm-project/vllm/issues/56871), [PR 56953](https://github.com/vllm-project/vllm/pull/56953) |
| vLLM PR 37885 (canonical KV cache allocation for HMA models) is not merged. The llm-d blog on hybrid models (2026-06-13) needs it for its HMA offload results. | [PR 37885](https://github.com/vllm-project/vllm/pull/37885), [llm-d blog](https://llm-d.ai/blog/serving-hybrid-models-at-scale-in-llm-d) |
| HMA puts the layers of Gemma-3-27B into 6 KV cache groups (10 layers in each group). | [vLLM HMA design](https://docs.vllm.ai/en/latest/design/hybrid_kv_cache_manager/) |
| LMCache has a Gemma 4 recipe. It tests `google/gemma-4-31B-it` (TP 2) with `LMCacheMPConnector` and `lmcache server --l1-size-gb 100 --eviction-policy LRU`. HMA stays on, with no extra flags. Cold-to-warm TTFT improved 3.7 times for 31B (with MTP). In MP mode, LMCache runs as a separate server process on each node. | [LMCache Gemma 4](https://docs.lmcache.ai/recipes/gemma4.html), [LMCache hybrid models](https://docs.lmcache.ai/mp/hybrid_models.html) |
| LMCache releases: 0.5.5 on 2026-09-12, 0.5.6rc1 on 2026-09-26. vLLM v0.30.0 (2026-09-22) guards the `lmcache_mp_connector` state transitions and fixes sliding-window coverage in KV offload. **VERIFY** at Gate G1: LMCache 0.5.5 or 0.5.6 with vLLM v0.30. | [PyPI lmcache](https://pypi.org/project/lmcache/), [vLLM v0.30.0](https://github.com/vllm-project/vllm/releases/tag/v0.30.0) |
| vLLM v0.29 and v0.30 add Mooncake Store features: decode KV saves, hybrid-model saves, and heterogeneous TP. We found no Gemma 4 test for Mooncake Store. | [vLLM v0.30.0](https://github.com/vllm-project/vllm/releases/tag/v0.30.0), [vLLM v0.29.0](https://github.com/vllm-project/vllm/releases/tag/v0.29.0) |
| The Lambda 2 x H100 SXM instance has 52 vCPUs and 450 GiB RAM. | [Lambda pricing](https://lambda.ai/pricing) |
| The llm-d LMCache path uses the in-process `LMCacheConnectorV1` (`LMCACHE_MAX_LOCAL_CPU_SIZE=100.0`, pod memory 500 GiB). In vLLM v0.30, this class does not support HMA. | [llm-d LMCache manifest](https://github.com/llm-d/llm-d/blob/main/guides/tiered-prefix-cache/modelserver/gpu/vllm/lmcache-connector/cpu/base/patch-vllm.yaml), [vLLM source](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/distributed/kv_transfer/kv_connector/v1/lmcache_connector.py) |
| vLLM v0.30 loads `LMCacheMPConnector` from the `lmcache` package when the package has it. That class supports HMA in lmcache 0.5.5 and 0.5.6rc1. The fallback class in vLLM stops with an error on HMA models. | [vLLM source](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/distributed/kv_transfer/kv_connector/v1/lmcache_mp_connector.py), [LMCache source](https://github.com/LMCache/LMCache/blob/v0.5.5/lmcache/integration/vllm/lmcache_mp_connector.py) |
| `MultiConnector` keeps HMA on only if all child connectors support it. The NIXL connector supports HMA. | [vLLM source](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/distributed/kv_transfer/kv_connector/v1/multi_connector.py) |
| llm-d main pins the model server image `vllm/vllm-openai:v0.26.0`. | [llm-d image component](https://github.com/llm-d/llm-d/blob/main/guides/recipes/modelserver/components/images/gpu-vllm/release/kustomization.yaml) |
| The vLLM image build installs `lmcache >= 0.3.9`, `nixl == 1.4.1`, and `mooncake-transfer-engine >= 0.3.12` when `INSTALL_KV_CONNECTORS=true`. | [vLLM requirements](https://github.com/vllm-project/vllm/blob/v0.30.0/requirements/kv_connectors.txt) |

## 13. llm-d P/D decision (checked 2026-09-27)

| Fact | Source |
|---|---|
| The `disagg-profile-handler` asks the `prefix-based-pd-decider` for each request. The decider looks at how much of the prompt is already cached on the chosen decode pod. If the uncached part is large, the handler also runs the prefill profile and returns a prefill pod and a decode pod. If not, the decode pod does the full request. | [llm-d disaggregation](https://llm-d.ai/docs/architecture/advanced/disaggregation), [disagg package](https://pkg.go.dev/github.com/llm-d/llm-d-router/pkg/epp/framework/plugins/scheduling/profilehandler/disagg) |
| Decider parameters in router v0.11.0: `nonCachedTokens` (example 512, and 0 turns the split off) and `promptTokens` (example 1024). | [Router v0.11.0](https://github.com/llm-d/llm-d-router/releases/tag/v0.11.0) |
| Router config format: `apiVersion: llm-d.ai/v1alpha1`, `kind: EndpointPickerConfig`, with `featureGates` (for example `flowControl`), `plugins`, `schedulingProfiles`, `dataLayer`, and `flowControl`. A priority band has `priority`, `defaultRequestTTL`, `fairnessPolicyRef`, and `orderingPolicyRef`. | [Config types](https://github.com/llm-d/llm-d-router/blob/v0.11.0/apix/config/v1alpha1/endpointpickerconfig_types.go), [P/D example](https://github.com/llm-d/llm-d-router/blob/v0.11.0/deploy/config/pd-epp-config.yaml) |
| Request headers of the router: `x-llm-d-inference-fairness-id`, `x-llm-d-inference-objective` (the name of an `InferenceObjective`, `llm-d.ai/v1alpha2`, with `spec.priority`), and `x-llm-d-inference-ttl` (a Go duration such as `1500ms`, the queue TTL for one request). | [Header constants](https://github.com/llm-d/llm-d-router/blob/v0.11.0/pkg/epp/metadata/consts.go), [Admission](https://github.com/llm-d/llm-d-router/blob/v0.11.0/pkg/epp/requestcontrol/admission.go) |
| Router reject reasons in `x-llm-d-request-dropped-reason`: `rejected-saturated`, `rejected-no-endpoints`, `rejected-ttl-expired`, `rejected-context-cancelled`, `rejected-shutting-down`, `rejected-internal`, `evicted`, `evicted-queue-pressure`, `evicted-priority`. | [Error codes](https://github.com/llm-d/llm-d-router/blob/v0.11.0/pkg/common/error/error.go) |
| The saturation detector (`utilization-detector`) has `queueDepthThreshold` (default 5), `kvCacheUtilThreshold` (default 0.8), `metricsStalenessThreshold` (default 200 ms), and `stalenessPolicy`. The default policy `saturated` counts a pod with stale metrics as full. This is the class7 rule "unknown is not idle". | [Detector config](https://github.com/llm-d/llm-d-router/blob/v0.11.0/pkg/epp/framework/plugins/flowcontrol/saturationdetector/utilization/config.go) |
| The session-affinity scorer has two strategies: `encoded_endpoint_header` (the default) and `session_id`. The `session_id` strategy reads `x-session-id`. | [Scorer source](https://github.com/llm-d/llm-d-router/tree/v0.11.0/pkg/epp/framework/plugins/scheduling/scorer/sessionaffinity) |
| The precise prefix path of llm-d: vLLM runs with `--block-size=64` and `--kv-events-config` (ZMQ publisher on port 5556, topic `kv@<pod ip>:<port>@<model>`). The router uses `token-producer`, `endpoint-notification-source`, and `precise-prefix-cache-producer` with pod discovery (socket port 5556, replay port 5559). The guide runs one router replica with leader election off. | [llm-d guide](https://github.com/llm-d/llm-d/tree/main/guides/precise-prefix-cache-routing) |
| vLLM v0.30.0 source (read on 2026-09-27): every flag in our engine manifests exists. `KVEventsConfig.replay_endpoint` is `None` by default, so each pod must set `tcp://*:5559` for the replay port of the router. `KVTransferConfig` has `kv_load_failure_policy` (`recompute` or `fail`, default `fail`) and `kv_buffer_device`. `--disable-access-log-for-endpoints` takes a comma-separated list. | `vllm/config/kv_events.py`, `vllm/config/kv_transfer.py`, `vllm/engine/arg_utils.py`, `vllm/entrypoints/launchers/cli_args.py` at v0.30.0 |
| llm-d-router v0.11.0 source (read on 2026-09-27): the routing sidecar reads the prefill pod from the header `x-prefiller-host-port` (`PrefillEndpointHeader`). Its flags `--port`, `--kv-connector` (default `nixlv2`), and `--secure-proxy` (default `true`) exist. The model-server port is 8200 by default (`--model-server-port`, and the old name `--vllm-port`). `--enable-ssrf-protection` with `--inference-pool` limits the prefill hosts to the pods of the pool (a P1 hardening step). | `pkg/common/routing/common.go`, `pkg/sidecar/proxy/options.go` |
| Pre-flight on 2026-09-27 (read-only registry and Hugging Face checks): all pinned image tags exist. The llm-d router chart is `oci://ghcr.io/llm-d/charts/llm-d-router-gateway` with the tag `v0.11.0` (the tag `0.11.0` does not exist). The router image is `ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.11.0`. The HF token reads every model of the plan, which includes Prompt Guard 2 and Glimmer. | GHCR, Docker Hub, MCR, and Hugging Face APIs |
| Envoy AI Gateway releases: v1.1.0 (2026-08-21), v1.0.0, v0.7.0. The llm-d guide installs Envoy Gateway v1.8.1 and AI Gateway v0.7.0. The token rate-limit example uses a `BackendTrafficPolicy` with global rules on the `x-tenant-id` header, a Redis backend, and response costs from the metadata key `llm_total_token` (`AIGatewayRoute.llmRequestCosts`). | [AI Gateway releases](https://github.com/envoyproxy/ai-gateway/releases), [Token rate limit example](https://github.com/envoyproxy/ai-gateway/tree/v1.1.0/examples/token_ratelimit), [llm-d doc](https://github.com/llm-d/llm-d/blob/main/docs/infrastructure/gateway/envoy-ai-gateway.md) |

## 14. Model candidates (checked 2026-09-27)

| Fact | Source |
|---|---|
| `meta-models/Muse-Glimmer-30B` (Meta, 2026-08-10): Apache 2.0, not gated, 29.8 B parameters in BF16 (a 1.8 B vision encoder included). 52 layers: 13 full-attention and 39 sliding-window (window 2,048). Both layer types use 2 KV heads of size 128. Context 131,072 tokens. Up to 4,096 visual tokens for each image. | [Model card](https://huggingface.co/meta-models/Muse-Glimmer-30B), [VentureBeat](https://venturebeat.com/technology/meta-returns-to-open-source-with-muse-glimmer-an-apache-2-0-licensed-30b-parameter-ai-model-optimized-for-agents-available-now) |
| Glimmer card benchmarks (Glimmer / Gemma 4 31B / Qwen3.6-27B): MCP Atlas 75.5 / 54.2 / 62.5. GAIA2 43.3 / 36.4 / 40.0. ScreenSpot Pro 75.4 / 75.9 / 76.1. OSWorld-Verified 65.9 / 58.5 / 75.6. GPQA Diamond 83.5 / 85.7 / 84.2. | [Model card](https://huggingface.co/meta-models/Muse-Glimmer-30B) |
| Glimmer has no FP8 checkpoint from Meta or RedHatAI. NVIDIA published an NVFP4 version (Blackwell only). On an H100 we need BF16 or vLLM online FP8. vLLM supports Glimmer since v0.28 with the older `muse_glimmer` tool and reasoning parsers. | [Hugging Face search](https://huggingface.co/models?search=Muse-Glimmer-30B), [vLLM v0.28.0](https://github.com/vllm-project/vllm/releases/tag/v0.28.0) |
| Open vLLM issues for Glimmer: 55395 (the tool parser shows partial ATEM markers in streamed text), 52594 (structured outputs are skipped when reasoning is on), 54453 (structured output makes decode quadratic), 54528 (parser move to the new parser engine). | [vLLM issues](https://github.com/vllm-project/vllm/issues?q=Glimmer) |
| Gemma 4 31B card: Tau2 76.9, MMMU Pro 76.9, GPQA Diamond 84.3, 256K context, 70 to 1,120 visual tokens for each image. Its vLLM parsers use the new parser engine (`vllm/parser/gemma4.py`). | [Model card](https://huggingface.co/google/gemma-4-31B-it), [vLLM source](https://github.com/vllm-project/vllm/tree/v0.30.0/vllm/parser) |
| Open vLLM issues for Gemma 4: 53363 (`tool_choice="required"` is not enforced), 47906 (bare argument values come back as strings), 57231 and 57232 (channel markers show in the text without the reasoning parser), 54926 (MTP with NIXL P/D does not move the draft KV). Issue 39407 (FP8-block output) was closed as stale, not fixed. | [vLLM issues](https://github.com/vllm-project/vllm/issues?q=gemma4) |
| Connector bugs on models with mixed head sizes, such as Gemma 4: 52234 (a failed NIXL receive with HMA can return HTTP 200 with bad tokens, seen with different block sizes on prefill and decode) and 57938 (a failed LMCache load causes an AssertionError). | [Issue 52234](https://github.com/vllm-project/vllm/issues/52234), [Issue 57938](https://github.com/vllm-project/vllm/issues/57938) |
| Open vLLM issues for Qwen3.5 and Qwen3.8 hybrids: 55766 (NaN logits after a prefix-cache hit), 43587 (prefix cache fails for multimodal requests), 37729 (engine deadlock under load with FP8 and prefix caching). | [Issue 55766](https://github.com/vllm-project/vllm/issues/55766), [Issue 43587](https://github.com/vllm-project/vllm/issues/43587), [Issue 37729](https://github.com/vllm-project/vllm/issues/37729) |
| `NVIDIA-Nemotron-3.5-Lightning-30B-A3B` (2026-08-11): text input only, Mamba-2 with MoE and attention, 3 B active parameters. | [Model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16) |
| vLLM issue 53130 (open, any model): the scheduler can stop admitting requests when running plus deferred requests reach `max_num_seqs`. The engine still reports healthy. | [Issue 53130](https://github.com/vllm-project/vllm/issues/53130) |
