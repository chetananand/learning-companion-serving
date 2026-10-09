"""The browser service: headless Chromium screenshots for the OCR stage (FR-08, J3).

POST /v1/screenshot {"url", "near_text"?, "full_page"?} -> image/png

Safety (product spec, section 6):
- Only http and https. No private, loopback, or link-local address, also for the subresources
  of the page (a route handler stops them). BROWSER_ALLOW_HOSTS opens the in-cluster test host.
- A new browser context for each request: no cookies, no storage, no downloads, no service workers.
- The service never clicks, types, or submits a form.

Run: uvicorn --factory app.browser.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from app.clients.fetcher import _blocked_ip

MAX_HEIGHT = 6000  # px, a limit for the OCR cost
ABOVE, BELOW = 600, 1000  # px around the element that holds `near_text`
SKIP_TYPES = {"media", "font", "websocket", "eventsource", "manifest"}


class ShotRequest(BaseModel):
    url: str = Field(max_length=2000)
    near_text: str | None = Field(default=None, max_length=200)
    full_page: bool = False
    width: int = Field(default=1280, ge=320, le=1920)
    height: int = Field(default=1600, ge=320, le=4000)


@lru_cache(maxsize=2048)
def _host_blocked(host: str) -> bool:
    try:
        return _blocked_ip(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return True
    return any(_blocked_ip(i[4][0]) for i in infos)


def url_allowed(url: str, allow_hosts: frozenset[str]) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return parts.scheme in ("data", "blob")  # inline content of the page itself
    host = (parts.hostname or "").lower()
    return bool(host) and (host in allow_hosts or not _host_blocked(host))


def clip_for(box: dict[str, float], page_height: float, width: int) -> dict[str, float]:
    """The region around the element: the figure is usually above or below its caption."""
    top = max(0.0, box["y"] - ABOVE)
    bottom = min(page_height, box["y"] + box["height"] + BELOW)
    return {"x": 0, "y": top, "width": width, "height": max(1.0, min(bottom - top, MAX_HEIGHT))}


def create_app(browser_factory: Any = None, *, allow_hosts: frozenset[str] = frozenset(),
               max_pages: int = 3, user_agent: str = "LearningCompanionBot/0.1 (private research agent)") -> FastAPI:
    registry = CollectorRegistry()
    shots = Counter("browser_screenshots_total", "Screenshots", ["outcome"], registry=registry)
    seconds = Histogram("browser_screenshot_seconds", "Screenshot latency", registry=registry,
                        buckets=(0.5, 1, 2, 4, 8, 15, 30))
    state: dict[str, Any] = {}
    sem = asyncio.Semaphore(max_pages)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if browser_factory is not None:
            state["browser"], state["close"] = await browser_factory()
        yield
        if "close" in state:
            await state["close"]()

    app = FastAPI(title="browser", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        if "browser" not in state:
            raise HTTPException(503, "no browser")
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/screenshot")
    async def screenshot(req: ShotRequest) -> Response:
        if not url_allowed(req.url, allow_hosts) or urlsplit(req.url).scheme not in ("http", "https"):
            shots.labels("blocked").inc()
            raise HTTPException(400, "this URL is not allowed")
        if "browser" not in state:
            raise HTTPException(503, "no browser")
        t0 = time.monotonic()
        async with sem:
            try:
                png = await take(state["browser"], req, allow_hosts, user_agent)
            except TimeoutError as exc:
                shots.labels("timeout").inc()
                raise HTTPException(504, "the page took too long") from exc
            except Exception as exc:
                shots.labels("error").inc()
                raise HTTPException(502, f"screenshot failed: {type(exc).__name__}") from exc
        seconds.observe(time.monotonic() - t0)
        shots.labels("ok").inc()
        return Response(png, media_type="image/png")

    return app


async def take(browser: Any, req: ShotRequest, allow_hosts: frozenset[str], user_agent: str) -> bytes:
    context = await browser.new_context(viewport={"width": req.width, "height": req.height}, user_agent=user_agent,
                                        accept_downloads=False, service_workers="block", java_script_enabled=True)
    try:
        async def guard(route: Any) -> None:
            request = route.request
            if request.method != "GET" or request.resource_type in SKIP_TYPES or \
                    not url_allowed(request.url, allow_hosts):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", guard)
        page = await context.new_page()
        async with asyncio.timeout(25):
            await page.goto(req.url, wait_until="domcontentloaded", timeout=15000)
            with contextlib.suppress(Exception):  # some pages never go idle; the screenshot still works
                await page.wait_for_load_state("networkidle", timeout=3000)
            height = float(await page.evaluate("document.documentElement.scrollHeight"))
            if req.near_text:
                target = page.get_by_text(req.near_text, exact=False).first
                if await target.count():
                    await target.scroll_into_view_if_needed(timeout=3000)
                    box = await target.bounding_box()
                    if box is not None:
                        top = float(await page.evaluate("window.scrollY"))
                        box = {**box, "y": box["y"] + top}
                        return await page.screenshot(clip=clip_for(box, height, req.width), full_page=True)
            if req.full_page:
                clip = {"x": 0, "y": 0, "width": req.width, "height": min(height, MAX_HEIGHT)}
                return await page.screenshot(clip=clip, full_page=True)
            return await page.screenshot()
    finally:
        await context.close()


async def chromium() -> tuple[Any, Any]:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)

    async def close() -> None:
        await browser.close()
        await pw.stop()

    return browser, close


def app() -> FastAPI:  # uvicorn --factory app.browser.server:app
    hosts = frozenset(h.strip().lower() for h in os.environ.get("BROWSER_ALLOW_HOSTS", "").split(",") if h.strip())
    return create_app(chromium, allow_hosts=hosts, max_pages=int(os.environ.get("BROWSER_MAX_PAGES", "3")))
