"""Engine variants for the experiments: change the vLLM pods of a rendered overlay, print the engine objects.

It renders an overlay with `kubectl kustomize`, changes the args, the env, and the labels of the engine
Deployments, and prints only the engine objects (the vLLM Deployments and Services, and the LMCache
DaemonSet) for `kubectl apply`. The router side of a variant comes from `render.py --set`.

Presets (06-experiments.md):
  layout-a   E3, E8, E11: two plain replicas (role prefill-decode), the same batch settings on both pods,
             and the LMCache tier only (no NIXL hop). Use it with the router override pd_mode=off.
  devmode    E7: VLLM_SERVER_DEV_MODE=1 on both pods, for POST /reset_prefix_cache (vLLM v0.30.0 source).
  stale-b    E11: the stale-metrics proxy in front of vLLM in vllm-decode (pod B).
  nixl-hop   E4: the P/D hop with NIXL (MultiConnector NIXL + LMCache, sidecar --kv-connector=nixlv2), the
             design before G0. The base hop goes through the shared LMCache tier (sidecar shared-storage):
             at G0, NIXL between two pods on one node ran over TCP (4.9 s against 0.9 s for 5.5K tokens).
Single changes:
  --arg vllm-decode:--max-num-seqs=32     set a flag (replace it, or add it)
  --drop vllm-prefill:--enable-prefix-caching
  --env vllm-decode:NAME=VALUE
  --sidecar-arg vllm-decode:--decode-chunk-size=512   (E5: a flag of the llm-d routing sidecar)

Usage: uv run python tools/variant.py --overlay t2 --preset layout-a | kubectl apply --server-side -f -
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ("vllm-prefill", "vllm-decode")
LMCACHE_ONLY = {"kv_connector": "LMCacheMPConnector", "kv_role": "kv_both",
                "kv_connector_extra_config": {"lmcache.mp.host": "tcp://$(HOST_IP)", "lmcache.mp.port": 5555,
                                              "lmcache.mp.isolated_ipc": True}}
STALE_PORT = 8300
NIXL_PREFILL = '{"kv_connector":"MultiConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"connectors":[{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_buffer_device":"cuda","kv_connector_extra_config":{"backends":["UCX"]}},{"kv_connector":"LMCacheMPConnector","kv_role":"kv_both","kv_connector_extra_config":{"lmcache.mp.host":"tcp://$(HOST_IP)","lmcache.mp.port":5555,"lmcache.mp.isolated_ipc":true}}]}}'
NIXL_DECODE = '{"kv_connector":"MultiConnector","kv_role":"kv_consumer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"connectors":[{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_buffer_device":"cuda","kv_connector_extra_config":{"backends":["UCX"]}},{"kv_connector":"LMCacheMPConnector","kv_role":"kv_both","kv_connector_extra_config":{"lmcache.mp.host":"tcp://$(HOST_IP)","lmcache.mp.port":5555,"lmcache.mp.isolated_ipc":true}}]}}'


def render(overlay: str) -> list[dict[str, Any]]:
    out = subprocess.run(["kubectl", "kustomize", "--load-restrictor", "LoadRestrictionsNone",
                          str(ROOT / "cluster" / "manifests" / "overlays" / overlay)],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def is_engine(doc: dict[str, Any]) -> bool:
    name, kind = doc["metadata"]["name"], doc["kind"]
    return (kind == "Deployment" and name in ENGINE) or (kind == "Service" and name.startswith("vllm-")) or (
        kind == "DaemonSet" and name == "lmcache-server")


def container(dep: dict[str, Any], name: str = "modelserver") -> dict[str, Any]:
    return next(c for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == name)


def set_arg(args: list[str], flag: str, value: str | None) -> None:
    """Replace `--flag=...` (or the value after `--flag`), else add it."""
    for i, a in enumerate(args):
        if a == flag and value is not None and i + 1 < len(args):
            args[i + 1] = value
            return
        if a == flag or a.startswith(flag + "="):
            args[i] = flag if value is None else f"{flag}={value}"
            return
    args.append(flag if value is None else f"{flag}={value}")


def drop_arg(args: list[str], flag: str) -> None:
    """Remove `--flag=...`, or `--flag` and its separate value (Wednesday, E7: --kv-transfer-config '{...}')."""
    for i, a in enumerate(args):
        if a.startswith(flag + "="):
            del args[i]
            return
        if a == flag:
            has_value = i + 1 < len(args) and not args[i + 1].startswith("-")
            del args[i:i + 2 if has_value else i + 1]
            return


def set_env(c: dict[str, Any], name: str, value: str) -> None:
    env = c.setdefault("env", [])
    for e in env:
        if e["name"] == name:
            e.clear()
            e.update({"name": name, "value": value})
            return
    env.append({"name": name, "value": value})


def preset_layout_a(deps: dict[str, dict[str, Any]]) -> None:
    for dep in deps.values():
        labels = dep["spec"]["template"]["metadata"]["labels"]
        labels["llm-d.ai/role"] = "prefill-decode"
        args = container(dep)["args"]
        set_arg(args, "--kv-transfer-config", json.dumps(LMCACHE_ONLY, separators=(",", ":")))
        set_arg(args, "--max-num-batched-tokens", "8192")
        set_arg(args, "--max-num-seqs", "16")


def preset_devmode(deps: dict[str, dict[str, Any]]) -> None:
    for dep in deps.values():
        set_env(container(dep), "VLLM_SERVER_DEV_MODE", "1")


def preset_stale_b(deps: dict[str, dict[str, Any]]) -> None:
    dep = deps["vllm-decode"]
    spec = dep["spec"]["template"]["spec"]
    sidecar = next(c for c in spec.get("initContainers", []) if c["name"] == "routing-proxy")
    set_arg(sidecar["args"], "--model-server-port", str(STALE_PORT))
    spec["containers"].append({
        "name": "stale-proxy", "image": "companion/edge:dev", "imagePullPolicy": "Never",  # has control/
        "command": ["python", "-m", "control.stale.proxy"],
        "env": [{"name": "STALE_LISTEN_PORT", "value": str(STALE_PORT)},
                {"name": "STALE_UPSTREAM", "value": "http://127.0.0.1:8200"}],
        "ports": [{"name": "stale", "containerPort": STALE_PORT}],
        "resources": {"requests": {"cpu": "250m", "memory": "128Mi"}, "limits": {"memory": "512Mi"}},
    })


def preset_nixl_hop(deps: dict[str, dict[str, Any]]) -> None:
    set_arg(container(deps["vllm-prefill"])["args"], "--kv-transfer-config", NIXL_PREFILL)
    set_arg(container(deps["vllm-decode"])["args"], "--kv-transfer-config", NIXL_DECODE)
    spec = deps["vllm-decode"]["spec"]["template"]["spec"]
    sidecar = next(c for c in spec.get("initContainers", []) if c["name"] == "routing-proxy")
    set_arg(sidecar["args"], "--kv-connector", "nixlv2")
    set_env(container(deps["vllm-prefill"], "store-barrier"), "BARRIER_LMCACHE_METRICS", "")  # a NIXL hop: no hold


PRESETS = {"layout-a": preset_layout_a, "devmode": preset_devmode, "stale-b": preset_stale_b,
           "nixl-hop": preset_nixl_hop}


def split_target(item: str) -> tuple[str, str]:
    dep, _, rest = item.partition(":")
    if dep not in ENGINE or not rest:
        raise ValueError(f"expected vllm-prefill|vllm-decode:..., got {item!r}")
    return dep, rest


def apply(docs: list[dict[str, Any]], presets: list[str], sets: list[str], drops: list[str],
          envs: list[str], sidecar_sets: list[str] | None = None) -> list[dict[str, Any]]:
    engine = [d for d in docs if is_engine(d)]
    deps = {d["metadata"]["name"]: d for d in engine if d["kind"] == "Deployment"}
    for name in presets:
        PRESETS[name](deps)
    for item in sets:
        dep, rest = split_target(item)
        flag, _, value = rest.partition("=")
        set_arg(container(deps[dep])["args"], flag, value or None)
    for item in drops:
        dep, flag = split_target(item)
        drop_arg(container(deps[dep])["args"], flag)
    for item in envs:
        dep, rest = split_target(item)
        name, _, value = rest.partition("=")
        set_env(container(deps[dep]), name, value)
    for item in sidecar_sets or []:
        dep, rest = split_target(item)
        spec = deps[dep]["spec"]["template"]["spec"]
        sidecar = next((c for c in spec.get("initContainers", []) if c["name"] == "routing-proxy"), None)
        if sidecar is None:
            raise ValueError(f"{dep} has no routing sidecar")
        flag, _, value = rest.partition("=")
        set_arg(sidecar["args"], flag, value or None)
    return engine


def main() -> int:
    ap = argparse.ArgumentParser(description="Engine variants for the experiments")
    ap.add_argument("--overlay", default="t2")
    ap.add_argument("--preset", action="append", default=[], choices=sorted(PRESETS))
    ap.add_argument("--arg", action="append", default=[])
    ap.add_argument("--drop", action="append", default=[])
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--sidecar-arg", action="append", default=[])
    a = ap.parse_args()
    docs = apply(render(a.overlay), a.preset, a.arg, a.drop, a.env, a.sidecar_arg)
    sys.stdout.write(yaml.safe_dump_all(docs, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
