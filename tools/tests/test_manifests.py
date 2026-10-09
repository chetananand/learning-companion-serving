"""The G1 fixes (2026-09-28) must hold in every overlay: render each one and check the engine objects."""

from __future__ import annotations

import json

import pytest

from tools.variant import render

LMCACHE_OVERLAYS = ["t2", "t4", "dev", "t2-vllm026"]


def objects(overlay: str) -> dict[tuple[str, str], dict]:
    return {(d["kind"], d["metadata"]["name"]): d for d in render(overlay)}


def args(dep: dict) -> list[str]:
    return next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "modelserver")["args"]


def kv_config(dep: dict) -> dict:
    a = args(dep)
    return json.loads(a[a.index("--kv-transfer-config") + 1])


@pytest.mark.parametrize("overlay", LMCACHE_OVERLAYS)
def test_lmcache_server_binds_the_node_ip_and_uses_isolated_ipc(overlay):
    command = objects(overlay)[("DaemonSet", "lmcache-server")]["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command[command.index("--host") + 1] == "$(HOST_IP)" and "--isolated-ipc" in command


def connectors(cfg: dict) -> list[dict]:
    """The connectors of a KV transfer config: a MultiConnector list, or the one connector."""
    return cfg["kv_connector_extra_config"]["connectors"] if cfg["kv_connector"] == "MultiConnector" else [cfg]


@pytest.mark.parametrize("overlay", LMCACHE_OVERLAYS)
def test_every_lmcache_connector_uses_isolated_ipc(overlay):
    objs = objects(overlay)
    for name in ("vllm-prefill", "vllm-decode"):
        lm = [c for c in connectors(kv_config(objs[("Deployment", name)])) if c["kv_connector"] == "LMCacheMPConnector"]
        assert lm and all(c["kv_connector_extra_config"]["lmcache.mp.isolated_ipc"] is True for c in lm)


@pytest.mark.parametrize("overlay", LMCACHE_OVERLAYS + ["t2-k0"])
def test_the_sidecar_protocol_matches_the_connectors(overlay):
    """shared-storage needs the LMCache tier on both pods; nixlv2 needs NIXL (G0: the hop medium)."""
    objs = objects(overlay)
    decode = objs[("Deployment", "vllm-decode")]
    mode = next(a.split("=", 1)[1] for a in decode["spec"]["template"]["spec"]["initContainers"][0]["args"]
                if a.startswith("--kv-connector="))
    for name in ("vllm-prefill", "vllm-decode"):
        kinds = {c["kv_connector"] for c in connectors(kv_config(objs[("Deployment", name)]))}
        assert ("LMCacheMPConnector" in kinds) if mode == "shared-storage" else ("NixlConnector" in kinds)
    assert mode == ("nixlv2" if overlay == "t2-k0" else "shared-storage")


@pytest.mark.parametrize("overlay", LMCACHE_OVERLAYS + ["t2-k0"])
def test_engine_pods_recreate_and_decode_fits_one_image(overlay):
    objs = objects(overlay)
    for name in ("vllm-prefill", "vllm-decode"):
        dep = objs[("Deployment", name)]
        assert dep["spec"]["strategy"]["type"] == "Recreate"  # one GPU for each pod
        mnbt = next(int(a.split("=")[1]) for a in args(dep) if a.startswith("--max-num-batched-tokens="))
        assert mnbt >= 2496  # vLLM refuses less: one Gemma 4 image item


@pytest.mark.parametrize("overlay", LMCACHE_OVERLAYS)
def test_the_safety_model_is_ready_only_after_one_real_check(overlay):
    """Session 1: a cold first check took 0.99 s, and the edge fails closed at 1.0 s."""
    dep = objects(overlay)[("Deployment", "guard-safety")]
    probe = dep["spec"]["template"]["spec"]["containers"][0]["startupProbe"]
    assert "/v1/chat/completions" in " ".join(probe["exec"]["command"])


@pytest.mark.parametrize("overlay", LMCACHE_OVERLAYS + ["t2-k0"])
def test_node1_gpu_pods_skip_the_hami_webhook(overlay):
    """Node 1 has the NVIDIA device plugin. A pod there that the HAMi webhook mutates stays Pending (fault 24)."""
    for (kind, name), obj in objects(overlay).items():
        if kind not in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
            continue
        spec = obj["spec"]["template"]["spec"]
        gpu = any("nvidia.com/gpu" in (c.get("resources", {}).get("limits") or {}) for c in spec["containers"])
        if gpu and spec.get("nodeSelector", {}).get("companion.io/node") == "gpu":
            assert obj["spec"]["template"]["metadata"]["labels"].get("hami.io/webhook") == "ignore", name


@pytest.mark.parametrize("overlay", LMCACHE_OVERLAYS + ["t2-k0"])
def test_the_store_barrier_fronts_the_prefill_engine(overlay):
    """Session 1: the barrier takes port 8000 of the prefill pod, and vLLM moves to 8200. It is off in K0."""
    dep = objects(overlay)[("Deployment", "vllm-prefill")]
    containers = {c["name"]: c for c in dep["spec"]["template"]["spec"]["containers"]}
    assert "--port=8200" in args(dep) and containers["modelserver"]["ports"][0]["containerPort"] == 8200
    barrier = containers["store-barrier"]
    assert barrier["ports"] == [{"name": "barrier", "containerPort": 8000}]
    env = {e["name"]: e.get("value") for e in barrier["env"]}
    assert env["BARRIER_UPSTREAM"] == "http://127.0.0.1:8200"
    assert env["BARRIER_LMCACHE_METRICS"] == ("" if overlay == "t2-k0" else "http://$(HOST_IP):8080/metrics")


def test_dev_engine_pods_on_node2_use_hami():
    objs = objects("dev")
    for name in ("vllm-prefill", "vllm-decode"):
        assert "hami.io/webhook" not in objs[("Deployment", name)]["spec"]["template"]["metadata"]["labels"]


@pytest.mark.parametrize("overlay", ["t2", "t4", "t2-vllm026"])
def test_the_cpu_tier_holds_more_tokens_than_the_gpu_cache(overlay):
    """About 865 KB for each token in LMCache: 250 GiB at 90% is about 280K tokens (GPU cache: 173,657)."""
    c = objects(overlay)[("DaemonSet", "lmcache-server")]["spec"]["template"]["spec"]["containers"][0]
    size = int(c["command"][c["command"].index("--l1-size-gb") + 1])
    mark = float(c["command"][c["command"].index("--eviction-trigger-watermark") + 1])
    assert size * mark * 2**30 / 865e3 > 173_657 * 1.5
    assert int(c["resources"]["requests"]["memory"].removesuffix("Gi")) >= size + 10


@pytest.mark.parametrize("overlay", ["t2", "t4"])
def test_the_gateway_buffers_a_long_prompt(overlay):
    """Session 1: the Envoy default buffer (32 KiB) gave 413 for prompts of about 8K tokens or more."""
    ctp = objects(overlay)[("ClientTrafficPolicy", "companion-gateway-buffer")]
    assert ctp["spec"]["targetRefs"][0]["name"] == "companion-gateway"
    assert ctp["spec"]["connection"]["bufferLimit"] == "4Mi"


def test_only_the_load_generator_reaches_the_gateway_directly():
    """E1 and E9 skip edge. The app pods (web-egress) must not reach the gateway namespace (session 1)."""
    objs = objects("t2")
    lg = objs[("NetworkPolicy", "loadgen-gateway-egress")]["spec"]
    assert lg["podSelector"] == {"matchLabels": {"app": "loadgen"}}
    names = [t["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] for t in lg["egress"][0]["to"]]
    assert names == ["envoy-gateway-system"]
    web = objs[("NetworkPolicy", "web-egress")]["spec"]
    assert "envoy-gateway-system" not in str(web)
    lmcache = lg["egress"][1]  # E4: the LMCache metrics on the node IP
    assert lmcache["ports"] == [{"port": 8080, "protocol": "TCP"}]
    assert lmcache["to"][0]["ipBlock"]["cidr"] == "172.16.0.0/12"


def test_the_planner_uses_the_prefill_capacity_that_e1_measured():
    """E9 needs the measured value: about 10,500 tokens/s for one prefill pod (E1, 2026-09-29)."""
    from pathlib import Path

    import yaml
    rules = yaml.safe_load(Path(__file__).resolve().parents[2].joinpath(
        "cluster/manifests/base/monitoring/rules.yaml").read_text())
    exprs = [r["expr"] for g in rules["spec"]["groups"] for r in g["rules"]
             if r.get("record") == "companion:planner_desired_replicas" and r["labels"]["pool"] == "prefill"]
    assert exprs and "0.7 * 10500" in exprs[0]


def test_the_warm_controller_keeps_its_engine_pod_label():
    """Fault 34 (2026-09-30): without honorLabels, Prometheus renames the pod label of the warm controller."""
    from pathlib import Path

    import yaml
    docs = yaml.safe_load_all(Path(__file__).resolve().parents[2].joinpath(
        "cluster/manifests/base/monitoring/monitors.yaml").read_text())
    warm = next(d for d in docs if d and d["metadata"]["name"] == "warm-controller")
    assert all(e.get("honorLabels") is True for e in warm["spec"]["endpoints"])


def test_the_hop_alert_watches_the_lmcache_hop():
    """ADR-002 revision 2: the hop goes through the LMCache tier, so the alert must watch the store barrier (R-03)."""
    from pathlib import Path

    import yaml
    rules = yaml.safe_load(Path(__file__).resolve().parents[2].joinpath(
        "cluster/manifests/base/monitoring/rules.yaml").read_text())
    alerts = {r["alert"]: r for g in rules["spec"]["groups"] for r in g["rules"] if "alert" in r}
    expr = alerts["HopFailures"]["expr"]
    assert "barrier_timeouts_total" in expr and "barrier_fail_open_total" in expr
