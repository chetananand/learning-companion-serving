import copy

import pytest
import yaml

from control.router.render import (
    load_policy,
    render_agent_router,
    render_epp_config,
    render_objectives,
    tenant_windows,
    write_all,
)

BASE = load_policy()


def policy(**changes):
    p = copy.deepcopy(BASE)
    p.update(changes)
    return p


def plugin_names(cfg):
    return {pl.get("name", pl["type"]) for pl in cfg["plugins"]}


def refs(profile):
    return [r["pluginRef"] for r in profile["plugins"]]


@pytest.mark.parametrize("pd_mode", ["decider", "always", "off"])
@pytest.mark.parametrize("policy_name", ["prefix_then_load", "least_loaded", "random"])
@pytest.mark.parametrize("prefix_index", ["precise", "approx"])
def test_every_reference_resolves(pd_mode, policy_name, prefix_index):
    cfg = render_epp_config(policy(pd_mode=pd_mode, policy=policy_name, prefix_index=prefix_index))
    names = plugin_names(cfg)
    for prof in cfg["schedulingProfiles"]:
        assert set(refs(prof)) <= names, prof["name"]
    fc = cfg["flowControl"]
    assert {fc["usageLimitPolicyPluginRef"], fc["saturationDetector"]["pluginRef"]} <= names
    for band in fc["priorityBands"]:
        assert {band["fairnessPolicyRef"], band["orderingPolicyRef"]} <= names
    for src in cfg["dataLayer"]["sources"]:
        assert src["pluginRef"] in names and {e["pluginRef"] for e in src["extractors"]} <= names
    assert len({pl.get("name", pl["type"]) for pl in cfg["plugins"]}) == len(cfg["plugins"])  # no duplicates


def test_default_is_option_c_with_our_threshold():
    cfg = render_epp_config(BASE)
    decider = next(pl for pl in cfg["plugins"] if pl["type"] == "prefix-based-pd-decider")
    assert decider["parameters"]["nonCachedTokens"] == BASE["pd_non_cached_tokens"] == 2048
    handler = next(pl for pl in cfg["plugins"] if pl["type"] == "disagg-profile-handler")
    assert handler["parameters"]["deciders"]["prefill"] == "prefix-based-pd-decider"
    assert [p["name"] for p in cfg["schedulingProfiles"]] == ["prefill", "decode"]
    assert cfg["featureGates"] == ["flowControl"]


def test_prefill_and_decode_use_different_scorers_and_queue_depth_in_both():
    prefill, decode = render_epp_config(BASE)["schedulingProfiles"]
    assert "queue-scorer" in refs(prefill) and "queue-scorer" in refs(decode)  # L649
    assert "token-load-scorer" in refs(prefill) and "token-load-scorer" not in refs(decode)
    assert "session-affinity-scorer" in refs(decode) and "session-affinity-scorer" not in refs(prefill)
    for prof in (prefill, decode):
        assert refs(prof)[:2] == [f"{prof['name']}-filter", "warm-filter"]  # filters first, warm gate on
        assert refs(prof)[-1] == "picker"


def test_option_a_has_one_profile_without_role_filters():
    cfg = render_epp_config(policy(pd_mode="off"))
    assert [p["name"] for p in cfg["schedulingProfiles"]] == ["default"]
    assert "decode-filter" not in refs(cfg["schedulingProfiles"][0])
    assert any(pl["type"] == "single-profile-handler" for pl in cfg["plugins"])


def test_prefix_index_modes():
    precise = render_epp_config(BASE)
    scorer = next(pl for pl in precise["plugins"] if pl["type"] == "prefix-cache-scorer")
    assert scorer["parameters"]["prefixMatchInfoProducerName"] == "precise-prefix-cache-producer"
    producer = next(pl for pl in precise["plugins"] if pl["type"] == "precise-prefix-cache-producer")
    assert producer["parameters"]["tokenProcessorConfig"]["blockSizeTokens"] == BASE["block_size_tokens"]
    approx = render_epp_config(policy(prefix_index="approx"))
    scorer = next(pl for pl in approx["plugins"] if pl["type"] == "prefix-cache-scorer")
    assert scorer["parameters"]["prefixMatchInfoProducerName"] == "approx-prefix-cache-producer"


