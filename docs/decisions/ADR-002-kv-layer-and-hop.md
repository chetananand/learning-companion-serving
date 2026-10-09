# ADR-002: KV layer and hop

Status: accepted (2026-09-27). Revision 2 (2026-09-28, from the G0 data): the hop goes through the LMCache tier. The owner accepted revision 2 on 2026-09-28 (19:35 PDT). The debate is in `DEBATE-LOG.md`, point 3. Owners: the project owner and Claude.

## Context

The handout asks for a named hop backend: "mooncake, or a name you implement" (L607). A dashboard must show "Mooncake KV (or your hop store)" (L753). Two jobs exist:

1. **The hop**: move the KV of one prompt from the prefill pod to the decode pod.
2. **The CPU tier**: keep KV in host RAM, so a prefix survives an HBM eviction or a pod restart.

Gemma 4 31B mixes two head sizes. The sliding-window layers use 256, and the full layers use 512. vLLM needs its hybrid KV cache manager (HMA) for this model. Each KV connector must support HMA.

## Options for the CPU tier

| Option | For | Against |
|---|---|---|
| K0: none | Nothing new can break. | An evicted prefix needs a full prefill. A restarted pod starts cold. |
| K1: vLLM native `OffloadingConnector` | In vLLM. The llm-d default. | It turns off HMA, and Gemma 4 then fails to start. With HMA on, issue 56871 divides the CPU capacity by the number of KV groups (fix not merged). |
| **K2: LMCache MP mode** | LMCache tested Gemma 4 31B with it. The tier is a separate process, so it stays when a pod restarts, and all pods on the node share it. | One more component. Not in the llm-d CI (llm-d tests the in-process connector, which does not support HMA). |
| K3: Mooncake Store | The handout names Mooncake. | No Gemma 4 test. More services. No RDMA on Lambda. |

## Decision

1. Hop backend: NIXL through the vLLM `NixlConnector`, as in the llm-d P/D path. Its name in our hop records is `nixl`.
2. CPU tier: K2. One `lmcache server` on each GPU node (`--l1-size-gb 150 --eviction-policy LRU`). Each vLLM pod joins `NixlConnector` and `LMCacheMPConnector` through `MultiConnector`.
3. The same `--block-size` on both pods (issue 52234).
4. Fallback: K0, with the reason in `DESIGN.md`.

## Source check (2026-09-27)

1. vLLM v0.30 uses the `LMCacheMPConnector` of the `lmcache` package when the package has it. That class supports HMA in lmcache 0.5.5 and 0.5.6rc1. The fallback class in vLLM does not.
2. `MultiConnector` keeps HMA on only if all child connectors support it. `NixlConnector` does.

## Revision 2 (2026-09-28): the hop goes through the LMCache tier

G0 ran the P/D path on node 2 with the small Gemma 4 model (E4B) on two A6000 GPUs. For one prompt of 5,544 tokens:

| Hop | Transfer | Request time |
|---|---|---|
| NIXL, the decision above | 117 MB in 4.0 s (29.5 MB/s) | 4.9 s |
| NIXL, with both GPUs in each pod | 117 MB in 0.8 s (146 MB/s) | 1.7 s |
| LMCache tier (sidecar `shared-storage`) | the decode pod loads 5,376 tokens from the CPU tier | 0.9 s |
| No split (the decode pod computes the prompt) | none | 0.8 s |

The cause: UCX sends the NIXL transfer between two pods over TCP (`tcp/eth0`, software emulation). It does not use `cuda_ipc`, although the image has it. These settings did not change that:

- both GPUs in each pod (own GPU first)
- `hostPID`
- `UCX_TLS=all`

We expect the same problem for the pods on the H100 node. A prompt of 20K tokens has about 4.7 GB of KV on the 31B model. At this rate, its hop takes about 30 s.

New decision:

1. The hop goes through the LMCache tier. The prefill pod stores the KV in the shared CPU tier, and the decode pod loads it. The llm-d routing sidecar does this with `--kv-connector=shared-storage`. Both pods use `LMCacheMPConnector` (`kv_both`, isolated IPC). Our name for this hop store is `lmcache`.
2. The NIXL hop stays as the preset `nixl-hop` (`tools/variant.py`). E4 compares the two hop media.
3. K0 (the overlay `t2-k0`) has no LMCache, so its hop goes back to NIXL.

## Revision 3 (2026-09-29): a store barrier, and a larger CPU tier

Session 1 ran the 31B model on two H100 GPUs. Two facts changed the hop:

1. The prefill engine answers before LMCache has stored the KV. The sidecar calls the decode pod at once, so the decode lookup misses, and the decode pod computes the prompt again. We sent one prompt of 8,500 tokens during load. Gaps of 10 and 20 ms gave 0 hits in 10 tries. A gap of 50 ms gave 1 hit in 5, and 100 ms gave 5 hits in 5. With a hit, the decode pod loaded 8,448 tokens.
2. LMCache keeps the sliding-window layers of Gemma 4 in full. It stored 1.42M tokens as 1.23 TB, about 865 KB for each token. The GPU cache needs about 237 KB for each token. The 150 GiB tier evicts at 80%, so it held about 149K tokens, fewer than the GPU cache of one pod (173,657). A prompt that left the GPU cache was also gone from the tier.

New decision:

1. A store barrier (`control/barrier/proxy.py`) runs in the prefill pod on port 8000, in front of vLLM on 8200. It holds the prefill leg of the sidecar (not streaming, one output token) for at least 0.1 s. Then it waits until the LMCache store counters show no store in progress, for 0.5 s at most. With no LMCache (K0, `nixl-hop`), it does not hold.
2. The tier is 250 GiB, with eviction at 90%: about 280K tokens, 1.6 times the GPU cache of one pod.
3. The report states the cost. A split pays the prefill, the hold, and the load from the CPU tier (8,448 tokens: 0.37 s). A single request is faster with no split. The split helps when the decode pod has other streams (E5).

## Consequences

- Experiment E16 measures the tier (K0 against K2) for agent sessions and pod restarts.
- We dropped experiment E17 (NIXL against MooncakeConnector).
- The "Hop store" dashboard shows the LMCache metrics (moved bytes, lookups, stores, CPU tier use) and the `vllm:nixl_*` metrics (for `nixl-hop`).
- lmcache 0.5.6rc1 adds KV events for the MP connector. With one GPU node, all pods share one tier, so we do not score the CPU tier in the router yet.

## VERIFY at Gate G1

1. The vLLM v0.30 image has `lmcache` 0.5.5 or later.
2. Gemma 4 starts with `MultiConnector` and HMA on.
3. A prefix that left HBM comes back from the CPU tier.
4. The tier keeps its data when a vLLM pod restarts.
5. The IPC settings between the vLLM pods and the LMCache server.
