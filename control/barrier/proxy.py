"""Store barrier for the P/D hop through the LMCache tier (session 1, 2026-09-29).

The decode pod loads the prompt KV from the shared LMCache tier (routing sidecar mode shared-storage).
The prefill engine answers before LMCache has stored that KV. So a decode lookup right after the answer
misses, and the decode pod computes the whole prompt again. Session 1 on 2 x H100, 8,500 tokens, during the
capture load: a gap of 10 or 20 ms gave 0 hits in 10 tries, 50 ms gave 1 hit in 5, and 100 ms gave 5 in 5
(8,448 tokens loaded). The store counters of the server show the store a few ms after the answer, but the
lookup sees the KV later. So the hold has a floor (min_s, 0.1 s).

This proxy runs in the prefill pod, in front of vLLM (port 8000 -> vLLM on 8200). Every request passes
through, with streaming. It holds one kind of answer: the prefill leg of the sidecar (not streaming,
max_tokens 1). After that answer, it waits min_s. Then it waits until the LMCache server has finished every
store that the server had received by then (the counters lmcache_mp_num_submitted_stores_total and
lmcache_mp_num_finished_stores_total). Then it returns the answer, and the decode lookup hits.

Fail open: no LMCache URL, or no answer from it, gives no hold. A hold ends after max_s at most.
GET /barrier/metrics gives the hold counters (Prometheus text). GET /barrier/health is the probe.

Run: python -m control.barrier.proxy   (env BARRIER_LISTEN_PORT, BARRIER_UPSTREAM, BARRIER_LMCACHE_METRICS,
     BARRIER_MIN_S, BARRIER_MAX_S, BARRIER_POLL_S)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}
COMPLETIONS = ("/v1/chat/completions", "/v1/completions")
COUNTER = re.compile(r"^(lmcache_mp_num_(?:submitted|finished)_stores_total)(?:\{[^}]*\})?\s+([0-9.eE+-]+)")
BUCKETS = (0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0)


def is_prefill_leg(path: str, body: bytes) -> bool:
    """The sidecar's prefill leg: a completion that is not streaming and asks for one token."""
    if path not in COMPLETIONS or not body:
        return False
    try:
        req = json.loads(body)
    except ValueError:
        return False
    if not isinstance(req, dict) or req.get("stream"):
        return False
    return 1 in (req.get("max_tokens"), req.get("max_completion_tokens"))


def store_counts(text: str) -> tuple[float, float]:
    """Sum the submitted and finished store counters over all devices."""
    total = {"lmcache_mp_num_submitted_stores_total": 0.0, "lmcache_mp_num_finished_stores_total": 0.0}
    for line in text.splitlines():
        m = COUNTER.match(line)
        if m:
            total[m.group(1)] += float(m.group(2))
    return total["lmcache_mp_num_submitted_stores_total"], total["lmcache_mp_num_finished_stores_total"]


@dataclass
class Stats:
    holds: int = 0
    timeouts: int = 0
    fail_open: int = 0
    passed: int = 0
    hold_sum_s: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * len(BUCKETS))

    def observe(self, hold_s: float) -> None:
        self.holds += 1
        self.hold_sum_s += hold_s
        for i, le in enumerate(BUCKETS):
            if hold_s <= le:
                self.buckets[i] += 1

    def text(self) -> str:
        out = ["# TYPE barrier_hold_seconds histogram"]
        out += [f'barrier_hold_seconds_bucket{{le="{le}"}} {n}' for le, n in zip(BUCKETS, self.buckets, strict=True)]
        out += [f'barrier_hold_seconds_bucket{{le="+Inf"}} {self.holds}',
                f"barrier_hold_seconds_sum {self.hold_sum_s:.6f}", f"barrier_hold_seconds_count {self.holds}",
                "# TYPE barrier_timeouts_total counter", f"barrier_timeouts_total {self.timeouts}",
                "# TYPE barrier_fail_open_total counter", f"barrier_fail_open_total {self.fail_open}",
                "# TYPE barrier_passed_total counter", f"barrier_passed_total {self.passed}"]
        return "\n".join(out) + "\n"


