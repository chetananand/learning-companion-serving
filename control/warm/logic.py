"""Pure decision logic of the warm-controller (system design section 6.5). No I/O here."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

RAMP_STEPS = ("r10", "r25", "r50", "r100")


class PodState(StrEnum):
    STARTING = "starting"          # the pod exists, but vLLM is not ready
    ENGINE_READY = "engine_ready"  # /health and /v1/models answer; not warm yet
    WARMING = "warming"            # the warmup routine runs; no user traffic
    WARM = "warm"                  # the router may pick it (ramp label controls the weight)
    COLD_FAILED = "cold_failed"    # warmup failed 3 times
    STALLED = "stalled"            # progress check failed (vLLM issue 53130)
    DRAINING = "draining"          # the pod is going away


@dataclass(frozen=True)
class WarmSettings:
    warm_factor: float = 1.5        # 4K probe TTFT at most 1.5 x the warm baseline
    max_attempts: int = 3
    ramp_interval_s: float = 10.0
    ramp_up_factor: float = 2.0     # step up while the fleet TTFT p99 is at most 2 x the baseline
    ramp_down_factor: float = 3.0   # back to r10 when the p99 is above 3 x the baseline
    stall_window_s: float = 60.0


def is_warm(probe_ttft_s: float, baseline_s: float, s: WarmSettings) -> bool:
    return probe_ttft_s <= s.warm_factor * baseline_s


def next_ramp(current: str, fleet_p99_s: float | None, baseline_s: float, s: WarmSettings) -> str:
    """One ramp decision (H-73). Unknown p99 holds the step: unknown is not good news."""
    if current not in RAMP_STEPS:
        return RAMP_STEPS[0]
    if fleet_p99_s is None:
        return current
    if fleet_p99_s > s.ramp_down_factor * baseline_s:
        return RAMP_STEPS[0]
    if fleet_p99_s <= s.ramp_up_factor * baseline_s:
        i = RAMP_STEPS.index(current)
        return RAMP_STEPS[min(i + 1, len(RAMP_STEPS) - 1)]
    return current


@dataclass
class ProgressWindow:
    """Detect an engine that stops (issue 53130): waiting > 0 and no new generation tokens for a window."""

    window_s: float
    samples: deque[tuple[float, float, float]] = field(default_factory=deque)  # (t, waiting, gen_tokens)

    def add(self, t: float, waiting: float, gen_tokens: float) -> None:
        self.samples.append((t, waiting, gen_tokens))
        while self.samples and t - self.samples[0][0] > self.window_s * 2:
            self.samples.popleft()

    def stalled(self) -> bool:
        if not self.samples:
            return False
        t_now, _, g_now = self.samples[-1]
        old = [s for s in self.samples if t_now - s[0] >= self.window_s]
        if not old:
            return False  # not enough history yet
        t_old, _, g_old = old[-1]
        recent = [s for s in self.samples if s[0] >= t_old]
        return all(w > 0 for _, w, _ in recent) and g_now <= g_old


def labels_for(state: PodState, ramp: str | None, warm_label: str, ramp_label: str) -> dict[str, str | None]:
    """The pod labels for a state. None removes the label."""
    if state is PodState.WARM:
        return {warm_label: "true", ramp_label: ramp or RAMP_STEPS[0]}
    return {warm_label: None, ramp_label: None}