def test_warm_gate_ramp_and_staleness():
    cfg = render_epp_config(BASE)
    by_name = {pl.get("name", pl["type"]): pl for pl in cfg["plugins"]}
    assert by_name["warm-filter"]["parameters"] == {"matchLabels": {"companion.io/warm": "true"}}
    assert by_name["ramp-scorer"]["parameters"]["weights"]["r10"] == 0.10
    assert by_name["utilization-detector"]["parameters"]["stalenessPolicy"] == "saturated"


def test_objectives_put_interactive_above_batch():
    objs = {o["metadata"]["name"]: o["spec"]["priority"] for o in render_objectives(BASE)}
    assert objs["interactive"] > objs["batch"]


def test_agent_router_tenant_windows():
    windows = tenant_windows(BASE)
    assert {"owner", "sweep", "noisy", "load-1", "load-32"} <= set(windows) and "load-33" not in windows
    route, btp = render_agent_router(BASE)
    costs = {c["metadataKey"]: c["type"] for c in route["spec"]["llmRequestCosts"]}
    assert costs == {"llm_total_token": "TotalToken", "llm_input_token": "InputToken",
                     "llm_cached_input_token": "CachedInputToken", "llm_output_token": "OutputToken"}
    assert route["spec"]["rules"][0]["backendRefs"][0]["kind"] == "InferencePool"
    rules = btp["spec"]["rateLimit"]["global"]["rules"]
    assert len(rules) == 2 * len(windows)
    noisy_tokens = next(r for r in rules if r["clientSelectors"][0]["headers"][0]["value"] == "noisy" and "cost" in r)
    assert noisy_tokens["limit"]["requests"] == 60000
    assert noisy_tokens["cost"]["response"]["metadata"]["key"] == "llm_total_token"


def test_write_all(tmp_path):
    paths = write_all(__import__("control.router.render", fromlist=["POLICY_FILE"]).POLICY_FILE, tmp_path)
    assert {p.name for p in paths} == {"epp-config.yaml", "router-values.yaml", "inference-objectives.yaml",
                                       "agent-router.yaml"}
    values = yaml.safe_load((tmp_path / "router-values.yaml").read_text())
    inner = yaml.safe_load(values["router"]["epp"]["pluginsCustomConfig"]["companion-plugins.yaml"])
    assert inner["kind"] == "EndpointPickerConfig"
    assert values["router"]["inferencePool"]["failureMode"] == "FailClose"  # no bypass of admission
    assert values["router"]["tokenizer"]["enabled"] is True
    assert values["router"]["modelServers"]["matchLabels"] == {"app.kubernetes.io/part-of": "companion-vllm"}


def test_bad_policy_is_rejected(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text(yaml.safe_dump(policy(policy="p2c")))
    with pytest.raises(ValueError):
        load_policy(bad)


def test_overrides_for_experiment_variants(tmp_path):
    from control.router.render import apply_overrides

    p = apply_overrides(BASE, ["pd_mode=off", "prefix_index=approx", "flow_control.ordering=edf-ordering-policy",
                               "pd_non_cached_tokens=512"])
    assert (p["pd_mode"], p["prefix_index"], p["flow_control"]["ordering"], p["pd_non_cached_tokens"]) == (
        "off", "approx", "edf-ordering-policy", 512)
    assert BASE["pd_mode"] == "decider"  # the base policy does not change
    for bad in (["pd_mode=maybe"], ["no_such_key=1"], ["flow_control.nope=1"], ["pd_mode"]):
        with pytest.raises(ValueError):
            apply_overrides(BASE, bad)
    paths = write_all(__import__("control.router.render", fromlist=["POLICY_FILE"]).POLICY_FILE, tmp_path,
                      ["pd_mode=off"])
    epp = yaml.safe_load((tmp_path / "epp-config.yaml").read_text().split("\n", 1)[1])
    assert [p["name"] for p in epp["schedulingProfiles"]] == ["default"] and len(paths) == 4


def test_the_pool_name_is_the_router_release_name():
    """The llm-d router chart names the InferencePool after the Helm release (G0, 2026-09-28)."""
    import re
    from pathlib import Path

    from control.router.render import load_policy

    install = (Path(__file__).resolve().parents[3] / "cluster" / "install.sh").read_text()
    release = re.search(r"helm upgrade -i (\S+) oci://ghcr.io/llm-d/charts/llm-d-router-gateway", install).group(1)
    assert load_policy()["pool"] == release


def test_the_route_waits_300_s_for_a_stream():
    """Session 1: the AI Gateway default (60 s) cut long streams under load."""
    route, _ = render_agent_router(BASE)
    assert route["spec"]["rules"][0]["timeouts"] == {"request": "300s"}
