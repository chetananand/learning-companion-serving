import asyncio

import pytest

from control.warm.adapters import Pod
from control.warm.controller import ControllerSettings, WarmController
from control.warm.logic import PodState, ProgressWindow, WarmSettings, is_warm, labels_for, next_ramp
from control.warm.probes import ProbeResult, prompt_of

W = WarmSettings()


def test_is_warm():
    assert is_warm(0.5, 0.35, W) and not is_warm(0.6, 0.35, W)


@pytest.mark.parametrize(("current", "p99", "expected"), [
    ("r10", 0.5, "r25"), ("r25", 0.7, "r50"), ("r50", 0.1, "r100"), ("r100", 0.1, "r100"),
    ("r50", 0.9, "r50"),     # between 2x and 3x: hold
    ("r100", 1.2, "r10"),    # above 3x: back to r10
    ("r25", None, "r25"),    # unknown p99: hold
    ("junk", 0.1, "r10"),
])
def test_next_ramp(current, p99, expected):
    assert next_ramp(current, p99, 0.35, W) == expected


def test_progress_window_detects_a_stall():
    pw = ProgressWindow(60)
    for t in range(0, 121, 15):
        pw.add(float(t), waiting=3, gen_tokens=1000)
    assert pw.stalled()


def test_progress_window_no_stall_when_tokens_grow_or_queue_is_empty():
    grow = ProgressWindow(60)
    idle = ProgressWindow(60)
    for t in range(0, 121, 15):
        grow.add(float(t), waiting=3, gen_tokens=1000 + t)
        idle.add(float(t), waiting=0, gen_tokens=1000)
    assert not grow.stalled() and not idle.stalled()


def test_labels_for():
    assert labels_for(PodState.WARM, "r25", "w", "r") == {"w": "true", "r": "r25"}
    assert labels_for(PodState.WARMING, None, "w", "r") == {"w": None, "r": None}


def test_prompt_of_length():
    assert len(prompt_of(1024)) == 4096


class FakeKube:
    def __init__(self, pods):
        self.pods = pods
        self.patches = []

    async def list_pods(self, selector):
        return self.pods

    async def patch_labels(self, pod, labels):
        self.patches.append((pod, labels))
        for i, p in enumerate(self.pods):
            if p.name == pod:
                merged = {**p.labels, **{k: v for k, v in labels.items() if v is not None}}
                for k, v in labels.items():
                    if v is None:
                        merged.pop(k, None)
                self.pods[i] = Pod(p.name, p.ip, p.ready, p.deleting, p.role, merged)


class FakeProm:
    def __init__(self):
        self.p99 = 0.4
        self.waiting = 0.0
        self.generated = 100.0

    async def scalar(self, query):
        if "num_requests_waiting" in query:
            return self.waiting
        if "generation_tokens_total" in query:
            return self.generated
        return self.p99


class FakeProbe:
    def __init__(self, ttfts):
        self.ttfts = list(ttfts)
        self.splits = []

    async def engine_ready(self, base):
        return True

    async def seed_prefixes(self, base, prompts):
        return True

    async def shape_probes(self, base, sizes=()):
        return True

    async def split_probe(self, decode_base, prefill, header):
        self.splits.append((decode_base, prefill, header))
        return ProbeResult(True, 0.5)

    async def ttft_probe(self, base, tokens=4096):
        return ProbeResult(True, self.ttfts.pop(0) if self.ttfts else 9.9)


S = ControllerSettings(baseline_ttft_4k_s=0.35)


def pod(name="vllm-decode-0", ready=True, role="decode", labels=None, deleting=False, ip="10.0.0.5"):
    return Pod(name, ip, ready, deleting, role, labels or {})


async def settle(ctrl, now=0.0):
    await ctrl.reconcile_once(now)
    for t in [tr.task for tr in ctrl.tracks.values() if tr.task]:
        await t
    await ctrl.reconcile_once(now)


