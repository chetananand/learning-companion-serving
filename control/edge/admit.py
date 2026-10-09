"""The part of admit that runs in `edge` (system design section 6.2).

The Agent Router enforces the tenant windows, and the llm-d flow control enforces
the queue TTL and saturation. `edge` checks `slice_oom` and maps the rejects of
those components to our codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    status: int  # the code for the app
    reason: str
    may_leave: bool  # the overflow gate can send it out (section 6.6)
    retry_after_s: int | None = None
    stage: str = "engine"  # guard | edge | rate_limit | flow_control | engine


def slice_oom(prompt_tokens: int, max_tokens: int, max_model_len: int) -> Outcome | None:
    if prompt_tokens + max_tokens > max_model_len:
        return Outcome(413, "slice_oom", may_leave=False, stage="edge")
    return None


def _retry_after(headers: Mapping[str, str], default: int) -> int:
    value = headers.get("retry-after", "")
    return int(value) if value.isdigit() else default


# llm-d v0.11.0 reasons (pkg/common/error) -> (our class7 reason, may leave). System design section 6.2.
ROUTER_REASONS: dict[str, tuple[str, bool]] = {
    "rejected-saturated": ("kv_free", True),
    "rejected-ttl-expired": ("timeout_queue", True),
    "rejected-no-endpoints": ("no_endpoints", True),
    "evicted-priority": ("p99_spread", True),
    "evicted-queue-pressure": ("queue_pressure", True),
    "evicted": ("evicted", True),
    "rejected-shutting-down": ("router_shutdown", True),
    "rejected-context-cancelled": ("client_gone", False),
    "rejected-internal": ("router_internal", False),
}


def router_reason(raw: str) -> tuple[str, bool]:
    return ROUTER_REASONS.get(raw, (f"router_{raw}", True))


def classify_upstream(status: int, headers: Mapping[str, str], dropped_reason_header: str) -> Outcome:
    """Map an upstream result to our code, reason, and stay-or-leave class."""
    h = {k.lower(): v for k, v in headers.items()}
    if status < 400:
        return Outcome(status, "ok", may_leave=False)
    dropped = h.get(dropped_reason_header.lower())
    if dropped:
        # Since llm-d v0.9, the router uses 429 for capacity and queue TTL. We keep 429 for tenants.
        reason, may_leave = router_reason(dropped)
        code = 500 if reason == "router_internal" else 503
        return Outcome(code, reason, may_leave=may_leave, retry_after_s=_retry_after(h, 2), stage="flow_control")
    if status == 429:
        return Outcome(429, "tenant_tokens", may_leave=False, retry_after_s=_retry_after(h, 60), stage="rate_limit")
    if status in (503, 529):
        reason = "no_endpoints" if status == 503 else "overloaded"
        return Outcome(status, reason, may_leave=True, retry_after_s=_retry_after(h, 2), stage="flow_control")
    if status >= 500:
        return Outcome(500, "upstream_error", may_leave=False)
    return Outcome(status, "upstream_rejected", may_leave=False)
