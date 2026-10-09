"""Render our router policy into llm-d and Agent Router config (system design sections 6.2 to 6.5).

Plugin types and fields follow llm-d-router v0.11.0 (apix/config/v1alpha1, deploy/config) and the
Envoy AI Gateway v1.1.0 token rate-limit example.

Usage: python -m control.router.render [--policy control/router/policy.yaml] [--out cluster/manifests/router/generated]
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

POLICY_FILE = Path(__file__).with_name("policy.yaml")
POLICIES = ("prefix_then_load", "least_loaded", "random")
PD_MODES = ("decider", "always", "off")


def _validate(policy: dict[str, Any]) -> None:
    if policy["policy"] not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    if policy["pd_mode"] not in PD_MODES:
        raise ValueError(f"pd_mode must be one of {PD_MODES}")
    if policy["prefix_index"] not in ("precise", "approx"):
        raise ValueError("prefix_index must be precise or approx")


def load_policy(path: Path = POLICY_FILE) -> dict[str, Any]:
    policy = yaml.safe_load(path.read_text())
    _validate(policy)
    return policy


def _plugin(type_: str, name: str | None = None, **parameters: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {"type": type_}
    if name:
        spec["name"] = name
    if parameters:
        spec["parameters"] = parameters
    return spec


def _ref(name: str, weight: float | None = None) -> dict[str, Any]:
    return {"pluginRef": name} if weight is None else {"pluginRef": name, "weight": float(weight)}


def render_epp_config(p: dict[str, Any]) -> dict[str, Any]:
    """The llm-d EndpointPickerConfig for our pool."""
    fc = p["flow_control"]
    sat = fc["saturation"]
    plugins: list[dict[str, Any]] = [_plugin("endpoint-notification-source")]
    sources: list[dict[str, Any]] = []
    extractors: list[dict[str, Any]] = [{"pluginRef": "label-producer"}]

    # Prefix index: exact (KV events) or approximate (request history).
    if p["prefix_index"] == "precise":
        plugins += [
            _plugin("token-producer", modelName=p["model_name"], vllm={"url": p["render_service_url"]}),
            _plugin("precise-prefix-cache-producer",
                    tokenProcessorConfig={"blockSizeTokens": p["block_size_tokens"]},
                    indexerConfig={"kvBlockIndexConfig": {"enableMetrics": True}},
                    kvEventsConfig={"topicFilter": p["kv_events"]["topic_filter"], "concurrency": 8,
                                    "discoverPods": True,
                                    "podDiscoveryConfig": {"socketPort": p["kv_events"]["socket_port"],
                                                           "replaySocketPort": p["kv_events"]["replay_socket_port"]}}),
        ]
        extractors.insert(0, {"pluginRef": "precise-prefix-cache-producer"})
        prefix_producer = "precise-prefix-cache-producer"
    else:
        plugins.append(_plugin("approx-prefix-cache-producer", maxPrefixTokensToMatch=16384,
                               lruCapacityPerServer=31250))
        prefix_producer = "approx-prefix-cache-producer"
    sources.append({"pluginRef": "endpoint-notification-source", "extractors": extractors})

    # Scorers, filters, the warm gate, and the recovery ramp.
    warm = p["warm"]
    plugins += [
        _plugin("prefix-cache-scorer", prefixMatchInfoProducerName=prefix_producer),
        _plugin("queue-scorer"),
        _plugin("kv-cache-utilization-scorer"),
        _plugin("token-load-scorer"),
        _plugin("session-affinity-scorer", strategy="session_id"),
        _plugin("label-producer", labels=[{"label": warm["ramp_label"], "attributeKey": "ramp"}]),
        _plugin("endpoint-attribute-weight-scorer", "ramp-scorer", attributeKey="ramp", producer="label-producer",
                weights=dict(warm["ramp_weights"])),
        _plugin("label-selector-filter", "warm-filter", matchLabels={warm["label"]: "true"}),
        _plugin("random-picker" if p["policy"] == "random" else "max-score-picker", "picker"),
    ]

    def profile_plugins(role: str) -> list[dict[str, Any]]:
        w = p["weights"][role]
        refs = [_ref(f"{role}-filter")] if p["pd_mode"] != "off" else []
        refs.append(_ref("warm-filter"))
        if p["policy"] == "prefix_then_load":
            refs.append(_ref("prefix-cache-scorer", w["prefix"]))
            if role == "decode":
                refs.append(_ref("session-affinity-scorer", w["session"]))
            else:
                refs.append(_ref("token-load-scorer", w["token_load"]))
        if p["policy"] in ("prefix_then_load", "least_loaded"):
            refs.append(_ref("queue-scorer", w["queue"]))  # queue depth is a scorer in both pools (L649)
            if role == "decode" or p["policy"] == "least_loaded":
                refs.append(_ref("kv-cache-utilization-scorer", p["weights"]["decode"]["kv_util"]))
        refs.append(_ref("ramp-scorer", w["ramp"]))
        refs.append(_ref("picker"))
        return refs

    if p["pd_mode"] == "off":
        plugins.append(_plugin("single-profile-handler"))
        profiles = [{"name": "default", "plugins": profile_plugins("decode")}]
    else:
        decider = "prefix-based-pd-decider" if p["pd_mode"] == "decider" else "always-disagg-pd-decider"
        plugins += [_plugin("prefill-filter"), _plugin("decode-filter"),
                    _plugin("disagg-profile-handler", deciders={"prefill": decider})]
        if p["pd_mode"] == "decider":
            plugins.append(_plugin("prefix-based-pd-decider", nonCachedTokens=p["pd_non_cached_tokens"]))
        else:
            plugins.append(_plugin("always-disagg-pd-decider"))
        profiles = [{"name": "prefill", "plugins": profile_plugins("prefill")},
                    {"name": "decode", "plugins": profile_plugins("decode")}]

    # Flow control: priority bands, tenant fairness, TTLs, saturation, usage limits.
    plugins += [
        _plugin(fc["fairness"]),
        _plugin(fc["ordering"]),
        _plugin(fc["usage_limit"], **fc.get("usage_limit_parameters", {})),
        _plugin("utilization-detector", queueDepthThreshold=sat["queue_depth_threshold"],
                kvCacheUtilThreshold=sat["kv_cache_util_threshold"],
                metricsStalenessThreshold=sat["metrics_staleness_threshold"],
                stalenessPolicy=sat["staleness_policy"]),
    ]
    flow_control = {
        "defaultRequestTTL": fc["default_request_ttl"],
        "priorityBands": [{"priority": b["priority"], "defaultRequestTTL": b["ttl"],
                           "fairnessPolicyRef": fc["fairness"], "orderingPolicyRef": fc["ordering"]}
                          for b in fc["bands"]],
        "usageLimitPolicyPluginRef": fc["usage_limit"],
        "saturationDetector": {"pluginRef": "utilization-detector"},
    }
    return {
        "apiVersion": "llm-d.ai/v1alpha1",
        "kind": "EndpointPickerConfig",
        "featureGates": ["flowControl"],
        "plugins": plugins,
        "dataLayer": {"sources": sources},  # metrics-data-source and core-metrics-extractor are injected
        "schedulingProfiles": profiles,
        "flowControl": flow_control,
    }


def render_objectives(p: dict[str, Any]) -> list[dict[str, Any]]:
    """One InferenceObjective for each band. `edge` sends its name in x-llm-d-inference-objective."""
    return [{
        "apiVersion": "llm-d.ai/v1alpha2",
        "kind": "InferenceObjective",
        "metadata": {"name": b["objective"], "namespace": p["namespace"]},
        "spec": {"priority": b["priority"], "poolRef": {"group": "inference.networking.k8s.io", "name": p["pool"]}},
    } for b in p["flow_control"]["bands"]]


def render_router_values(p: dict[str, Any], epp_config_text: str) -> dict[str, Any]:
    """Helm values for the llm-d router chart (config/charts/llm-d-router-gateway, v0.11.0)."""
    chart = p["router_chart"]
    values: dict[str, Any] = {
        "router": {
            "epp": {
                "replicas": 1,  # flow-control queues live in memory; the llm-d guides run one replica
                "image": {"registry": "ghcr.io/llm-d", "repository": "llm-d-router-endpoint-picker",
                          "tag": chart["epp_image_tag"], "pullPolicy": "IfNotPresent"},
                # label-producer (warm gate, ramp) is an Alpha plugin: the EPP refuses it without this flag (G0).
                "flags": {"ha-enable-leader-election": False, "allow-experimental-plugins": True},
                "pluginsConfigFile": "companion-plugins.yaml",
                "pluginsCustomConfig": {"companion-plugins.yaml": epp_config_text},
                # G0: node 2 has 28 vCPUs, and the requests must leave room for a load Job (2 vCPUs).
                "resources": {"requests": {"cpu": "1", "memory": "4Gi"}, "limits": {"memory": "8Gi"}},
            },
            "monitoring": {"prometheus": {"enabled": True}},
            "inferencePool": {"create": True, "failureMode": chart["failure_mode"]},
            "modelServers": {"type": "vllm", "protocol": "http", "targetPorts": [{"number": chart["target_port"]}],
                             "matchLabels": dict(chart["model_server_labels"])},
        },
        "provider": {"name": "none"},
        "httpRoute": {"create": False},  # the AIGatewayRoute (render_agent_router) routes to the pool
    }
    if p["prefix_index"] == "precise":
        # The chart default requests 4 vCPUs and 8 GiB for this sidecar (G0): a tokenizer needs much less.
        values["router"]["tokenizer"] = {"enabled": True, "flavor": "python", "modelName": p["model_name"],
                                         "resources": {"requests": {"cpu": "1", "memory": "3Gi"},
                                                       "limits": {"memory": "8Gi"}},
                                         # The EPP renders with the served name. Without it: 404, no token IDs,
                                         # no prefix match, and the P/D decider never splits (G0).
                                         "extraArgs": ["--served-model-name", p["served_model_name"], p["model_name"]]}
    return values


def tenant_windows(p: dict[str, Any]) -> dict[str, dict[str, int]]:
    windows = {name: dict(limits) for name, limits in p["tenants"].items()}
    load = p["load_tenants"]
    for i in range(1, load["count"] + 1):
        windows[f"load-{i}"] = {"tokens_per_minute": load["tokens_per_minute"],
                                "requests_per_minute": load["requests_per_minute"]}
    return windows


def render_agent_router(p: dict[str, Any]) -> list[dict[str, Any]]:
    """AIGatewayRoute (token costs, InferencePool backend) and the tenant windows. VERIFY at G0."""
    route = {
        "apiVersion": "aigateway.envoyproxy.io/v1beta1",
        "kind": "AIGatewayRoute",
        "metadata": {"name": "companion", "namespace": p["namespace"]},
        "spec": {
            "parentRefs": [{"name": p["gateway"], "kind": "Gateway", "group": "gateway.networking.k8s.io"}],
            "rules": [{"matches": [{"headers": [{"type": "Exact", "name": "x-ai-eg-model",
                                                 "value": p["served_model_name"]}]}],
                       "backendRefs": [{"group": "inference.networking.k8s.io", "kind": "InferencePool",
                                        "name": p["pool"]}],
                       # Session 1: with no timeout, the AI Gateway stops a request (the whole stream) at 60 s.
                       # E1 at 24K tokens lost 7 streams at 60 to 66 s. 300 s = the replay client timeout.
                       "timeouts": {"request": "300s"}}],
            # llm_total_token feeds the tenant windows. The other keys feed the access log and the hop records.
            "llmRequestCosts": [{"metadataKey": "llm_total_token", "type": "TotalToken"},
                                {"metadataKey": "llm_input_token", "type": "InputToken"},
                                {"metadataKey": "llm_cached_input_token", "type": "CachedInputToken"},
                                {"metadataKey": "llm_output_token", "type": "OutputToken"}],
        },
    }
    rules: list[dict[str, Any]] = []
    for tenant, limits in tenant_windows(p).items():
        selector = [{"headers": [{"name": "x-tenant-id", "type": "Exact", "value": tenant}]}]
        rules.append({"clientSelectors": selector,
                      "limit": {"requests": limits["tokens_per_minute"], "unit": "Minute"},
                      "cost": {"request": {"from": "Number", "number": 0},
                               "response": {"from": "Metadata",
                                            "metadata": {"namespace": "io.envoy.ai_gateway",
                                                         "key": "llm_total_token"}}}})
        rules.append({"clientSelectors": selector,
                      "limit": {"requests": limits["requests_per_minute"], "unit": "Minute"}})
    policy = {
        "apiVersion": "gateway.envoyproxy.io/v1alpha1",
        "kind": "BackendTrafficPolicy",
        "metadata": {"name": "tenant-windows", "namespace": p["namespace"]},
        "spec": {"targetRefs": [{"name": p["gateway"], "kind": "Gateway", "group": "gateway.networking.k8s.io"}],
                 "rateLimit": {"type": "Global", "global": {"rules": rules}}},
    }
    return [route, policy]


class _BlockDumper(yaml.SafeDumper):
    """Write multi-line strings as block literals, so the ConfigMap stays readable."""


def _str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockDumper.add_representer(str, _str_presenter)


def apply_overrides(p: dict[str, Any], sets: list[str]) -> dict[str, Any]:
    """Apply `key=value` overrides for an experiment variant. Dotted keys reach nested values.

    Example: pd_mode=off, prefix_index=approx, flow_control.ordering=edf-ordering-policy
    """
    import copy

    out = copy.deepcopy(p)
    for item in sets:
        key, _, raw = item.partition("=")
        if not key or not _:
            raise ValueError(f"expected key=value, got {item!r}")
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                raise ValueError(f"unknown key {key}")
            node = node[part]
        if parts[-1] not in node:
            raise ValueError(f"unknown key {key}")
        node[parts[-1]] = _parse_value(raw)
    _validate(out)
    return out


def _parse_value(raw: str) -> Any:
    """int, float, true or false, else the string. Not YAML: YAML 1.1 reads "off" as a boolean."""
    if raw in ("true", "false"):
        return raw == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def write_all(policy_path: Path, out_dir: Path, sets: list[str] | None = None) -> list[Path]:
    p = apply_overrides(load_policy(policy_path), sets or [])
    out_dir.mkdir(parents=True, exist_ok=True)
    header = "# GENERATED by control/router/render.py from control/router/policy.yaml. Do not edit.\n"
    epp_text = yaml.dump(render_epp_config(p), Dumper=_BlockDumper, sort_keys=False)
    files = {
        "epp-config.yaml": [render_epp_config(p)],
        "router-values.yaml": [render_router_values(p, epp_text)],
        "inference-objectives.yaml": render_objectives(p),
        "agent-router.yaml": render_agent_router(p),
    }
    written = []
    for name, docs in files.items():
        path = out_dir / name
        path.write_text(header + yaml.dump_all(docs, Dumper=_BlockDumper, sort_keys=False))
        written.append(path)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--policy", type=Path, default=POLICY_FILE)
    ap.add_argument("--out", type=Path, default=Path("cluster/manifests/router/generated"))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="an experiment override, for example pd_mode=off (repeat for more)")
    args = ap.parse_args()
    for path in write_all(args.policy, args.out, args.set):
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
