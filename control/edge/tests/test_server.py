"""The full `edge` request path against mock upstreams (guard, Agent Router, overflow)."""

import json

import fakeredis
import httpx
import pytest

from control.edge.config import EdgeSettings
from control.edge.server import create_app
from control.edge.tokens import estimate_tokens

S = EdgeSettings(upstream_url="http://router", guard_url="http://guard", injection_url="http://injection",
                 overflow_url="http://overflow/v1", overflow_api_key="test-key")
H = {"X-Tenant-Id": "owner", "X-Request-Class": "interactive", "X-Session-Id": "s-1", "X-Step": "draft",
     "X-Allow-Overflow": "true", "X-Deadline-Ms": "30000"}
BODY = {"model": "companion", "messages": [{"role": "user", "content": "What is continuous batching?"}]}
OK_COMPLETION = {"id": "c1", "object": "chat.completion",
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": "It batches steps."}}]}


class ListLog:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class Upstreams:
    """One mock transport for the guard, the router, and the overflow."""

    def __init__(self):
        self.guard_status = "passed"
        self.guard_rail = ""
        self.guard_error = False
        self.injection_malicious = False
        self.injection_error = False
        self.router = lambda req: httpx.Response(200, json=OK_COMPLETION)
        self.overflow_status = 200
        self.calls = {"guard": 0, "injection": 0, "router": 0, "overflow": 0}
        self.last_router_request = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "guard":
            self.calls["guard"] += 1
            if self.guard_error:
                raise httpx.ConnectTimeout("guard down")
            body = json.loads(request.content)
            assert body["guardrails"]["rail_types"] == ["input"] and body["messages"][0]["role"] == "user"
            return httpx.Response(200, json={"status": self.guard_status, "content": "", "rail": self.guard_rail})
        if request.url.host == "injection":
            self.calls["injection"] += 1
            if self.injection_error:
                raise httpx.ConnectTimeout("injection classifier down")
            score = 0.98 if self.injection_malicious else 0.01
            return httpx.Response(200, json={"results": [{"malicious": self.injection_malicious, "score": score}]})
        if request.url.host == "router":
            self.calls["router"] += 1
            self.last_router_request = request
            return self.router(request)
        if request.url.host == "overflow":
            self.calls["overflow"] += 1
            assert request.headers["authorization"] == "Bearer test-key"
            assert json.loads(request.content)["model"] == S.overflow_model
            if self.overflow_status != 200:
                return httpx.Response(self.overflow_status, json={"error": {"message": "provider error"}})
            return httpx.Response(200, json=OK_COMPLETION | {"id": "ovf"})
        raise AssertionError(f"unexpected host {request.url.host}")


@pytest.fixture
def env():
    up = Upstreams()
    log = ListLog()
    app = create_app(S, http=httpx.AsyncClient(transport=httpx.MockTransport(up)),
                     redis=fakeredis.FakeAsyncRedis(decode_responses=True), count_text=estimate_tokens,
                     access_log=log)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://edge")
    return up, log, client


async def test_stage1_reject_reaches_nothing(env):
    up, log, client = env
    r = await client.post("/v1/chat/completions", json=BODY | {"model": "gpt-x"}, headers=H)
    assert r.status_code == 400 and r.json()["error"]["type"] == "model_not_allowed"
    assert up.calls == {"guard": 0, "injection": 0, "router": 0, "overflow": 0}
    assert log.records[-1].stage == "guard"


async def test_stage2_injection_is_blocked_before_the_router(env):
    up, _, client = env
    up.injection_malicious = True
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 400 and r.json()["error"]["type"] == "prompt_injection"
    assert up.calls["router"] == 0


async def test_stage2_unsafe_content_is_blocked(env):
    up, _, client = env
    up.guard_status, up.guard_rail = "blocked", "content safety check input"
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 400 and r.json()["error"]["type"] == "unsafe_content"
    assert up.calls["router"] == 0


async def test_injection_classifier_down_fails_closed(env):
    up, _, client = env
    up.injection_error = True
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 503 and r.json()["error"]["type"] == "guard_unavailable"
    assert up.calls["router"] == 0 and up.calls["overflow"] == 0


async def test_guard_down_fails_closed_and_never_leaves(env):
    up, _, client = env
    up.guard_error = True
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 503 and r.json()["error"]["type"] == "guard_unavailable"
    assert up.calls["router"] == 0 and up.calls["overflow"] == 0


async def test_happy_path_sets_router_headers_and_priority(env):
    up, log, client = env
    r = await client.post("/v1/chat/completions", json=BODY | {"max_tokens": 50000}, headers=H)
    assert r.status_code == 200 and r.headers["x-companion-via"] == "local"
    assert r.json()["choices"][0]["message"]["content"] == "It batches steps."
    sent = up.last_router_request
    assert sent.headers[S.header_fairness] == "owner"
    assert sent.headers[S.header_objective] == "interactive"
    assert sent.headers[S.header_session] == "s-1"
    assert sent.headers[S.header_ttl].endswith("ms") and 0 < int(sent.headers[S.header_ttl][:-2]) <= 15000
    body = json.loads(sent.content)
    assert body["priority"] == S.priority_interactive and body["max_tokens"] == S.max_tokens_interactive
    assert log.records[-1].route == "local" and log.records[-1].status == 200


async def test_streaming_pass_through(env):
    up, _, client = env
    chunks = [b'data: {"choices":[{"delta":{"content":"It "}}]}\n\n',
              b'data: {"choices":[{"delta":{"content":"batches."}}]}\n\n', b"data: [DONE]\n\n"]
    up.router = lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"".join(chunks))
    async with client.stream("POST", "/v1/chat/completions", json=BODY | {"stream": True}, headers=H) as r:
        data = b"".join([c async for c in r.aiter_raw()])
    assert r.status_code == 200 and data == b"".join(chunks)


