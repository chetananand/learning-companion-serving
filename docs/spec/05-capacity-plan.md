# Capacity plan (Part 1 on paper)

This file answers H-35 to H-44. All numbers come from `tools/capacity.py`. Run it again after each change:

```bash
python3 tools/capacity.py --gpu h100-sxm-80gb
python3 tools/capacity.py --gpu h100-sxm-80gb --kv-bytes 1
```

The weights and the overhead are estimates. After the first boot, replace them with three values from the vLLM startup log:

- "Available KV cache memory"
- "GPU KV cache size"
- "Maximum concurrency for N tokens per request"

Save the log in `metrics/boot-<pod>-<date>.log`.

## 1. The formula

The handout formula (L580):

```text
max_concurrent_seqs ~= (HBM - weights - activations) / (kv_bytes_per_token x max_len)
```

Our models are hybrids. A part of their KV does not grow with the length. Thus we use this form:

```text
per_seq_kv(L) = full_attn_bytes_per_token x L
              + swa_bytes_per_token x min(L, window)     (sliding-window layers: Gemma 4, Muse Glimmer)
              + state_bytes_per_seq                      (Gated DeltaNet layers: Qwen3.5 architecture)

max_concurrent_seqs(L) ~= (HBM x util - weights - overhead) / per_seq_kv(L)
```

`overhead` is the activation peak, the CUDA graphs, and the buffers (paper value 6 GiB for a dense model near 30B parameters).

## 2. The GPU that we run (H-35)

| Item | Value |
|---|---|
| GPU | NVIDIA H100 SXM 80 GB (Lambda `2x H100 SXM`, and `4x H100 SXM` for the scale session) |
| Usable HBM | 79.65 GiB (81,559 MiB in `nvidia-smi`) |
| `--gpu-memory-utilization` | 0.90, so vLLM uses 71.7 GiB |
| HBM bandwidth | 3.35 TB/s |
| Dense FP8 | 1,979 TFLOPS peak. The plan uses 40% of peak. |
| GPU link | NVLink between the GPUs of the instance (**VERIFY** with `nvidia-smi topo -m`) |
| Host RAM | 450 GiB on the 2 x H100 instance. The LMCache CPU tier uses 150 GiB of it. |

Each vLLM pod has a full GPU. The prefill pod and the decode pod have the same KV budget.

## 3. Bytes for each token (H-39)

BF16 KV (2 bytes for each value):

| Model | Full-attention bytes for each token | Sliding-window bytes for each token (window) | Fixed state for each sequence |
|---|---|---|---|
| Gemma 4 31B (main) | 40,960 (10 layers x 4 KV heads x 512 x 1 tensor, because K = V) | 819,200 (50 layers x 2 x 16 x 256), only for the last 1,024 tokens | 0 |
| Muse Glimmer 30B (challenger) | 13,312 (13 layers x 2 x 2 x 128) | 39,936 (39 layers x 2 x 2 x 128), only for the last 2,048 tokens | 0 |
| Qwen3.8-27B (dropped) | 65,536 (16 layers x 2 x 4 x 256) | 0 | 74.8 MiB (48 Gated DeltaNet layers) |
| Gemma 4 26B-A4B (dropped) | 10,240 | 204,800 (window 1,024) | 0 |

The sliding-window part of Gemma 4 31B is large: 800 MiB for each sequence after 1,024 tokens. The global part is small: 40 KiB for each token. Muse Glimmer has 2 KV heads on all layers, so its KV is about 6 times smaller at 8K.

## 4. Concurrency on one H100 (H-36 to H-38)

BF16 KV, `util` 0.90:

| Model | KV budget GiB | L = 8K (app length) GiB/seq, max seqs | L = 24K GiB/seq, max seqs | L = 32K (`max_len`) GiB/seq, max seqs | L = 128K GiB/seq, max seqs |
|---|---|---|---|---|---|
| Gemma 4 31B FP8-dynamic | 35.3 | 1.09, 32 | 1.72, 20 | 2.03, 17 | 5.78, 6 |
| Muse Glimmer 30B, online FP8 | 33.7 | 0.18, 189 | 0.38, 88 | 0.48, 69 | 1.70, 19 |
| Muse Glimmer 30B, BF16 | 10.2 | 0.18, 57 | 0.38, 26 | 0.48, 21 | 1.70, 5 |
| Qwen3.8-27B FP8 (dropped) | 37.7 | 0.57, 65 | 1.57, 23 | 2.07, 18 | 8.07, 4 |

FP8 KV (experiment E6) doubles each "max seqs" value (for example, Gemma 4 31B: 64 at 8K, 34 at 32K).

The app length: the product spec gives the call shapes. The draft call (S3) is about 8K tokens. Verify steps grow from 2.5K to 6K tokens. We use 8K as the app length. The load generator records the real lengths (FR-14). After the first real runs, we replace 8K with the measured p50 and p95.

## 5. What a model switch does to bytes for each token (H-40)

If Gate G1 picks the challenger, a switch from Gemma 4 31B to Muse Glimmer 30B (online FP8) changes the numbers like this:

| Item | Gemma 4 31B | Muse Glimmer 30B | Change |
|---|---|---|---|
| Full-attention bytes for each token | 40,960 | 13,312 | -67% |
| Fixed sliding-window bytes for each sequence | 800 MiB | 78 MiB | -90% |
| KV for one 8K sequence | 1.09 GiB | 0.18 GiB | -83% |
| KV for one 32K sequence | 2.03 GiB | 0.48 GiB | -76% |
| Max seqs at 8K | 32 | 189 | x 5.9 |
| Max seqs at 32K | 17 | 69 | x 4.1 |
| KV moved by one 8K hop | 1,120 MiB | 182 MiB | -84% |