async def test_ready_pod_gets_warm_and_r10_labels():
    kube = FakeKube([pod(), pod("vllm-prefill-0", role="prefill", ip="10.0.0.4")])
    probe = FakeProbe([0.4, 0.4])
    ctrl = WarmController(kube, FakeProm(), probe, S)
    await settle(ctrl)
    labels = {p.name: p.labels for p in kube.pods}
    assert labels["vllm-decode-0"] == {"companion.io/warm": "true", "companion.io/ramp": "r10"}
    assert probe.splits == [("http://10.0.0.5:8000", "10.0.0.4:8000", S.prefill_header)]


async def test_not_ready_pod_gets_no_labels():
    kube = FakeKube([pod(ready=False, labels={"companion.io/warm": "true"})])
    ctrl = WarmController(kube, FakeProm(), FakeProbe([]), S)
    await ctrl.reconcile_once(0.0)
    assert ctrl.tracks["vllm-decode-0"].state is PodState.STARTING
    assert "companion.io/warm" not in kube.pods[0].labels


async def test_slow_pod_fails_warmup_and_stays_unlabeled():
    kube = FakeKube([pod()])
    ctrl = WarmController(kube, FakeProm(), FakeProbe([2.0, 2.0, 2.0]), S)
    await settle(ctrl)
    assert ctrl.tracks["vllm-decode-0"].state is PodState.COLD_FAILED
    assert "companion.io/warm" not in kube.pods[0].labels


async def test_ramp_steps_up_while_p99_holds_and_falls_back():
    kube = FakeKube([pod()])
    prom = FakeProm()
    ctrl = WarmController(kube, prom, FakeProbe([0.4]), S)
    await settle(ctrl, now=0.0)
    track = ctrl.tracks["vllm-decode-0"]
    track.last_ramp_t = 0.0
    await ctrl.reconcile_once(10.0)
    assert kube.pods[0].labels["companion.io/ramp"] == "r25"
    prom.p99 = 5.0
    await ctrl.reconcile_once(20.0)
    assert kube.pods[0].labels["companion.io/ramp"] == "r10"


async def test_stalled_engine_loses_its_labels():
    kube = FakeKube([pod()])
    prom = FakeProm()
    ctrl = WarmController(kube, prom, FakeProbe([0.4]), S)
    await settle(ctrl, now=0.0)
    prom.waiting = 4.0
    for t in range(15, 136, 15):
        await ctrl.reconcile_once(float(t))
    assert ctrl.tracks["vllm-decode-0"].state is PodState.STALLED
    assert "companion.io/warm" not in kube.pods[0].labels


async def test_deleting_pod_is_drained():
    kube = FakeKube([pod(deleting=True, labels={"companion.io/warm": "true", "companion.io/ramp": "r100"})])
    ctrl = WarmController(kube, FakeProm(), FakeProbe([]), S)
    await ctrl.reconcile_once(0.0)
    assert ctrl.tracks["vllm-decode-0"].state is PodState.DRAINING and kube.pods[0].labels == {}


async def test_gone_pod_task_is_cancelled():
    kube = FakeKube([pod()])
    ctrl = WarmController(kube, FakeProm(), FakeProbe([0.4]), S)
    await ctrl.reconcile_once(0.0)
    kube.pods = []
    await ctrl.reconcile_once(1.0)
    await asyncio.sleep(0)
    assert ctrl.tracks == {}


async def test_e8_arm_a_immediate_mode_skips_the_warmup():
    from dataclasses import replace

    kube = FakeKube([pod()])
    probe = FakeProbe([])
    ctrl = WarmController(kube, FakeProm(), probe, replace(S, mode="immediate"))
    await ctrl.reconcile_once(0.0)
    assert kube.pods[0].labels == {"companion.io/warm": "true", "companion.io/ramp": "r10"}
    assert probe.splits == [] and ctrl.tracks["vllm-decode-0"].task is None  # no probes at all


async def test_e8_jump_mode_goes_to_r100_and_stays():
    from dataclasses import replace

    kube = FakeKube([pod()])
    prom = FakeProm()
    ctrl = WarmController(kube, prom, FakeProbe([0.4]), replace(S, ramp_mode="jump"))
    await settle(ctrl, now=0.0)
    assert kube.pods[0].labels["companion.io/ramp"] == "r100"
    prom.p99 = 5.0
    await ctrl.reconcile_once(100.0)
    assert kube.pods[0].labels["companion.io/ramp"] == "r100"
