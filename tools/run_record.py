"""Write the run record of an experiment (06-experiments.md, rules 3 and 4) as JSON.

It reads the live cluster with kubectl (through the SSH tunnel) and the repo files: the image and its
digest, the args and the env of each pod (no secret values: `valueFrom` entries show only the name),
the node labels, the router policy, the pinned versions, and the Git commit.
Usage: python3 tools/run_record.py <run-id> [--loadgen "<module and args>"] > metrics/<run-id>/run.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMESPACES = ("companion", "guard", "data", "platform")
NODE_LABELS = ("companion.io/", "kubernetes.io/hostname", "nvidia.com/", "node.kubernetes.io/instance-type")


def kubectl_json(*args: str) -> dict:
    out = subprocess.run(["kubectl", *args, "-o", "json"], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def container_record(c: dict, statuses: dict[str, dict]) -> dict:
    env = {}
    for e in c.get("env", []):
        # Session 2: Kubernetes drops an empty value (the barrier off: BARRIER_LMCACHE_METRICS=""), so an entry
        # can have neither value nor valueFrom.
        env[e["name"]] = f"<from {next(iter(e['valueFrom']), 'unknown')}>" if "valueFrom" in e else e.get("value", "")
    return {"name": c["name"], "image": c.get("image"), "image_id": statuses.get(c["name"], {}).get("imageID"),
            "command": c.get("command"), "args": c.get("args"), "env": env, "resources": c.get("resources")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--loadgen", default="")
    a = ap.parse_args()
    pods = []
    for ns in NAMESPACES:
        for p in kubectl_json("-n", ns, "get", "pods").get("items", []):
            statuses = {s["name"]: s for s in p.get("status", {}).get("containerStatuses", [])}
            spec = p["spec"]
            pods.append({"namespace": ns, "name": p["metadata"]["name"], "node": spec.get("nodeName"),
                         "phase": p["status"].get("phase"), "labels": p["metadata"].get("labels", {}),
                         # The one-node layout (ADR-005, revision 2): the GPUs that HAMi gave the pod.
                         "gpu_devices": (p["metadata"].get("annotations") or {}).get("hami.io/vgpu-devices-allocated"),
                         "containers": [container_record(c, statuses) for c in spec.get("containers", [])],
                         "init_containers": [container_record(c, statuses) for c in spec.get("initContainers", [])]})
    nodes = [{"name": n["metadata"]["name"],
              "labels": {k: v for k, v in n["metadata"].get("labels", {}).items() if k.startswith(NODE_LABELS)},
              "capacity": n["status"].get("capacity", {})} for n in kubectl_json("get", "nodes").get("items", [])]
    git = ["git", "-C", str(ROOT)]
    commit = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run([*git, "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
    versions = dict(line.split("#")[0].strip().split("=", 1) for line in (ROOT / "cluster/versions.env").read_text()
                    .splitlines() if "=" in line.split("#")[0])
    variant_file = ROOT / "cluster" / "state" / "variant.json"
    variant = json.loads(variant_file.read_text()) if variant_file.exists() else {"name": "baseline"}
    overlay_file = ROOT / "cluster" / "state" / "overlay"  # written at the bring-up (cluster/one.sh, cluster/t2.sh)
    record = {"run_id": a.run_id, "created_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
              "variant": variant, "overlay": overlay_file.read_text().strip() if overlay_file.exists() else "t2",
              "git_commit": commit, "git_dirty": dirty, "loadgen": a.loadgen, "versions": versions,
              "router_policy": (ROOT / "control/router/policy.yaml").read_text(), "nodes": nodes, "pods": pods}
    json.dump(record, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
