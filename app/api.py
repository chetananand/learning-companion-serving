"""The Companion API (ADR-006): the UI and the load generator call it.

POST /v1/turns   one user turn. The response is a stream of server-sent events.
GET  /healthz    the process is alive.
GET  /readyz     Qdrant answers (the data plane is ready).
GET  /metrics    Prometheus metrics.

Run: uvicorn --factory app.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from app.config import AppSettings, get_settings
from app.metrics import AppMetrics
from app.service import Companion, TurnRequest

log = logging.getLogger("companion.api")
MAX_IMAGES = 2
MAX_IMAGE_CHARS = 6 * 1024 * 1024  # a data URL of a 4 MB image (the edge limit), in base64


class TurnBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal["quick", "verified"] = "quick"
    session_id: str | None = Field(default=None, max_length=100)
    tenant: str | None = Field(default=None, max_length=40)
    request_class: Literal["interactive", "batch"] = "interactive"
    allow_overflow: bool | None = None
    images: list[str] = Field(default_factory=list)


def sse(event: dict[str, Any]) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event, default=str)}\n\n"


def check_images(images: list[str]) -> None:
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, f"at most {MAX_IMAGES} images")
    for url in images:
        if not url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
            raise HTTPException(400, "an image must be a PNG or JPEG data URL")
        if len(url) > MAX_IMAGE_CHARS:
            raise HTTPException(413, "an image is too large")


def create_app(companion: Companion, metrics: AppMetrics, *, ready: Any = None,
               lifespan: Any = None) -> FastAPI:
    app = FastAPI(title="Companion API", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        if ready is not None and not await ready():
            raise HTTPException(503, "the data plane is not ready")
        return {"status": "ready"}

    @app.get("/metrics")
    async def prom() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/turns")
    async def turns(body: TurnBody) -> StreamingResponse:
        check_images(body.images)
        req = TurnRequest(question=body.question, mode=body.mode, session_id=body.session_id, tenant=body.tenant,
                          request_class=body.request_class, allow_overflow=body.allow_overflow, images=body.images)

        async def stream() -> AsyncIterator[str]:
            async for event in companion.run(req):
                yield sse(event)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def build_companion(settings: AppSettings) -> tuple[Companion, AppMetrics, Any, Any]:
    """Build the real clients. Imports stay here, so tests of `create_app` need no cluster."""
    import redis.asyncio as aioredis
    from qdrant_client import AsyncQdrantClient

    from app.agent.tools import Toolbox
    from app.clients.browser import BrowserClient
    from app.clients.fetcher import PageFetcher
    from app.clients.pageguard import PageGuard
    from app.clients.sie import SieClient
    from app.clients.websearch import TavilySearch
    from app.llm import TraceRecorder, make_http_client, make_model
    from app.pages import PageReader
    from app.prompts import PROMPT_VERSION
    from app.retrieval import HybridRetriever

    metrics = AppMetrics()
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, timeout=10)
    sie_embed = SieClient(settings.sie_embed_url, timeout_s=settings.sie_timeout_s)
    sie_ocr = SieClient(settings.sie_ocr_url, timeout_s=60.0)
    guard = PageGuard(settings.injection_url, timeout_s=settings.page_check_timeout_s)
    fetcher = PageFetcher(user_agent=settings.user_agent, timeout_s=settings.fetch_timeout_s,
                          max_bytes=settings.fetch_max_bytes, host_interval_s=settings.fetch_domain_interval_s,
                          allow_hosts=frozenset(settings.fetch_allow_hosts))
    reader = PageReader(fetcher, browser=BrowserClient(settings.browser_url), sie_ocr=sie_ocr,
                        ocr_model=settings.ocr_model, guard=guard, ocr_min_chars=settings.ocr_min_chars)
    cache = aioredis.from_url(settings.redis_url, decode_responses=True)
    search = TavilySearch(settings.tavily_api_key, base_url=settings.tavily_url, cache=cache,
                          cache_ttl_s=settings.search_cache_ttl_s)
    toolbox = Toolbox(settings, HybridRetriever(qdrant, sie_embed, settings), search, reader, guard, metrics)
    capture = None
    if settings.capture_path:
        from app.loadgen.capture import Capture

        capture = Capture(settings.capture_path)
    http = make_http_client(settings, capture=capture)
    trace = TraceRecorder(settings.trace_path or None)
    models = {r: make_model(r, settings, http, PROMPT_VERSION) for r in ("quick", "agent", "verify")}
    companion = Companion(settings, toolbox, models, metrics, trace)

    async def ready() -> bool:
        try:
            return await qdrant.collection_exists(settings.collection)
        except Exception:  # noqa: BLE001 - any failure means "not ready"
            return False

    async def close() -> None:
        for client in (sie_embed, sie_ocr, guard, fetcher, search, reader.browser):
            if client is not None:
                await client.aclose()
        await http.aclose()
        await qdrant.close()
        await cache.aclose()

    return companion, metrics, ready, close


def app() -> FastAPI:  # uvicorn --factory app.api:app
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    companion, metrics, ready, close = build_companion(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await close()

    return create_app(companion, metrics, ready=ready, lifespan=lifespan)
