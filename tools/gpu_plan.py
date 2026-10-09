"""Check the GPU plan of the one-node layout (ADR-005, revision 2) from the HAMi pod annotations.

HAMi writes the GPUs of each pod into the annotation `hami.io/vgpu-devices-allocated`: for each container,
"UUID,type,memory MiB,cores:" for each device, and ";" between the containers (HAMi v2.10.0,
pkg/device/devices.go). The plan passes when:
  1. each engine pod (vllm-prefill, vllm-decode) has one GPU with 100% of the cores,
  2. no other pod uses the GPU of an engine pod,
  3. the node 2 GPU pods (SIE embed, SIE OCR, the two guard models) share one GPU.

Usage: kubectl get pods -A -o json | python3 tools/gpu_plan.py
       python3 tools/gpu_plan.py kv <vllm-prefill log> <vllm-decode log>   (vLLM sees the full GPU)
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ANNOTATION = "hami.io/vgpu-devices-allocated"
# "Available KV cache memory" of the base engine on 2026-09-29 (node 1, NVIDIA device plugin, no HAMi):
# metrics/t2-20260930T022105Z/vllm-*.log. A HAMi memory limit in vLLM would make it smaller.
KV_REFERENCE_GIB = {"vllm-prefill": 36.34, "vllm-decode": 38.36}
KV_TOLERANCE_GIB = 1.0
KV_MIN_SHARE = 0.8  # 2026-10-01: the one node can be 8 x A100 80 GB (the owner), so the check is relative
ENGINE_APPS = ("vllm-prefill", "vllm-decode")
NODE2_GPU_APPS = ("sie-embed", "sie-ocr", "guard-safety", "guard-injection")


def devices(annotation: str) -> list[tuple[str, int, int]]:
    """Return (uuid, memory MiB, cores) for each device in the annotation."""
    out = []
    for container in annotation.split(";"):
        for dev in container.split(":"):
            parts = dev.split(",")
            if len(parts) >= 4 and parts[0]:
                out.append((parts[0], int(parts[2]), int(parts[3])))
    return out


def app_of(pod: dict[str, Any]) -> str:
    labels = pod["metadata"].get("labels") or {}
    return labels.get("app") or labels.get("app.kubernetes.io/name") or pod["metadata"]["name"]


def check(pods: list[dict[str, Any]]) -> dict[str, Any]:
    plan: dict[str, list[tuple[str, int, int]]] = {}
    apps: dict[str, str] = {}
    problems: list[str] = []
    for pod in pods:
        if (pod.get("status") or {}).get("phase") not in ("Running", "Pending"):
            continue
        name, app = f"{pod['metadata']['namespace']}/{pod['metadata']['name']}", app_of(pod)
        ann = (pod["metadata"].get("annotations") or {}).get(ANNOTATION)
        if ann:
            plan[name], apps[name] = devices(ann), app
        elif app in ENGINE_APPS + NODE2_GPU_APPS:
            problems.append(f"{name}: no {ANNOTATION}, so HAMi did not place it")
    by_gpu: dict[str, list[str]] = defaultdict(list)
    for name, devs in plan.items():
        for uuid, _, _ in devs:
            by_gpu[uuid].append(name)
    for name, devs in plan.items():
        if apps[name] not in ENGINE_APPS:
            continue
        if len(devs) != 1 or devs[0][2] != 100:
            problems.append(f"{name}: expected one GPU with 100% of the cores, found {devs}")
        for uuid, _, _ in devs:
            if others := [n for n in by_gpu[uuid] if n != name]:
                problems.append(f"{name}: shares its GPU {uuid} with {', '.join(others)}")
    node2 = {uuid for name, devs in plan.items() if apps[name] in NODE2_GPU_APPS for uuid, _, _ in devs}
    if len(node2) > 1:
        problems.append(f"the node 2 GPU pods use {len(node2)} GPUs, expected one: {', '.join(sorted(node2))}")
    return {"gpus": {u: sorted(n) for u, n in by_gpu.items()}, "pods": plan, "problems": problems,
            "pass": not problems}


def kv_memory_gib(log: str) -> float | None:
    """The last "Available KV cache memory: X GiB" of a vLLM log."""
    found = re.findall(r"Available KV cache memory: ([0-9.]+) GiB", log)
    return float(found[-1]) if found else None


def kv_problems(logs: dict[str, str]) -> list[str]:
    problems = []
    for app, ref in KV_REFERENCE_GIB.items():
        got = kv_memory_gib(logs.get(app, ""))
        if got is None:
            problems.append(f"{app}: no 'Available KV cache memory' line in the log")
        elif got < KV_MIN_SHARE * ref:  # a HAMi limit gives much less; another GPU of 80 GB gives about the same
            problems.append(f"{app}: {got} GiB of KV cache memory, less than {KV_MIN_SHARE:.0%} of {ref} GiB")
    return problems


def main() -> int:
    if sys.argv[1:2] == ["kv"]:  # python3 tools/gpu_plan.py kv <prefill.log> <decode.log>
        logs = {app: Path(path).read_text() for app, path in zip(KV_REFERENCE_GIB, sys.argv[2:4], strict=True)}
        for app, text in logs.items():
            print(f"{app}: {kv_memory_gib(text)} GiB (2026-09-29: {KV_REFERENCE_GIB[app]} GiB)")
        problems = kv_problems(logs)
        for p in problems:
            print(f"PROBLEM: {p}")
        print("KV memory: PASS" if not problems else "KV memory: FAIL")
        return 0 if not problems else 1
    result = check(json.load(sys.stdin)["items"])
    for uuid, names in sorted(result["gpus"].items()):
        print(f"{uuid}: {', '.join(names)}")
    for p in result["problems"]:
        print(f"PROBLEM: {p}")
    print("GPU plan: PASS" if result["pass"] else "GPU plan: FAIL")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
