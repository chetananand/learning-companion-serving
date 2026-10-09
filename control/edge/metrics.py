"""Prometheus metrics of `edge` (system design section 10.1). We keep the class10 `orch_` names."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 1.5, 2, 3, 5, 10, 20, 30, 60)


class EdgeMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.requests = Counter("orch_requests_total", "Requests into edge", ["tenant", "class", "step"], registry=r)
        self.guard_rejects = Counter("orch_guard_reject_total", "Guard rejects", ["stage", "reason"], registry=r)
        self.guard_seconds = Histogram("orch_guard_seconds", "Guard latency", ["stage", "cached"],
                                       buckets=LATENCY_BUCKETS, registry=r)
        self.sheds = Counter("orch_shed_total", "Sheds by reason, code, and stage", ["reason", "code", "stage"],
                             registry=r)
        self.overflow = Counter("orch_overflow_total", "Requests sent to the overflow", ["model", "reason"],
                                registry=r)
        self.overflow_refused = Counter("orch_overflow_refused_total", "Overflow refusals", ["why"], registry=r)
        self.ttft = Histogram("orch_ttft_seconds", "Time to first byte at edge", ["class", "route"],
                              buckets=LATENCY_BUCKETS, registry=r)
        self.duration = Histogram("orch_request_duration_seconds", "Request duration at edge", ["class", "route"],
                                  buckets=LATENCY_BUCKETS, registry=r)
