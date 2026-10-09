"""Tests of the one-node layout (ADR-005, revision 2) on the real renders of the overlays one and one-e9."""

import copy
import re

import pytest

from tools import variant as v

ONE, ONE_E9, T2 = v.render("one"), v.render("one-e9"), v.render("t2")
NODE2_GPU = ("sie-embed", "sie-ocr", "guard-safety", "guard-injection")
H100_MIB, NODE_GIB, NODE_VCPU = 81_559, 900, 104  # gpu_4x_h100_sxm5: the Lambda specs (2026-09-30)


def deps(docs):
    return {d["metadata"]["name"]: d for d in docs if d["kind"] in ("Deployment", "DaemonSet", "StatefulSet")}


def gib(q):
    m = re.fullmatch(r"([0-9.]+)(Gi|Mi)?", str(q))
    return float(m[1]) / (1024 if m[2] == "Mi" else 1) if m[2] else float(m[1]) / 2**30


def cpu(q):
    q = str(q)
    return float(q[:-1]) / 1000 if q.endswith("m") else float(q)


def test_no_pod_asks_for_the_gpu_node():
    for name, d in deps(ONE).items():
        assert d["spec"]["template"]["spec"].get("nodeSelector", {}).get("companion.io/node") != "gpu", name
    assert deps(T2)["vllm-decode"]["spec"]["template"]["spec"]["nodeSelector"] == {"companion.io/node": "gpu"}


@pytest.mark.parametrize("name", ["vllm-prefill", "vllm-decode"])
def test_each_engine_pod_takes_a_whole_gpu_through_hami_without_the_hook(name):
    d = deps(ONE)[name]
    assert "hami.io/webhook" not in d["spec"]["template"]["metadata"]["labels"]
    c = v.container(d)
    assert c["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert c["resources"]["limits"]["nvidia.com/gpucores"] == "100"
    assert c["resources"]["limits"]["nvidia.com/gpumem-percentage"] == "100"
    assert {"name": "CUDA_DISABLE_CONTROL", "value": "true"} in c["env"]
    assert v.container(deps(T2)[name])["args"] == c["args"]  # the same engine settings as the two-node runs


def test_the_node2_gpu_pods_pack_onto_one_h100():
    total = 0
    for name in NODE2_GPU:
        d = deps(ONE)[name]
        assert d["spec"]["template"]["metadata"]["annotations"]["hami.io/gpu-scheduler-policy"] == "binpack"
        total += int(d["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpumem"])
    assert total == 52_000 and total <= H100_MIB


def test_the_pods_fit_the_4x_h100_node_with_three_engine_pods():
    mem_limit, cpu_req = 0.0, 0.0
    for d in deps(ONE).values():
        # E9: at most 3 engine pods (2 in one pool, 1 in the other). The other objects: their replicas.
        n = {"vllm-prefill": 2, "vllm-decode": 1}.get(d["metadata"]["name"], d["spec"].get("replicas", 1))
        for c in d["spec"]["template"]["spec"]["containers"]:
            r = c.get("resources", {})
            mem_limit += n * gib(r.get("limits", {}).get("memory", r.get("requests", {}).get("memory", "0")))
            cpu_req += n * cpu(r.get("requests", {}).get("cpu", "0"))
    # The helm charts (Prometheus, Grafana, KEDA, HAMi, Envoy, the router) are not in this render: keep a margin.
    assert mem_limit <= NODE_GIB - 150, mem_limit  # 2026-09-30: 666 GiB
    assert cpu_req <= NODE_VCPU - 20, cpu_req      # 2026-09-30: 57 vCPUs


def test_keda_limits():
    def maxes(docs):
        return {d["metadata"]["name"]: d["spec"]["maxReplicaCount"] for d in docs if d["kind"] == "ScaledObject"}
    assert maxes(ONE) == {"vllm-prefill": 1, "vllm-decode": 1}  # the must-do items: the t2 engine
    assert maxes(ONE_E9) == {"vllm-prefill": 2, "vllm-decode": 2}  # E9: one pool at a time (cluster/one.sh hold)


def test_the_variants_keep_the_one_node_patches():
    for presets, drops in ((["layout-a"], []), (["layout-a", "devmode"],
                           ["vllm-prefill:--kv-transfer-config", "vllm-decode:--kv-transfer-config"])):
        out = v.apply(copy.deepcopy(ONE), presets, ["vllm-decode:--max-num-seqs=32"], drops, [])
        for d in (d for d in out if d["kind"] == "Deployment"):
            spec = d["spec"]["template"]
            assert spec["spec"]["nodeSelector"] == {"companion.io/node": "control"}
            assert "hami.io/webhook" not in spec["metadata"]["labels"]
            assert {"name": "CUDA_DISABLE_CONTROL", "value": "true"} in v.container(d)["env"]
