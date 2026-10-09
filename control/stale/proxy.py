"""Stale-metrics proxy for E11 (mix M3): make pod B look idle to the router while it is busy.

It runs as a container in pod B, between the llm-d routing sidecar and vLLM
(sidecar --model-server-port=8300 -> this proxy -> vLLM on 8200). All requests pass through, with
streaming. Only GET /metrics changes, by mode:
  pass    the real metrics of vLLM
  frozen  the snapshot that the proxy took at start, while the pod was idle: the router sees fresh
          data that says "empty" (the class7 T3 case: a smart router herds onto a busy pod)
  stall   no answer for `delay_s`: the router's last sample ages, and the router's staleness
          policy decides (ours: stale counts as saturated, "unknown is not idle")
Admin: GET /__stale shows the mode. POST /__stale {"mode": "frozen", "delay_s": 10} sets it.

Run: python -m control.stale.proxy   (env STALE_LISTEN_PORT, STALE_UPSTREAM, STALE_MODE)
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}
MODES = ("pass", "frozen", "stall")


def create_app(upstream: str, *, mode: str = "pass", delay_s: float = 10.0,
               transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    state: dict[str, Any] = {"mode": mode, "delay_s": delay_s, "snapshot": None, "snapshot_ts": None}
    client = httpx.AsyncClient(base_url=upstream.rstrip("/"), timeout=httpx.Timeout(600.0, connect=5.0),
                               transport=transport)

    async def take_snapshot() -> bool:
        try:
            resp = await client.get("/metrics")
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        state["snapshot"], state["snapshot_ts"] = resp.content, time.time()
        return True

    async def admin(request: Request) -> Response:
        if request.method == "POST":
            body = await request.json()
            if body.get("mode") not in MODES:
                return JSONResponse({"error": f"mode must be one of {MODES}"}, status_code=400)
            if body.get("retake_snapshot"):
                await take_snapshot()
            state["mode"] = body["mode"]
            state["delay_s"] = float(body.get("delay_s", state["delay_s"]))
        age = round(time.time() - state["snapshot_ts"], 1) if state["snapshot_ts"] else None
        return JSONResponse({"mode": state["mode"], "delay_s": state["delay_s"], "snapshot_age_s": age})

    async def metrics(request: Request) -> Response:
        if state["snapshot"] is None:
            await take_snapshot()  # the first scrape comes while the pod is still idle
        if state["mode"] == "frozen" and state["snapshot"] is not None:
            return Response(state["snapshot"], media_type="text/plain; version=0.0.4")
        if state["mode"] == "stall":
            await asyncio.sleep(state["delay_s"])
            return Response("stalled", status_code=503)
        return await forward(request)

    async def forward(request: Request) -> Response:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        req = client.build_request(request.method, request.url.path, params=request.query_params,
                                   headers=headers, content=await request.body())
        resp = await client.send(req, stream=True)
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
        return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out_headers,
                                 background=BackgroundTask(resp.aclose))

    methods = ["GET", "POST", "PUT", "DELETE", "PATCH"]
    return Starlette(routes=[Route("/__stale", admin, methods=["GET", "POST"]),
                             Route("/metrics", metrics, methods=["GET"]),
                             Route("/{path:path}", forward, methods=methods)])


def main() -> None:  # pragma: no cover
    import uvicorn

    app = create_app(os.environ.get("STALE_UPSTREAM", "http://127.0.0.1:8200"),
                     mode=os.environ.get("STALE_MODE", "pass"))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("STALE_LISTEN_PORT", "8300")))


if __name__ == "__main__":  # pragma: no cover
    main()
