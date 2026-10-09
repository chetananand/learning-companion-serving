"""Prometheus metrics of the Companion API (system design 10.3, the "App" dashboard)."""

from __future__ import annotations

from dataclasses import dataclass, field

from prometheus_client import CollectorRegistry, Counter, Histogram

_TIME = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 20, 30, 45, 60)


@dataclass
class AppMetrics:
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)

    def __post_init__(self) -> None:
        r = self.registry
        self.turns = Counter("companion_turns_total", "User turns", ["mode", "outcome"], registry=r)
        self.turn_seconds = Histogram("companion_turn_seconds", "Turn latency", ["mode", "phase"],
                                      buckets=_TIME, registry=r)
        self.tool_seconds = Histogram("companion_tool_seconds", "Tool latency", ["tool"], buckets=_TIME, registry=r)
        self.tool_errors = Counter("companion_tool_errors_total", "Tool errors", ["tool", "reason"], registry=r)
        self.verdicts = Counter("companion_verdicts_total", "Fact-check results", ["status", "path"], registry=r)
        self.gate = Counter("companion_answer_gate_total", "Answer gate decisions", ["agent", "reason"], registry=r)
        self.page_check_removed = Counter("companion_page_check_removed_total", "Windows removed by the page check",
                                          ["tool"], registry=r)
