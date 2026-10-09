"""Stay or leave: the overflow gate (system design section 6.6, ADR-008)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from control.edge.admit import Outcome
from control.edge.config import EdgeSettings


@dataclass(frozen=True)
class LeaveDecision:
    leave: bool
    why: str


def should_leave(outcome: Outcome, *, request_class: str, allow_overflow: bool, has_image: bool,
                 remaining_deadline_s: float | None, settings: EdgeSettings) -> LeaveDecision:
    """The pure part of the gate. The limiter runs after a `leave` result."""
    if not settings.overflow_enabled:
        return LeaveDecision(False, "overflow_disabled")
    if not outcome.may_leave:
        return LeaveDecision(False, f"stays_on_{outcome.reason}")
    if request_class != "interactive":
        return LeaveDecision(False, "batch_stays")
    if not allow_overflow:
        return LeaveDecision(False, "privacy_switch")
    if has_image:
        return LeaveDecision(False, "image_stays")
    if remaining_deadline_s is not None and remaining_deadline_s <= settings.overflow_p50_s:
        return LeaveDecision(False, "deadline_too_short")
    return LeaveDecision(True, "capacity")


class OverflowLimiter:
    """Redis limiter shared by the `edge` replicas: requests and tokens each minute, in flight, USD each day."""

    def __init__(self, redis: Any, settings: EdgeSettings) -> None:
        self.redis, self.s = redis, settings

    @staticmethod
    def _keys(now: dt.datetime) -> tuple[str, str, str, str]:
        minute, day = now.strftime("%Y%m%d%H%M"), now.strftime("%Y%m%d")
        return (f"ovf:req:{minute}", f"ovf:tok:{minute}", "ovf:inflight", f"ovf:usd:{day}")

    async def acquire(self, tokens: int, now: dt.datetime | None = None) -> str | None:
        """Take one slot. Return None if permitted, else the reason for the refusal."""
        now = now or dt.datetime.now(dt.UTC)
        req_key, tok_key, inflight_key, usd_key = self._keys(now)
        usd_cost = tokens / 1000 * self.s.overflow_usd_per_1k_tokens
        pipe = self.redis.pipeline()
        pipe.incr(req_key)
        pipe.expire(req_key, 120)
        pipe.incrby(tok_key, tokens)
        pipe.expire(tok_key, 120)
        pipe.incr(inflight_key)
        pipe.expire(inflight_key, 600)
        pipe.incrbyfloat(usd_key, usd_cost)
        pipe.expire(usd_key, 2 * 86400)
        req, _, tok, _, inflight, _, usd, _ = await pipe.execute()
        why = None
        if int(req) > self.s.overflow_rpm:
            why = "rpm"
        elif int(tok) > self.s.overflow_tpm:
            why = "tpm"
        elif int(inflight) > self.s.overflow_in_flight:
            why = "in_flight"
        elif float(usd) > self.s.overflow_usd_per_day:
            why = "daily_budget"
        if why:
            undo = self.redis.pipeline()
            undo.decr(req_key)
            undo.decrby(tok_key, tokens)
            undo.decr(inflight_key)
            undo.incrbyfloat(usd_key, -usd_cost)
            await undo.execute()
        return why

    async def release(self) -> None:
        await self.redis.decr("ovf:inflight")
