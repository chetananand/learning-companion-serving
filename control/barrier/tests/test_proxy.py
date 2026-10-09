"""Tests of the store barrier against a fake vLLM and a fake LMCache metrics endpoint."""

import time

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

from control.barrier.proxy import create_app, is_prefill_leg, store_counts


class FakeLMCache:
    """Each answer of the fake vLLM submits one store. The store finishes `finish_s` later."""

    def __init__(self, finish_s: float = 0.05, up: bool = True) -> None:
        self.finish_s, self.up = finish_s, up
        self.stores: list[float] = []  # the submit times

    def submit(self) -> None:
        self.stores.append(time.monotonic())

    def app(self) -> Starlette:
        async def metrics(request: Request):
            if not self.up:
                return PlainTextResponse("down", status_code=503)
            now = time.monotonic()
            done = sum(1 for t in self.stores if now - t >= self.finish_s)
            return PlainTextResponse(
                f'lmcache_mp_num_submitted_stores_total{{device="cuda:0"}} {len(self.stores)}\n'
                f'lmcache_mp_num_finished_stores_total{{device="cuda:0"}} {done}\n'
                'lmcache_mp_num_submitted_stores_created{device="cuda:0"} 1.7e9\n')
        return Starlette(routes=[Route("/metrics", metrics)])


def fake_vllm(lm: FakeLMCache) -> Starlette:
    async def chat(request: Request):
        body = await request.json()
        lm.submit()
        if body.get("stream"):
            async def gen():
                for word in ("a", "b", "c"):
                    yield f"data: {word}\n\n".encode()
            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse({"choices": [{"message": {"content": "x"}}]}, headers={"x-upstream": "vllm"})

    async def metrics(request: Request):
        return PlainTextResponse("vllm:num_requests_running 0\n")

    return Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]), Route("/metrics", metrics)])


def client_for(lm: FakeLMCache, url: str = "http://lmcache:8080/metrics", **kw) -> httpx.AsyncClient:
    app = create_app("http://vllm", url, transport=httpx.ASGITransport(app=fake_vllm(lm)),
                     lmcache_transport=httpx.ASGITransport(app=lm.app()), **kw)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://barrier")


LEG = {"model": "companion", "max_tokens": 1, "stream": False, "messages": [{"role": "user", "content": "hi"}]}


def test_the_prefill_leg_rule():
    assert is_prefill_leg("/v1/chat/completions", b'{"max_tokens": 1}')
    assert is_prefill_leg("/v1/completions", b'{"max_completion_tokens": 1, "stream": false}')
    assert not is_prefill_leg("/v1/chat/completions", b'{"max_tokens": 1, "stream": true}')
    assert not is_prefill_leg("/v1/chat/completions", b'{"max_tokens": 64}')
    assert not is_prefill_leg("/metrics", b"")
    assert not is_prefill_leg("/v1/chat/completions", b"not json")


def test_store_counts_sum_the_devices_and_skip_created():
    text = ('lmcache_mp_num_submitted_stores_total{device="cuda:0"} 37\n'
            'lmcache_mp_num_submitted_stores_total{device="cuda:1"} 1978\n'
            'lmcache_mp_num_finished_stores_total{device="cuda:0"} 37\n'
            'lmcache_mp_num_finished_stores_total{device="cuda:1"} 1977\n'
            'lmcache_mp_num_submitted_stores_created{device="cuda:0"} 1.7e9\n')
    assert store_counts(text) == (2015.0, 2014.0)


async def test_the_prefill_leg_waits_for_the_floor_and_the_store():
    lm = FakeLMCache(finish_s=0.15)
    c = client_for(lm, min_s=0.05, max_s=1.0, poll_s=0.005)
    t = time.monotonic()
    resp = await c.post("/v1/chat/completions", json=LEG)
    held = time.monotonic() - t
    assert resp.status_code == 200 and resp.json()["choices"][0]["message"]["content"] == "x"
    assert resp.headers["x-barrier-outcome"] == "done" and resp.headers["x-upstream"] == "vllm"
    assert held >= 0.15  # the store finished before the answer came back
    text = (await c.get("/barrier/metrics")).text
    assert "barrier_hold_seconds_count 1" in text and "barrier_timeouts_total 0" in text


async def test_the_floor_holds_even_when_the_counters_say_done():
    lm = FakeLMCache(finish_s=0.0)
    c = client_for(lm, min_s=0.08, max_s=1.0)
    t = time.monotonic()
    resp = await c.post("/v1/chat/completions", json=LEG)
    assert resp.headers["x-barrier-outcome"] == "done" and time.monotonic() - t >= 0.08


async def test_other_requests_stream_through_without_a_hold():
    lm = FakeLMCache(finish_s=5.0)
    c = client_for(lm, min_s=0.5)
    t = time.monotonic()
    resp = await c.post("/v1/chat/completions", json={**LEG, "stream": True, "max_tokens": 64})
    assert resp.text == "data: a\n\ndata: b\n\ndata: c\n\n" and "x-barrier-outcome" not in resp.headers
    assert time.monotonic() - t < 0.4
    assert (await c.get("/metrics")).text == "vllm:num_requests_running 0\n"  # the router reads vLLM


async def test_a_slow_store_ends_at_max_s():
    lm = FakeLMCache(finish_s=10.0)
    c = client_for(lm, min_s=0.01, max_s=0.1)
    resp = await c.post("/v1/chat/completions", json=LEG)
    assert resp.status_code == 200 and resp.headers["x-barrier-outcome"] == "timeout"
    assert "barrier_timeouts_total 1" in (await c.get("/barrier/metrics")).text


async def test_no_lmcache_answer_means_no_hold():
    lm = FakeLMCache(up=False)
    c = client_for(lm, min_s=0.01, max_s=0.5)
    resp = await c.post("/v1/chat/completions", json=LEG)
    assert resp.status_code == 200 and resp.headers["x-barrier-outcome"] == "fail_open"
    assert "barrier_fail_open_total 1" in (await c.get("/barrier/metrics")).text


async def test_no_lmcache_url_turns_the_barrier_off():
    lm = FakeLMCache(finish_s=10.0)
    c = client_for(lm, url="", min_s=0.5)
    t = time.monotonic()
    resp = await c.post("/v1/chat/completions", json=LEG)
    assert resp.status_code == 200 and "x-barrier-outcome" not in resp.headers and time.monotonic() - t < 0.4
