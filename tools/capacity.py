"""Paper capacity math for the handout Part 1.

max_concurrent_seqs ~= (HBM * util - weights - overhead) / per_seq_kv(L)

The handout formula uses kv_bytes_per_token * max_len. Our candidate models are
hybrids, so per_seq_kv(L) has two parts:

  per_seq_kv(L) = full_attn_bytes_per_token * L        (grows with L)
                + swa_bytes_per_token * min(L, window) (Gemma 4 sliding layers)
                + state_bytes_per_seq                 (Qwen3.5-arch Gated DeltaNet)

All inputs come from the config.json files read on 2026-09-26 (see
docs/spec/02-research-findings.md). Weights and overhead are estimates. Replace
them with the vLLM startup log values ("Available KV cache memory", "GPU KV cache
size", "Maximum concurrency") after the first real boot.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

GIB = 1024**3
MIB = 1024**2


@dataclass(frozen=True)
class Gpu:
    name: str
    hbm_gib: float
    hbm_tb_s: float
    dense_tflops_fp8: float | None
    dense_tflops_bf16: float


@dataclass(frozen=True)
class Model:
    name: str
    weights_gib: float  # estimate for the named checkpoint; verify at boot
    overhead_gib: float  # activations + CUDA graphs + buffers; verify at boot
    active_params_b: float  # for the prefill FLOP estimate
    full_layers: int
    full_kv_heads: int
    full_head_dim: int
    full_k_eq_v: bool = False  # Gemma 4 global layers store K only (K = V)
    swa_layers: int = 0
    swa_kv_heads: int = 0
    swa_head_dim: int = 0
    swa_window: int = 0
    gdn_layers: int = 0
    gdn_k_heads: int = 0
    gdn_v_heads: int = 0
    gdn_k_dim: int = 0
    gdn_v_dim: int = 0
    gdn_conv_kernel: int = 0
    fp8_compute: bool = True  # False for a BF16 checkpoint: prefill runs at BF16 speed

    def full_bytes_per_token(self, kv_bytes: int) -> int:
        tensors = 1 if self.full_k_eq_v else 2
        return self.full_layers * tensors * self.full_kv_heads * self.full_head_dim * kv_bytes

    def swa_bytes_per_token(self, kv_bytes: int) -> int:
        return self.swa_layers * 2 * self.swa_kv_heads * self.swa_head_dim * kv_bytes

    def state_bytes_per_seq(self, state_bytes: int = 2) -> int:
        if not self.gdn_layers:
            return 0
        recurrent = self.gdn_v_heads * self.gdn_k_dim * self.gdn_v_dim * state_bytes
        conv_ch = 2 * self.gdn_k_heads * self.gdn_k_dim + self.gdn_v_heads * self.gdn_v_dim
        conv = conv_ch * (self.gdn_conv_kernel - 1) * state_bytes
        return self.gdn_layers * (recurrent + conv)

    def per_seq_bytes(self, length: int, kv_bytes: int) -> int:
        swa_tokens = min(length, self.swa_window) if self.swa_layers else 0
        return (
            self.full_bytes_per_token(kv_bytes) * length
            + self.swa_bytes_per_token(kv_bytes) * swa_tokens
            + self.state_bytes_per_seq()
        )


GPUS = {
    "h100-sxm-80gb": Gpu("H100 SXM 80GB", 79.65, 3.35, 1979.0, 989.0),
    "a6000-48gb": Gpu("RTX A6000 48GB", 47.99, 0.768, None, 154.8),
    "a100-sxm-80gb": Gpu("A100 SXM 80GB", 80.0, 2.04, None, 312.0),
    "a100-sxm-40gb": Gpu("A100 SXM 40GB", 40.0, 1.555, None, 312.0),  # Lambda gpu_1x_a100_sxm4 and gpu_8x_a100
    "b200-180gb": Gpu("B200 180GB", 178.0, 8.0, 4500.0, 2250.0),
}

MODELS = {
    "gemma-4-31b": Model(
        name="google/gemma-4-31B-it (RedHatAI FP8-Dynamic)",
        weights_gib=30.4,
        overhead_gib=6.0,
        active_params_b=30.1,
        full_layers=10,
        full_kv_heads=4,
        full_head_dim=512,
        full_k_eq_v=True,
        swa_layers=50,
        swa_kv_heads=16,
        swa_head_dim=256,
        swa_window=1024,
    ),
    "qwen3.8-27b": Model(
        name="Qwen/Qwen3.8-27B (FP8)",
        weights_gib=28.0,
        overhead_gib=6.0,
        active_params_b=25.5,
        full_layers=16,
        full_kv_heads=4,
        full_head_dim=256,
        gdn_layers=48,
        gdn_k_heads=16,
        gdn_v_heads=48,
        gdn_k_dim=128,
        gdn_v_dim=128,
        gdn_conv_kernel=4,
    ),
    "qwen3.6-35b-a3b": Model(
        name="Qwen/Qwen3.6-35B-A3B-FP8",
        weights_gib=32.6,
        overhead_gib=5.0,
        active_params_b=3.0,
        full_layers=10,
        full_kv_heads=2,
        full_head_dim=256,
        gdn_layers=30,
        gdn_k_heads=16,
        gdn_v_heads=32,
        gdn_k_dim=128,
        gdn_v_dim=128,
        gdn_conv_kernel=4,
    ),
    # config.json read on 2026-09-27: 52 layers (13 full, 39 sliding, window 2048),
    # 2 KV heads x 128 for both layer types. No FP8 checkpoint exists, so the FP8
    # row assumes vLLM online FP8 (linear layers FP8; embeddings, lm_head, and the
    # 1.8B vision tower stay BF16).
    "muse-glimmer-30b-fp8": Model(
        name="meta-models/Muse-Glimmer-30B (online FP8)",
        weights_gib=32.0,
        overhead_gib=6.0,
        active_params_b=28.0,
        full_layers=13,
        full_kv_heads=2,
        full_head_dim=128,
        swa_layers=39,
        swa_kv_heads=2,
        swa_head_dim=128,
        swa_window=2048,
    ),
    "muse-glimmer-30b-bf16": Model(
        name="meta-models/Muse-Glimmer-30B (BF16)",
        fp8_compute=False,
        weights_gib=55.5,
        overhead_gib=6.0,
        active_params_b=28.0,
        full_layers=13,
        full_kv_heads=2,
        full_head_dim=128,
        swa_layers=39,
        swa_kv_heads=2,
        swa_head_dim=128,
        swa_window=2048,
    ),
    "gemma-4-26b-a4b": Model(
        name="google/gemma-4-26B-A4B-it (FP8)",
        weights_gib=25.6,
        overhead_gib=5.0,
        active_params_b=4.0,
        full_layers=5,
        full_kv_heads=2,
        full_head_dim=512,
        full_k_eq_v=True,
        swa_layers=25,
        swa_kv_heads=8,
        swa_head_dim=256,
        swa_window=1024,
    ),
}

LINKS_GB_S = {"nvlink (NIXL cuda_ipc, eff.)": 100.0, "pcie4 x16 (eff.)": 20.0, "10GbE tcp (eff.)": 1.1}


def kv_budget_gib(gpu: Gpu, model: Model, util: float) -> float:
    return gpu.hbm_gib * util - model.weights_gib - model.overhead_gib


def report(gpu_key: str, util: float, kv_bytes: int, lengths: list[int]) -> str:
    gpu = GPUS[gpu_key]
    out: list[str] = []
    out.append(f"GPU: {gpu.name}, HBM {gpu.hbm_gib} GiB, gpu-memory-utilization {util}, KV dtype bytes {kv_bytes}")
    out.append("")
    out.append("| model | weights GiB | KV budget GiB | full-attn bytes/token "
               "| SWA bytes/token (window) | state MiB/seq |")
    out.append("|---|---|---|---|---|---|")
    for m in MODELS.values():
        swa = f"{m.swa_bytes_per_token(kv_bytes):,} ({m.swa_window})" if m.swa_layers else "0"
        out.append(
            f"| {m.name} | {m.weights_gib:.1f} | {kv_budget_gib(gpu, m, util):.1f} | "
            f"{m.full_bytes_per_token(kv_bytes):,} | {swa} | {m.state_bytes_per_seq() / MIB:.1f} |"
        )
    out.append("")
    header = "| model | " + " | ".join(f"L={n // 1024}K: GiB/seq, max seqs" for n in lengths) + " |"
    out.append(header)
    out.append("|---|" + "---|" * len(lengths))
    for m in MODELS.values():
        budget = kv_budget_gib(gpu, m, util) * GIB
        cells = []
        for n in lengths:
            per = m.per_seq_bytes(n, kv_bytes)
            cells.append(f"{per / GIB:.2f}, {int(budget // per)}")
        out.append(f"| {m.name} | " + " | ".join(cells) + " |")
    out.append("")
    prompt = 8192
    out.append(f"Hop size and transfer time for one {prompt}-token prompt (paper, before prefix-cache savings):")
    out.append("")
    out.append("| model | hop MiB | " + " | ".join(LINKS_GB_S) + " | prefill s (paper) |")
    out.append("|---|---|" + "---|" * len(LINKS_GB_S) + "---|")
    for m in MODELS.values():
        hop = m.per_seq_bytes(prompt, kv_bytes)
        times = [f"{hop / (bw * 1e9) * 1000:.1f} ms" for bw in LINKS_GB_S.values()]
        peak = gpu.dense_tflops_fp8 if (m.fp8_compute and gpu.dense_tflops_fp8) else gpu.dense_tflops_bf16
        tflops = peak * 0.4  # 40% of peak, paper value
        prefill_s = 2 * m.active_params_b * 1e9 * prompt / (tflops * 1e12)
        out.append(f"| {m.name} | {hop / MIB:,.0f} | " + " | ".join(times) + f" | {prefill_s:.2f} |")
    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--gpu", default="h100-sxm-80gb", choices=sorted(GPUS))
    p.add_argument("--util", type=float, default=0.90)
    p.add_argument("--kv-bytes", type=int, default=2, help="2 = BF16 KV, 1 = FP8 KV")
    p.add_argument("--lengths", default="2048,8192,24576,32768,262144")
    args = p.parse_args()
    lengths = [int(x) for x in args.lengths.split(",")]
    print(report(args.gpu, args.util, args.kv_bytes, lengths))


if __name__ == "__main__":
    main()
