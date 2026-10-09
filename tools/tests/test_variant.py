"""Tests of the engine variants on the real t2 render."""

import json

import pytest

from tools import variant as v

DOCS = v.render("t2")


def engine(presets=(), sets=(), drops=(), envs=()):
    import copy

    out = v.apply(copy.deepcopy(DOCS), list(presets), list(sets), list(drops), list(envs))
    return {d["metadata"]["name"]: d for d in out if d["kind"] == "Deployment"}, out


def args(dep):
    return v.container(dep)["args"]


def test_only_engine_objects():
    _, out = engine()
    assert {(d["kind"], d["metadata"]["name"]) for d in out} == {
        ("Deployment", "vllm-prefill"), ("Deployment", "vllm-decode"), ("Service", "vllm-prefill"),
        ("Service", "vllm-decode"), ("DaemonSet", "lmcache-server")}


def test_layout_a_makes_two_equal_replicas_with_the_cpu_tier_only():
    deps, _ = engine(["layout-a"])
    for dep in deps.values():
        a = args(dep)
        kv = json.loads(a[a.index("--kv-transfer-config") + 1])
        assert kv["kv_connector"] == "LMCacheMPConnector" and kv["kv_role"] == "kv_both"
        assert "--max-num-batched-tokens=8192" in a and "--max-num-seqs=16" in a
        assert dep["spec"]["template"]["metadata"]["labels"]["llm-d.ai/role"] == "prefill-decode"


def test_single_changes_and_devmode():
    deps, _ = engine(["devmode"], sets=["vllm-decode:--max-num-seqs=32", "vllm-prefill:--kv-cache-dtype=fp8"],
                     drops=["vllm-prefill:--enable-prefix-caching"], envs=["vllm-decode:FOO=1"])
    assert "--max-num-seqs=32" in args(deps["vllm-decode"]) and "--max-num-seqs=24" not in args(deps["vllm-decode"])
    assert "--kv-cache-dtype=fp8" in args(deps["vllm-prefill"])
    assert "--enable-prefix-caching" not in args(deps["vllm-prefill"])
    envs = {e["name"]: e.get("value") for e in v.container(deps["vllm-decode"])["env"]}
    assert envs["VLLM_SERVER_DEV_MODE"] == "1" and envs["FOO"] == "1"


def test_stale_b_puts_the_proxy_between_the_sidecar_and_vllm():
    deps, _ = engine(["stale-b"])
    spec = deps["vllm-decode"]["spec"]["template"]["spec"]
    sidecar = next(c for c in spec["initContainers"] if c["name"] == "routing-proxy")
    assert "--model-server-port=8300" in sidecar["args"]
    proxy = next(c for c in spec["containers"] if c["name"] == "stale-proxy")
    assert {e["name"]: e["value"] for e in proxy["env"]}["STALE_UPSTREAM"] == "http://127.0.0.1:8200"
    assert "stale-proxy" not in str(deps["vllm-prefill"])


def test_bad_target():
    with pytest.raises(ValueError):
        engine(sets=["edge:--x=1"])


def test_sidecar_flag_for_e5():
    import copy

    out = v.apply(copy.deepcopy(DOCS), [], [], [], [], ["vllm-decode:--decode-chunk-size=512"])
    dep = next(d for d in out if d["kind"] == "Deployment" and d["metadata"]["name"] == "vllm-decode")
    sidecar = next(c for c in dep["spec"]["template"]["spec"]["initContainers"] if c["name"] == "routing-proxy")
    assert "--decode-chunk-size=512" in sidecar["args"]
    with pytest.raises(ValueError):
        v.apply(copy.deepcopy(DOCS), [], [], [], [], ["vllm-prefill:--decode-chunk-size=512"])


def test_the_base_hop_goes_through_lmcache_and_nixl_hop_restores_nixl():
    base, _ = engine()
    sidecar = base["vllm-decode"]["spec"]["template"]["spec"]["initContainers"][0]
    assert "--kv-connector=shared-storage" in sidecar["args"]  # G0: the hop through the LMCache tier
    for name in ("vllm-prefill", "vllm-decode"):
        a = args(base[name])
        assert json.loads(a[a.index("--kv-transfer-config") + 1])["kv_connector"] == "LMCacheMPConnector"
    deps, _ = engine(["nixl-hop"])
    a = args(deps["vllm-decode"])
    kv = json.loads(a[a.index("--kv-transfer-config") + 1])
    assert kv["kv_connector"] == "MultiConnector" and kv["kv_role"] == "kv_consumer"
    names = [c["kv_connector"] for c in kv["kv_connector_extra_config"]["connectors"]]
    assert names == ["NixlConnector", "LMCacheMPConnector"]
    assert "--kv-connector=nixlv2" in deps["vllm-decode"]["spec"]["template"]["spec"]["initContainers"][0]["args"]
    barrier = next(c for c in deps["vllm-prefill"]["spec"]["template"]["spec"]["containers"]
                   if c["name"] == "store-barrier")
    assert {"name": "BARRIER_LMCACHE_METRICS", "value": ""} in barrier["env"]  # a NIXL hop: no hold


def test_drop_removes_a_flag_and_its_separate_value():
    from tools.variant import drop_arg
    args = ["$(MODEL)", "--port=8200", "--kv-transfer-config", '{"kv_connector":"LMCacheMPConnector"}',
            "--enable-prefix-caching"]
    drop_arg(args, "--kv-transfer-config")
    assert args == ["$(MODEL)", "--port=8200", "--enable-prefix-caching"]
    drop_arg(args, "--enable-prefix-caching")  # a flag with no value
    drop_arg(args, "--port")
    assert args == ["$(MODEL)"]


def test_e7b_renders_layout_a_with_no_kv_connector():
    deps, _ = engine(["layout-a", "devmode"],
                     drops=["vllm-prefill:--kv-transfer-config", "vllm-decode:--kv-transfer-config"])
    for name in ("vllm-prefill", "vllm-decode"):
        a = args(deps[name])
        assert "--kv-transfer-config" not in a and not any(x.startswith('{"kv_connector"') for x in a)
        assert "--kv-events-config" in a  # the precise prefix index still gets the KV events