class Barrier:
    """Wait until LMCache has finished the stores that it had received a short time after an answer."""

    def __init__(self, metrics_url: str, client: httpx.AsyncClient, *, min_s: float, max_s: float,
                 poll_s: float) -> None:
        self.url, self.client = metrics_url, client
        self.min_s, self.max_s, self.poll_s = min_s, max_s, poll_s

    async def counts(self) -> tuple[float, float] | None:
        try:
            resp = await self.client.get(self.url, timeout=max(self.max_s, 0.2))
        except httpx.HTTPError:
            return None
        return store_counts(resp.text) if resp.status_code == 200 else None

    async def wait(self) -> tuple[float, str]:
        """Return (hold seconds, outcome): outcome is done, timeout, or fail_open."""
        start = time.monotonic()
        if await self.counts() is None:  # no LMCache (K0): no hold at all
            return time.monotonic() - start, "fail_open"
        await asyncio.sleep(self.min_s)  # the floor: the lookup sees a new store later than the counters
        first = await self.counts()
        if first is None:
            return time.monotonic() - start, "fail_open"
        target, finished = first
        while finished < target:
            if time.monotonic() - start >= self.max_s:
                return time.monotonic() - start, "timeout"
            await asyncio.sleep(self.poll_s)
            now = await self.counts()
            if now is None:
                return time.monotonic() - start, "fail_open"
            finished = now[1]
        return time.monotonic() - start, "done"


def create_app(upstream: str, lmcache_metrics: str = "", *, min_s: float = 0.1, max_s: float = 0.5,
               poll_s: float = 0.005, transport: httpx.AsyncBaseTransport | None = None,
               lmcache_transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    client = httpx.AsyncClient(base_url=upstream.rstrip("/"), timeout=httpx.Timeout(600.0, connect=5.0),
                               transport=transport)
    barrier = (Barrier(lmcache_metrics, httpx.AsyncClient(transport=lmcache_transport), min_s=min_s,
                       max_s=max_s, poll_s=poll_s) if lmcache_metrics else None)
    stats = Stats()

    async def forward(request: Request) -> Response:
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        req = client.build_request(request.method, request.url.path, params=request.query_params,
                                   headers=headers, content=body)
        if barrier is None or not is_prefill_leg(request.url.path, body):
            stats.passed += 1
            resp = await client.send(req, stream=True)
            out = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
            return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out,
                                     background=BackgroundTask(resp.aclose))
        resp = await client.send(req)
        out = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
        if resp.status_code == 200:
            hold_s, outcome = await barrier.wait()
            if outcome == "fail_open":
                stats.fail_open += 1
            else:
                stats.observe(hold_s)
                stats.timeouts += outcome == "timeout"
            out["x-barrier-hold-ms"] = f"{hold_s * 1000:.1f}"
            out["x-barrier-outcome"] = outcome
        return Response(resp.content, status_code=resp.status_code, headers=out)

    async def own_metrics(request: Request) -> Response:
        return PlainTextResponse(stats.text(), media_type="text/plain; version=0.0.4")

    async def health(request: Request) -> Response:
        return PlainTextResponse("ok")

    methods = ["GET", "POST", "PUT", "DELETE", "PATCH"]
    return Starlette(routes=[Route("/barrier/metrics", own_metrics, methods=["GET"]),
                             Route("/barrier/health", health, methods=["GET"]),
                             Route("/{path:path}", forward, methods=methods)])


def main() -> None:  # pragma: no cover
    import uvicorn

    env = os.environ.get
    app = create_app(env("BARRIER_UPSTREAM", "http://127.0.0.1:8200"), env("BARRIER_LMCACHE_METRICS", ""),
                     min_s=float(env("BARRIER_MIN_S", "0.1")), max_s=float(env("BARRIER_MAX_S", "0.5")),
                     poll_s=float(env("BARRIER_POLL_S", "0.005")))
    uvicorn.run(app, host="0.0.0.0", port=int(env("BARRIER_LISTEN_PORT", "8000")), log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
