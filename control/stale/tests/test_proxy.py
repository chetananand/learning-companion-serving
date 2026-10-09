"""Tests of the stale-metrics proxy against a fake vLLM."""

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route

from control.stale.proxy import create_app


def fake_vllm():
    state = {"running": 0}

    async def metrics(request: Request):
        return PlainTextResponse(f"vllm:num_requests_running {state['running']}\n")

    async def chat(request: Request):
        body = await request.json()
        state["running"] = body.get("busy", 0)

        async def gen():
            for word in ("a", "b", "c"):
                yield f"data: {word}\n\n".encode()
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"x-upstream": "vllm"})

    return state, Starlette(routes=[Route("/metrics", metrics), Route("/v1/chat/completions", chat, methods=["POST"])])


def client_for(mode="pass", delay_s=0.05):
    state, upstream = fake_vllm()
    app = create_app("http://vllm", mode=mode, delay_s=delay_s, transport=httpx.ASGITransport(app=upstream))
    return state, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy")


async def test_requests_pass_through_with_streaming():
    _, c = client_for()
    resp = await c.post("/v1/chat/completions", json={"busy": 7})
    assert resp.status_code == 200 and resp.text == "data: a\n\ndata: b\n\ndata: c\n\n"
    assert resp.headers["x-upstream"] == "vllm"
    assert (await c.get("/metrics")).text == "vllm:num_requests_running 7\n"  # pass mode: real metrics


async def test_frozen_shows_the_idle_snapshot_while_the_pod_is_busy():
    state, c = client_for()
    assert (await c.get("/metrics")).text == "vllm:num_requests_running 0\n"  # the idle snapshot
    await c.post("/__stale", json={"mode": "frozen"})
    await c.post("/v1/chat/completions", json={"busy": 12})
    assert state["running"] == 12
    assert (await c.get("/metrics")).text == "vllm:num_requests_running 0\n"  # the router sees "empty"
    info = (await c.get("/__stale")).json()
    assert info["mode"] == "frozen" and info["snapshot_age_s"] is not None


async def test_stall_and_bad_mode():
    _, c = client_for()
    await c.post("/__stale", json={"mode": "stall", "delay_s": 0.05})
    assert (await c.get("/metrics")).status_code == 503
    assert (await c.post("/__stale", json={"mode": "later"})).status_code == 400
