"""warm-controller: declare warm, recovery ramp, and progress check (system design section 6.5)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from control.warm.adapters import Pod
from control.warm.logic import (
    RAMP_STEPS,
    PodState,
    ProgressWindow,
    WarmSettings,
    is_warm,
    labels_for,
    next_ramp,
)

log = logging.getLogger("warm-controller")


@dataclass(frozen=True)
class ControllerSettings:
    namespace: str = "companion"
    selector: str = "app.kubernetes.io/part-of=companion-vllm"
    port: int = 8000
    served_model: str = "companion"
    warm_label: str = "companion.io/warm"
    ramp_label: str = "companion.io/ramp"
    baseline_ttft_4k_s: float = 0.35  # paper value for Gemma 4 31B FP8 on an H100; Gate G1 measures it
    prefill_header: str = "x-prefiller-host-port"  # PrefillEndpointHeader in llm-d-router v0.11.0
    system_prompts: tuple[str, ...] = ()
    fleet_p99_query: str = ('histogram_quantile(0.99, sum by (le) '
                            '(rate(vllm:time_to_first_token_seconds_bucket{namespace="companion"}[1m])))')
    retry_cold_after_s: float = 300.0
    # E8 arms. mode: "warmup" (our design) or "immediate" (arm A: warm label as soon as the pod is ready).
    # ramp_mode: "ramp" (r10 to r100 while p99 holds) or "jump" (r100 at once).
    mode: str = "warmup"
    ramp_mode: str = "ramp"
    warm: WarmSettings = field(default_factory=WarmSettings)


class WarmMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        r = self.registry = registry or CollectorRegistry()
        self.state = Gauge("orch_worker_state", "1 for the current state of each pod", ["pod", "state"], registry=r)
        self.warmup = Histogram("orch_warmup_seconds", "Warmup time by phase", ["pod", "phase"],
                                buckets=(1, 5, 10, 30, 60, 120, 300, 600), registry=r)
        self.first_ttft = Histogram("orch_first_ttft_seconds", "4K probe TTFT", ["pod", "warm"],
                                    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16), registry=r)
        self.ramp = Gauge("orch_ramp_weight", "Ramp weight of each warm pod", ["pod"], registry=r)
        self.stalls = Counter("orch_engine_stall_total", "Engines that stopped making progress", ["pod"], registry=r)


RAMP_WEIGHT = {"r10": 0.10, "r25": 0.25, "r50": 0.50, "r100": 1.00}


@dataclass
class Track:
    state: PodState = PodState.STARTING
    ramp: str | None = None
    last_ramp_t: float = 0.0
    cold_since: float = 0.0
    task: asyncio.Task | None = None
    progress: ProgressWindow | None = None


class WarmController:
    def __init__(self, kube: Any, prom: Any, probe: Any, settings: ControllerSettings,
                 metrics: WarmMetrics | None = None) -> None:
        self.kube, self.prom, self.probe, self.s = kube, prom, probe, settings
        self.m = metrics or WarmMetrics()
        self.tracks: dict[str, Track] = {}

    def _set_state(self, pod: str, track: Track, state: PodState) -> None:
        for st in PodState:
            self.m.state.labels(pod, st.value).set(1 if st is state else 0)
        track.state = state

    async def _apply_labels(self, pod: Pod, track: Track) -> None:
        wanted = labels_for(track.state, track.ramp, self.s.warm_label, self.s.ramp_label)
        current = {k: pod.labels.get(k) for k in wanted}
        if current != wanted:
            await self.kube.patch_labels(pod.name, wanted)
        self.m.ramp.labels(pod.name).set(RAMP_WEIGHT.get(track.ramp or "", 0.0) if track.state is PodState.WARM
                                          else 0.0)

    async def reconcile_once(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        pods = await self.kube.list_pods(self.s.selector)
        seen = set()
        for pod in pods:
            seen.add(pod.name)
            track = self.tracks.setdefault(pod.name, Track(progress=ProgressWindow(self.s.warm.stall_window_s)))
            if pod.deleting:
                self._set_state(pod.name, track, PodState.DRAINING)
            elif not pod.ready:
                if track.state not in (PodState.WARMING,):
                    self._set_state(pod.name, track, PodState.STARTING)
            elif self.s.mode == "immediate" and track.state in (PodState.STARTING, PodState.ENGINE_READY):
                # Arm A of E8: no warmup routine. The first user requests meet a cold engine.
                track.ramp, track.last_ramp_t = self._first_step(), now
                self._set_state(pod.name, track, PodState.WARM)
            elif track.state in (PodState.STARTING, PodState.ENGINE_READY) or (
                    track.state is PodState.COLD_FAILED and now - track.cold_since > self.s.retry_cold_after_s):
                if track.task is None or track.task.done():
                    self._set_state(pod.name, track, PodState.WARMING)
                    track.task = asyncio.create_task(self.warmup(pod, track, pods))
            elif track.state is PodState.WARM:
                await self._ramp_and_check(pod, track, now)
            await self._apply_labels(pod, track)
        for gone in set(self.tracks) - seen:
            task = self.tracks.pop(gone).task
            if task and not task.done():
                task.cancel()

    async def warmup(self, pod: Pod, track: Track, pods: list[Pod]) -> None:
        base = f"http://{pod.ip}:{self.s.port}"
        t0 = time.monotonic()
        if not await self.probe.engine_ready(base):
            self._set_state(pod.name, track, PodState.STARTING)
            return
        self.m.warmup.labels(pod.name, "engine_ready").observe(time.monotonic() - t0)
        if self.s.system_prompts and not await self.probe.seed_prefixes(base, self.s.system_prompts):
            return self._cold(pod, track)
        if not await self.probe.shape_probes(base):
            return self._cold(pod, track)
        if pod.role == "decode":
            for other in pods:
                if other.role == "prefill" and other.ready and other.ip:
                    await self.probe.split_probe(base, f"{other.ip}:{self.s.port}", self.s.prefill_header)
        for _ in range(self.s.warm.max_attempts):
            result = await self.probe.ttft_probe(base)
            if result.ok and result.ttft_s is not None:
                warm = is_warm(result.ttft_s, self.s.baseline_ttft_4k_s, self.s.warm)
                self.m.first_ttft.labels(pod.name, str(warm).lower()).observe(result.ttft_s)
                if warm:
                    track.ramp, track.last_ramp_t = self._first_step(), time.monotonic()
                    self._set_state(pod.name, track, PodState.WARM)
                    self.m.warmup.labels(pod.name, "total").observe(time.monotonic() - t0)
                    log.info("pod %s is warm (4K TTFT %.3f s)", pod.name, result.ttft_s)
                    return
            await self.probe.shape_probes(base)
        self._cold(pod, track)

    def _first_step(self) -> str:
        return RAMP_STEPS[-1] if self.s.ramp_mode == "jump" else RAMP_STEPS[0]

    def _cold(self, pod: Pod, track: Track) -> None:
        track.cold_since = time.monotonic()
        self._set_state(pod.name, track, PodState.COLD_FAILED)
        log.warning("pod %s failed the warmup", pod.name)

    async def _ramp_and_check(self, pod: Pod, track: Track, now: float) -> None:
        waiting = await self.prom.scalar(f'vllm:num_requests_waiting{{pod="{pod.name}"}}')
        generated = await self.prom.scalar(f'vllm:generation_tokens_total{{pod="{pod.name}"}}')
        if waiting is not None and generated is not None and track.progress is not None:
            track.progress.add(now, waiting, generated)
            if track.progress.stalled():
                self.m.stalls.labels(pod.name).inc()
                self._set_state(pod.name, track, PodState.STALLED)
                log.error("pod %s stopped making progress (waiting %s)", pod.name, waiting)
                return
        if self.s.ramp_mode == "jump":
            return  # the jump arm of E8 keeps r100
        if now - track.last_ramp_t >= self.s.warm.ramp_interval_s:  # r100 too: it can fall back to r10
            p99 = await self.prom.scalar(self.s.fleet_p99_query)
            track.ramp = next_ramp(track.ramp or RAMP_STEPS[0], p99, self.s.baseline_ttft_4k_s, self.s.warm)
            track.last_ramp_t = now

    async def run(self, interval_s: float = 5.0) -> None:
        while True:
            try:
                await self.reconcile_once()
            except Exception:  # noqa: BLE001 - keep the controller alive; the next pass retries
                log.exception("reconcile failed")
            await asyncio.sleep(interval_s)