async def test_guard_verdict_cache(env):
    up, log, client = env
    await client.post("/v1/chat/completions", json=BODY, headers=H)
    await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert up.calls["guard"] == 1 and up.calls["injection"] == 1 and log.records[-1].guard_cached is True


async def test_router_capacity_reject_leaves_to_overflow(env):
    up, log, client = env
    up.router = lambda req: httpx.Response(429, headers={S.header_dropped_reason: "rejected-saturated"})
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 200 and r.headers["x-companion-via"] == "overflow"
    assert r.headers["x-companion-local-reason"] == "kv_free"
    assert up.calls["overflow"] == 1 and log.records[-1].route == "overflow"


async def test_no_overflow_key_records_the_decision_and_returns_the_local_503():
    up, log = Upstreams(), ListLog()
    up.router = lambda req: httpx.Response(429, headers={S.header_dropped_reason: "rejected-saturated"})
    app = create_app(S.model_copy(update={"overflow_api_key": ""}),
                     http=httpx.AsyncClient(transport=httpx.MockTransport(up)),
                     redis=fakeredis.FakeAsyncRedis(decode_responses=True), count_text=estimate_tokens,
                     access_log=log)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://edge")
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 503 and r.json()["error"]["type"] == "kv_free"
    assert up.calls["overflow"] == 0 and log.records[-1].extra["would_leave"] == "kv_free"


async def test_provider_error_returns_the_local_outcome(env):
    up, log, client = env
    up.router = lambda req: httpx.Response(429, headers={S.header_dropped_reason: "rejected-saturated"})
    up.overflow_status = 401
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 503 and r.json()["error"]["type"] == "kv_free" and r.headers["retry-after"] == "2"
    assert up.calls["overflow"] == 1 and log.records[-1].extra["overflow_error"] == "status_401"


async def test_router_capacity_reject_stays_when_privacy_switch_is_off(env):
    up, _, client = env
    up.router = lambda req: httpx.Response(429, headers={S.header_dropped_reason: "rejected-saturated"})
    r = await client.post("/v1/chat/completions", json=BODY, headers=H | {"X-Allow-Overflow": "false"})
    assert r.status_code == 503 and r.json()["error"]["type"] == "kv_free"
    assert up.calls["overflow"] == 0 and r.headers["retry-after"] == "2"


async def test_tenant_429_never_leaves(env):
    up, _, client = env
    up.router = lambda req: httpx.Response(429, headers={"x-envoy-ratelimited": "true"})
    r = await client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 429 and r.json()["error"]["type"] == "tenant_tokens"
    assert up.calls["overflow"] == 0


async def test_batch_capacity_reject_stays(env):
    up, _, client = env
    up.router = lambda req: httpx.Response(429, headers={S.header_dropped_reason: "rejected-ttl-expired"})
    headers = H | {"X-Tenant-Id": "sweep", "X-Request-Class": "batch"}
    r = await client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert r.status_code == 503 and up.calls["overflow"] == 0


async def test_slice_oom_on_a_pod_with_a_short_context():
    # With the T2 limits, 30,000 prompt tokens plus 2,048 output tokens fit in 32,768. slice_oom
    # triggers on a pod with a shorter context, for example the dev model on an A6000 slice.
    up = Upstreams()
    settings = S.model_copy(update={"max_model_len": 16384})
    app = create_app(settings, http=httpx.AsyncClient(transport=httpx.MockTransport(up)),
                     redis=fakeredis.FakeAsyncRedis(decode_responses=True), count_text=estimate_tokens,
                     access_log=ListLog())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://edge")
    r = await client.post("/v1/chat/completions", headers=H | {"X-Tenant-Id": "sweep", "X-Request-Class": "batch"},
                          json={"model": "companion", "messages": [{"role": "user", "content": "x" * 60000}]})
    assert r.status_code == 413 and r.json()["error"]["type"] == "slice_oom"
    assert up.calls["router"] == 0


async def test_metrics_endpoint(env):
    _, _, client = env
    await client.post("/v1/chat/completions", json=BODY | {"model": "nope"}, headers=H)
    text = (await client.get("/metrics")).text
    assert 'orch_guard_reject_total{reason="model_not_allowed",stage="1"} 1.0' in text
