import datetime as dt

import fakeredis
import pytest

from control.edge.admit import Outcome, classify_upstream, slice_oom
from control.edge.config import EdgeSettings
from control.edge.overflow import OverflowLimiter, should_leave

S = EdgeSettings()
DROP = S.header_dropped_reason


def test_slice_oom():
    assert slice_oom(31000, 2048, 32768).reason == "slice_oom"
    assert slice_oom(1000, 1024, 32768) is None


def test_router_capacity_429_becomes_503_that_may_leave():
    o = classify_upstream(429, {DROP: "rejected-ttl-expired", "retry-after": "3"}, DROP)
    assert (o.status, o.reason, o.may_leave, o.retry_after_s) == (503, "timeout_queue", True, 3)


@pytest.mark.parametrize(("raw", "reason", "code", "may_leave"), [
    ("rejected-saturated", "kv_free", 503, True),
    ("rejected-no-endpoints", "no_endpoints", 503, True),
    ("evicted-priority", "p99_spread", 503, True),
    ("rejected-context-cancelled", "client_gone", 503, False),
    ("rejected-internal", "router_internal", 500, False),
    ("rejected-new-thing", "router_rejected-new-thing", 503, True),
])
def test_router_reason_map(raw, reason, code, may_leave):
    o = classify_upstream(429, {DROP: raw}, DROP)
    assert (o.reason, o.status, o.may_leave) == (reason, code, may_leave)


def test_tenant_429_stays_429():
    o = classify_upstream(429, {"x-envoy-ratelimited": "true"}, DROP)
    assert (o.status, o.reason, o.may_leave) == (429, "tenant_tokens", False)


@pytest.mark.parametrize(("status", "may_leave", "code"), [(503, True, 503), (529, True, 529), (500, False, 500),
                                                           (502, False, 500), (400, False, 400)])
def test_other_codes(status, may_leave, code):
    o = classify_upstream(status, {}, DROP)
    assert (o.status, o.may_leave) == (code, may_leave)


def leave(outcome, **kw):
    args = dict(request_class="interactive", allow_overflow=True, has_image=False, remaining_deadline_s=30.0,
                settings=S)
    args.update(kw)
    return should_leave(outcome, **args)


CAPACITY = Outcome(503, "router_capacity", may_leave=True)


def test_stay_or_leave_table():
    assert leave(CAPACITY).leave
    assert leave(Outcome(429, "tenant_tokens", may_leave=False)).why == "stays_on_tenant_tokens"
    assert leave(Outcome(503, "guard_unavailable", may_leave=False)).leave is False
    assert leave(CAPACITY, request_class="batch").why == "batch_stays"
    assert leave(CAPACITY, allow_overflow=False).why == "privacy_switch"
    assert leave(CAPACITY, has_image=True).why == "image_stays"
    assert leave(CAPACITY, remaining_deadline_s=1.0).why == "deadline_too_short"
    assert leave(CAPACITY, settings=EdgeSettings(overflow_enabled=False)).why == "overflow_disabled"


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


async def test_limiter_requests_per_minute(redis):
    lim = OverflowLimiter(redis, EdgeSettings(overflow_rpm=2, overflow_in_flight=10))
    now = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
    assert await lim.acquire(100, now) is None
    assert await lim.acquire(100, now) is None
    assert await lim.acquire(100, now) == "rpm"
    assert await lim.acquire(100, now + dt.timedelta(minutes=1)) is None


async def test_limiter_in_flight_and_release(redis):
    lim = OverflowLimiter(redis, EdgeSettings(overflow_in_flight=1))
    assert await lim.acquire(10) is None
    assert await lim.acquire(10) == "in_flight"
    await lim.release()
    assert await lim.acquire(10) is None


async def test_limiter_daily_budget(redis):
    lim = OverflowLimiter(redis, EdgeSettings(overflow_usd_per_day=0.01, overflow_usd_per_1k_tokens=0.01,
                                              overflow_tpm=10**9, overflow_in_flight=100))
    assert await lim.acquire(900) is None
    assert await lim.acquire(900) == "daily_budget"
