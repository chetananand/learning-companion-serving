"""`edge`: guard stage 1, the call to `guard`, `slice_oom`, stay or leave, streaming pass-through.

It does not admit, place, or queue. The Agent Router and the llm-d router do that
(system design sections 4 to 6).
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from control.edge.admit import Outcome, classify_upstream, slice_oom
from control.edge.config import EdgeSettings
from control.edge.guard import GuardClient, inspect, last_user_content
from control.edge.metrics import EdgeMetrics
from control.edge.overflow import OverflowLimiter, should_leave
from control.edge.tokens import make_counter


@dataclass
class AccessRecord:
    ts: float
    request_id: str
    tenant: str = ""
    request_class: str = ""
    step: str = ""
    status: int = 0
    reason: str = "ok"
    stage: str = ""
    route: str = "local"
    prompt_tokens: int = 0
    max_tokens: int = 0
    guard_cached: bool = False
    guard_ms: float = 0.0
    ttft_ms: float | None = None
    duration_ms: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AccessLog:
    def __init__(self, path: str) -> None:
        self.fh = open(path, "a", buffering=1) if path else sys.stdout  # noqa: SIM115

    def write(self, record: AccessRecord) -> None:
        self.fh.write(json.dumps(asdict(record), separators=(",", ":")) + "\n")


def error_response(status: int, reason: str, message: str, request_id: str,
                   retry_after: int | None = None) -> JSONResponse:
    headers = {"x-request-id": request_id}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    body = {"error": {"message": message or reason, "type": reason, "code": status}}
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(settings: EdgeSettings | None = None, *, http: httpx.AsyncClient | None = None,
               redis: Any = None, count_text: Callable[[str], int] | None = None,
               metrics: EdgeMetrics | None = None, access_log: AccessLog | None = None) -> FastAPI:
    s = settings or EdgeSettings()
    m = metrics or EdgeMetrics()
    log = access_log or AccessLog(s.access_log_path)
    counter = count_text or make_counter()
    state: dict[str, Any] = {"http": http, "redis": redis}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if state["http"] is None:
            state["http"] = httpx.AsyncClient(timeout=httpx.Timeout(s.upstream_timeout_s, connect=5.0))
        if state["redis"] is None:
            import redis.asyncio as aioredis

            state["redis"] = aioredis.from_url(s.redis_url, decode_responses=True)
        yield
        if http is None:
            await state["http"].aclose()

    app = FastAPI(title="edge", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(generate_latest(m.registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Response:
        t0 = time.monotonic()
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        rec = AccessRecord(ts=time.time(), request_id=rid, step=request.headers.get("x-step", ""))

        def finish(status: int, reason: str, stage: str, message: str = "",
                   retry_after: int | None = None) -> JSONResponse:
            rec.status, rec.reason, rec.stage = status, reason, stage
            rec.duration_ms = round((time.monotonic() - t0) * 1000, 1)
            m.sheds.labels(reason, str(status), stage).inc()
            log.write(rec)
            return error_response(status, reason, message, rid, retry_after)

        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return finish(400, "bad_json", "guard")

        # Guard stage 1: the rules on the CPU. A reject here reaches no GPU.
        g = inspect(payload, headers=request.headers, count_text=counter, settings=s)
        rec.tenant, rec.request_class, rec.prompt_tokens = g.tenant, g.request_class, g.prompt_tokens
        if not g.ok:
            m.guard_rejects.labels("1", g.reason).inc()
            return finish(g.status, g.reason, "guard", g.message)
        m.requests.labels(g.tenant, g.request_class, rec.step or "unknown").inc()
        body = g.payload
        rec.max_tokens = body["max_tokens"]

        oom = slice_oom(g.prompt_tokens, body["max_tokens"], s.max_model_len)
        if oom:
            return finish(oom.status, oom.reason, oom.stage, "prompt plus max_tokens is above max_model_len")

        # Guard stage 2: NeMo Guardrails on node 2 GPU 1. Only the new user content. Fails closed.
        text, images = last_user_content(body)
        tg = time.monotonic()
        verdict = await GuardClient(state["http"], state["redis"], s).check(text, images)
        rec.guard_ms = round((time.monotonic() - tg) * 1000, 1)
        rec.guard_cached = verdict.cached
        m.guard_seconds.labels("2", str(verdict.cached).lower()).observe(time.monotonic() - tg)
        if not verdict.ok:
            m.guard_rejects.labels("2", verdict.reason).inc()
            return finish(verdict.status, verdict.reason, "guard", verdict.rail)

        body = dict(body, model=s.served_model,
                    priority=s.priority_interactive if g.request_class == "interactive" else s.priority_batch)
        up_headers = {
            "content-type": "application/json",
            "x-request-id": rid,
            "x-tenant-id": g.tenant,
            "x-step": request.headers.get("x-step", "unknown"),  # for the Envoy access log and the hop records
            s.header_fairness: g.tenant,
            s.header_objective: g.request_class,
        }
        if request.headers.get("x-session-id"):
            up_headers[s.header_session] = request.headers["x-session-id"]
        deadline_ms = request.headers.get("x-deadline-ms", "")
        remaining = int(deadline_ms) / 1000 - (time.monotonic() - t0) if deadline_ms.isdigit() else None
        if remaining is not None and remaining > 0:
            # Class7 rule: a queue wait above half of the timeout sheds. The router enforces it as a TTL.
            up_headers[s.header_ttl] = f"{int(remaining * 500)}ms"

        up_req = state["http"].build_request("POST", f"{s.upstream_url}/v1/chat/completions",
                                             json=body, headers=up_headers)
        try:
            resp = await state["http"].send(up_req, stream=True)
        except httpx.HTTPError:
            outcome = Outcome(503, "upstream_unreachable", may_leave=True, retry_after_s=2, stage="edge")
        else:
            outcome = classify_upstream(resp.status_code, resp.headers, s.header_dropped_reason)
            if resp.headers.get(s.header_dropped_reason):
                rec.extra["router_reason"] = resp.headers[s.header_dropped_reason]
            if resp.status_code < 400:
                return stream_back(resp, "local", g.request_class, t0, rec, extra_headers={})
            await resp.aread()
            await resp.aclose()

        decision = should_leave(outcome, request_class=g.request_class,
                                allow_overflow=request.headers.get("x-allow-overflow", "false").lower() == "true",
                                has_image=bool(images) or g.images > 0, remaining_deadline_s=remaining, settings=s)
        if decision.leave and not s.overflow_api_key:
            # No overflow provider: the handout makes a hosted API "overflow at most". The gate still decides,
            # and the access log keeps the decision, so E13 proves the rule with no live call.
            m.overflow_refused.labels("no_provider").inc()
            rec.extra["would_leave"] = outcome.reason
        elif decision.leave:
            limiter = OverflowLimiter(state["redis"], s)
            why = await limiter.acquire(g.prompt_tokens + body["max_tokens"])
            if why is None:
                m.overflow.labels(s.overflow_model, outcome.reason).inc()
                rec.extra["local_reason"] = outcome.reason
                return await send_overflow(body, g.request_class, t0, rec, outcome, limiter)
            m.overflow_refused.labels(why).inc()
            rec.extra["overflow_refused"] = why
        else:
            rec.extra["stay"] = decision.why
        return finish(outcome.status, outcome.reason, outcome.stage, retry_after=outcome.retry_after_s)

    def stream_back(resp: httpx.Response, route: str, request_class: str, t0: float, rec: AccessRecord,
                    extra_headers: dict[str, str], on_close: Callable[[], Any] | None = None) -> Response:
        rec.route, rec.status = route, resp.status_code
        headers = {"x-request-id": rec.request_id, "x-companion-via": route, **extra_headers}
        media_type = resp.headers.get("content-type", "application/json")

        async def body_iter() -> AsyncIterator[bytes]:
            first = True
            try:
                async for chunk in resp.aiter_bytes():
                    if first:
                        first = False
                        ttft = time.monotonic() - t0
                        rec.ttft_ms = round(ttft * 1000, 1)
                        m.ttft.labels(request_class, route).observe(ttft)
                    yield chunk
            finally:
                await resp.aclose()  # a client disconnect closes the upstream stream, and vLLM aborts
                if on_close:
                    await on_close()
                duration = time.monotonic() - t0
                rec.duration_ms = round(duration * 1000, 1)
                m.duration.labels(request_class, route).observe(duration)
                log.write(rec)

        return StreamingResponse(body_iter(), status_code=resp.status_code, media_type=media_type, headers=headers)

    async def send_overflow(body: dict[str, Any], request_class: str, t0: float, rec: AccessRecord,
                            outcome: Outcome, limiter: OverflowLimiter) -> Response:
        ovf_body = dict(body, model=s.overflow_model)
        ovf_body.pop("priority", None)
        req = state["http"].build_request(
            "POST", f"{s.overflow_url}/chat/completions", json=ovf_body,
            headers={"authorization": f"Bearer {s.overflow_api_key}", "content-type": "application/json"})
        async def local_error(why: str) -> Response:
            """The overflow failed: the client gets the local outcome, never the error of the provider."""
            await limiter.release()
            rec.extra["overflow_error"] = why
            m.sheds.labels(outcome.reason, str(outcome.status), outcome.stage).inc()
            rec.status, rec.reason, rec.stage = outcome.status, outcome.reason, outcome.stage
            log.write(rec)
            return error_response(outcome.status, outcome.reason, f"overflow {why}", rec.request_id,
                                  outcome.retry_after_s)

        try:
            resp = await state["http"].send(req, stream=True)
        except httpx.HTTPError:
            return await local_error("unreachable")
        if resp.status_code >= 400:
            await resp.aread()
            await resp.aclose()
            return await local_error(f"status_{resp.status_code}")
        return stream_back(resp, "overflow", request_class, t0, rec,
                           extra_headers={"x-companion-overflow-model": s.overflow_model,
                                          "x-companion-local-reason": outcome.reason},
                           on_close=limiter.release)

    return app


def app() -> FastAPI:  # uvicorn --factory control.edge.server:app
    return create_app()
