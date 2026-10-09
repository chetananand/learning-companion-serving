"""The warmup routine: HTTP probes straight to a vLLM pod, not through the router (section 6.5)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

FILLER = ("The companion reads bookmarks about inference engines, KV caches, batching, and GPUs. ")


def prompt_of(tokens: int) -> str:
    """A prompt of about `tokens` tokens (about 4 characters for each token)."""
    return (FILLER * (tokens * 4 // len(FILLER) + 1))[: tokens * 4]


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    ttft_s: float | None = None
    detail: str = ""


class PodProbe:
    def __init__(self, http: httpx.AsyncClient, served_model: str, timeout_s: float = 120.0) -> None:
        self.http, self.model, self.timeout = http, served_model, timeout_s

    async def engine_ready(self, base: str) -> bool:
        try:
            health = await self.http.get(f"{base}/health", timeout=5.0)
            models = await self.http.get(f"{base}/v1/models", timeout=5.0)
        except httpx.HTTPError:
            return False
        return health.status_code == 200 and models.status_code == 200

    async def complete(self, base: str, messages: list[dict], max_tokens: int,
                       headers: dict[str, str] | None = None) -> ProbeResult:
        """Stream one completion and measure the time to the first chunk."""
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "stream": True,
                "temperature": 0}
        t0 = time.monotonic()
        try:
            async with self.http.stream("POST", f"{base}/v1/chat/completions", json=body, headers=headers or {},
                                        timeout=self.timeout) as resp:
                if resp.status_code != 200:
                    return ProbeResult(False, detail=f"HTTP {resp.status_code}")
                ttft = None
                async for _ in resp.aiter_bytes():
                    if ttft is None:
                        ttft = time.monotonic() - t0
                return ProbeResult(True, ttft)
        except httpx.HTTPError as exc:
            return ProbeResult(False, detail=type(exc).__name__)

    async def seed_prefixes(self, base: str, system_prompts: Sequence[str]) -> bool:
        """Step 1: one request for each active prompt version, so the cache holds the real prefix."""
        for system in system_prompts:
            r = await self.complete(base, [{"role": "system", "content": system},
                                           {"role": "user", "content": "ok"}], max_tokens=1)
            if not r.ok:
                return False
        return True

    async def shape_probes(self, base: str, sizes: Sequence[int] = (1024, 4096, 8192, 16384)) -> bool:
        """Step 2: run the chunked-prefill and decode-batch code paths."""
        for size in sizes:
            for max_tokens in (1, 64):
                r = await self.complete(base, [{"role": "user", "content": prompt_of(size)}], max_tokens)
                if not r.ok:
                    return False
        return True

    async def split_probe(self, decode_base: str, prefill_host_port: str, header: str) -> ProbeResult:
        """Step 3: one P/D split for a new pair. The first NIXL transfer includes the handshake (E8)."""
        return await self.complete(decode_base, [{"role": "user", "content": prompt_of(4096)}], max_tokens=8,
                                   headers={header: prefill_host_port})

    async def ttft_probe(self, base: str, tokens: int = 4096) -> ProbeResult:
        """Step 4: the 4K probe that decides "warm". A new prompt each time, so no prefix hit."""
        unique = f"probe {time.time_ns()} " + prompt_of(tokens)
        return await self.complete(base, [{"role": "user", "content": unique}], max_tokens=1)