Result: a switch changes the limiter. With Gemma 4, the KV blocks on the decode pod limit long agent sessions. With Glimmer, `max-num-seqs` and the decode compute limit first, and the hop is almost free. "Bytes for each token" alone gives the wrong answer for hybrid models, because the fixed part for each sequence dominates at our app length.

The first spec compared Qwen3.8-27B. It doubled the concurrency at 8K but gave no gain at 32K. We dropped it for its open prefix-cache bugs (`DEBATE-LOG.md`, point 1).

## 6. The hop and the CPU tier on paper

One 8K prompt, BF16 KV, with no prefix-cache hit:

| Model | Hop size | NVLink (100 GB/s effective) | PCIe 4.0 (20 GB/s) | 10 GbE TCP (1.1 GB/s) | Prefill on H100 (paper) |
|---|---|---|---|---|---|
| Gemma 4 31B | 1,120 MiB | 11.7 ms | 58.7 ms | 1,068 ms | 0.62 s |
| Muse Glimmer 30B | 182 MiB | 1.9 ms | 9.5 ms | 174 ms | 0.58 s |

- On NVLink, the hop costs about 2% of the prefill time for Gemma 4. Over 10 GbE between two instances, the hop costs more than the prefill. Thus the prefill pod and the decode pod stay on one node.
- A hit in the LMCache CPU tier moves the same bytes from host RAM to the GPU. At PCIe speed, an 8K Gemma 4 prompt loads in about 25 to 60 ms, against 0.62 s for a new prefill.
- The CPU tier (150 GiB) holds about 137 Gemma 4 prompts of 8K, or about 840 Glimmer prompts of 8K.

## 7. Why this GPU and not a smaller or cheaper one (H-43)

| GPU (Lambda) | USD for each GPU-hour | Model fits with KV | FP8 compute | Paper TTFT, 8K prompt | Two GPUs with NVLink on one instance | Result |
|---|---|---|---|---|---|---|
| A6000 48 GB | 1.09 | yes, but only 6.8 GiB KV | no | 7.96 s | 2x or 4x, PCIe | Fails SLO-1. We use it for node 2 and the dev days. |
| A10 24 GB | 1.29 | no | no | - | no | The model does not fit. |
| A100 80 GB | 2.79 | yes, 35.6 GiB KV (FP8 weights, BF16 compute) | no | 3.95 s | only in the 8x shape | Fails SLO-1. The 8x shape costs too much. |
| GH200 96 GB | 2.29 | yes | yes | about 0.6 s | no (1x only, ARM CPU) | The P/D hop must cross the network. |
| H100 PCIe 80 GB | 3.29 | yes | yes | about 0.7 s | no (1x only) | No P/D split on one node. |
| H100 SXM 80 GB | 4.19 (2x), 4.09 (4x) | yes, 35.3 GiB KV | yes | 0.62 s | yes (2x, 4x) | Our choice. |
| B200 180 GB | 6.89 (2x) | yes, 123.8 GiB KV | yes | 0.27 s | yes | More HBM than we can fill. Costs 64% more. |

The H100 SXM is the smallest Lambda GPU that meets SLO-1 with FP8 compute and gives two GPUs with NVLink in one instance.

## 8. The limiter that we expect first (H-41)

Hypothesis for T2 (one prefill pod and one decode pod, Gemma 4 31B FP8, BF16 KV):

1. **RAG mix: prefill compute on the prefill pod.** A verified user turn has about 22K uncached prompt tokens after the prefix cache. At about 13K tokens each second (paper), one prefill pod handles about 0.6 verified turns each second. E1 measured about 10.5K tokens each second (81% of the paper value), so one pod handles about 0.48 verified turns each second. The planner rule uses 10,500.
2. **Agent mix with long sessions: KV blocks on the decode pod.** At 24K tokens, the decode pod holds about 20 sequences. `max_num_seqs` is 24. The LMCache tier softens this limit for sessions that pause.
3. **Not the weights.** The weights use 30.4 GiB of 71.7 GiB.
4. **Not the interconnect** on one node. The NVLink hop is about 2% of the prefill time.
5. **Not the scheduler.** vLLM does continuous batching and chunked prefill. The router flow control holds requests before the pick, so the vLLM waiting queue stays short.

Decode step time on paper: the weight read is about 9.8 ms (32.7 GB at 3.35 TB/s). At 24 running sequences of 8K, the KV read adds about 8 ms. Thus the inter-token latency is near 20 ms, well inside SLO-2 (50 ms).

Experiments E1, E2, and E3 test this hypothesis. The presentation says if it was correct (H-42).

## 9. Node 2 GPUs (2 x A6000 48 GB)

Node 2 does not serve the main model. Its GPUs hold the small models on HAMi slices. The dev model runs only on dev days. **VERIFY** the slice sizes at Gate G0.

| GPU | Pod | Model | HAMi slice |
|---|---|---|---|
| GPU 0 | SIE | embed, rerank, OCR models | 16,000 MiB |
| GPU 0 | `vllm-dev-prefill` (dev days) | `gemma-4-E4B-it` (8.0 B parameters, BF16) | 26,000 MiB |
| GPU 1 | `guard-safety` | Nemotron 3.5 Content Safety (4.3 B, BF16) | 16,000 MiB |
| GPU 1 | `guard-injection` | Llama Prompt Guard 2 (86 M) | 2,000 MiB |
| GPU 1 | `vllm-dev-decode` (dev days) | `gemma-4-E4B-it` | 26,000 MiB |

One A6000 cannot serve Gemma 4 31B inside SLO-1. It leaves 6.8 GiB of KV, and the paper TTFT for an 8K prompt is about 8 s. Thus we do not measure SLOs on node 2.
