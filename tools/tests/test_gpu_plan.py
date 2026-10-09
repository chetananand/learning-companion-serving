"""Tests of the one-node GPU plan check (HAMi annotations, ADR-005 revision 2)."""

from tools.gpu_plan import check, devices

G = ["GPU-aaa", "GPU-bbb", "GPU-ccc", "GPU-ddd"]


def pod(ns, name, app, ann=None, phase="Running"):
    meta = {"namespace": ns, "name": name, "labels": {"app": app}, "annotations": {}}
    if ann is not None:
        meta["annotations"]["hami.io/vgpu-devices-allocated"] = ann
    return {"metadata": meta, "status": {"phase": phase}}


def good():
    return [pod("companion", "vllm-prefill-1", "vllm-prefill", f"{G[0]},NVIDIA,81559,100:;"),
            pod("companion", "vllm-decode-1", "vllm-decode", f"{G[1]},NVIDIA,81559,100:;"),
            pod("data", "sie-embed-1", "sie-embed", f"{G[3]},NVIDIA,12000,0:;"),
            pod("data", "sie-ocr-1", "sie-ocr", f"{G[3]},NVIDIA,20000,0:;"),
            pod("guard", "guard-safety-1", "guard-safety", f"{G[3]},NVIDIA,16000,0:;"),
            pod("guard", "guard-injection-1", "guard-injection", f"{G[3]},NVIDIA,4000,0:;"),
            pod("companion", "edge-1", "edge")]


def test_devices_parses_the_hami_format():
    assert devices("GPU-aaa,NVIDIA,81559,100:;") == [("GPU-aaa", 81559, 100)]
    assert devices("GPU-a,NVIDIA,10,0:GPU-b,NVIDIA,20,0:;GPU-c,NVIDIA,30,0:") == [
        ("GPU-a", 10, 0), ("GPU-b", 20, 0), ("GPU-c", 30, 0)]


def test_the_planned_layout_passes():
    r = check(good())
    assert r["pass"], r["problems"]
    assert r["gpus"][G[3]] == ["data/sie-embed-1", "data/sie-ocr-1", "guard/guard-injection-1",
                               "guard/guard-safety-1"]


def test_a_shared_engine_gpu_fails():
    pods = good()
    pods[2] = pod("data", "sie-embed-1", "sie-embed", f"{G[0]},NVIDIA,12000,0:;")  # spread onto the prefill GPU
    r = check(pods)
    assert not r["pass"]
    assert any("shares its GPU" in p for p in r["problems"])
    assert any("node 2 GPU pods use 2 GPUs" in p for p in r["problems"])


def test_an_engine_pod_outside_hami_fails():  # for example, the t2 overlay with the skip label on one node
    pods = good()
    pods[0] = pod("companion", "vllm-prefill-1", "vllm-prefill")
    assert any("HAMi did not place it" in p for p in check(pods)["problems"])


def test_an_engine_pod_without_the_full_cores_fails():
    pods = good()
    pods[1] = pod("companion", "vllm-decode-1", "vllm-decode", f"{G[1]},NVIDIA,40000,50:;")
    assert any("100% of the cores" in p for p in check(pods)["problems"])


def test_e9_third_engine_pod_on_the_last_idle_gpu_passes():
    pods = good() + [pod("companion", "vllm-decode-2", "vllm-decode", f"{G[2]},NVIDIA,81559,100:;")]
    assert check(pods)["pass"]


def test_kv_memory_matches_the_two_node_engine():
    from tools.gpu_plan import kv_memory_gib, kv_problems
    line = "(EngineCore pid=1) INFO 10-01 [gpu_worker.py:640] Available KV cache memory: {} GiB"
    assert kv_memory_gib(line.format("38.36")) == 38.36 and kv_memory_gib("no line") is None
    assert kv_problems({"vllm-prefill": line.format("36.30"), "vllm-decode": line.format("38.20")}) == []
    limited = kv_problems({"vllm-prefill": line.format("36.30"), "vllm-decode": line.format("21.70")})
    assert len(limited) == 1 and "vllm-decode" in limited[0]  # a HAMi memory limit in vLLM
    a100 = kv_problems({"vllm-prefill": line.format("34.10"), "vllm-decode": line.format("36.90")})
    assert a100 == []  # another GPU of 80 GB (A100) gives about the same memory
