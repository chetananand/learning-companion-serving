#!/usr/bin/env bash
# T2 bring-up for the experiment sessions (docs/runbooks/experiments.md): node 1 (2 x H100) joins the k3s
# cluster of node 2. Node 2 is up first: cluster/g0.sh ips, bootstrap, kubeconfig, tunnel, images, secrets,
# `install t2`, and `restore`. Launch node 1 only after that, so that the H100 never waits for node 2.
# Usage: bash cluster/t2.sh <stage>
#   ips | join | images | wait | smoke | e9 | report
# OVERLAY=t2 (default) or t4 (the E9 session).
set -euo pipefail
cd "$(dirname "$0")/.."
STATE=cluster/state
export KUBECONFIG="$PWD/$STATE/kubeconfig"
SSH_KEY="$HOME/.ssh/lambda_ai"
OVERLAY="${OVERLAY:-t2}"
ip1() { cat "$STATE/node1-ip"; }
ip2() { cat "$STATE/node2-ip"; }
ssh1() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$(ip1)" "$@"; }
ssh2() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$(ip2)" "$@"; }

case "${1:?stage}" in
  ips)
    python3 - <<'PY'
import os, pathlib, sys
sys.path.insert(0, "cluster/lambda")
from lambda_api import get
state = pathlib.Path("cluster/state")
found = {i["name"]: i for i in get("/instances")["data"]
         if i.get("status") == "active" and i.get("name") in ("companion-node1", "companion-node2")}
if len(found) < 2:
    sys.exit(f"need both nodes active, found: {sorted(found)}")
n1, n2 = found["companion-node1"], found["companion-node2"]
if n1["region"]["name"] != n2["region"]["name"]:
    sys.exit(f"different regions ({n1['region']['name']}, {n2['region']['name']}): no private network for k3s")
(state / "node1-ip").write_text(n1["ip"])
(state / "overlay").write_text(os.environ.get("OVERLAY", "t2"))  # cluster/variant.sh reads it
import json, time  # a fresh cluster runs the base engine: no variant of an earlier session in the run records
(state / "variant.json").write_text(json.dumps({"name": "baseline", "overlay": os.environ.get("OVERLAY", "t2"),
                                                "applied_ts": time.time()}))
(state / "node1-private-ip").write_text(n1.get("private_ip") or "")
for n in (n1, n2):
    print(n["name"], n["ip"], "private:", n.get("private_ip"), n["instance_type"]["name"], n["region"]["name"])
PY
    ;;
  join)
    rsync -az --exclude .venv --exclude docs/spec/source --exclude metrics --exclude cluster/state \
      -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" ./ "ubuntu@$(ip1):companion/"
    ssh1 "python3 -m pip install --user --quiet 'httpx>=0.27'"   # the G1 tests run on node 1
    # The join token goes from node 2 to node 1 through a pipe: no file and no log on the laptop.
    ssh2 "sudo cat /var/lib/rancher/k3s/server/node-token" | ssh1 "cd companion && sudo bash \
      cluster/bootstrap/k3s-agent.sh $(cat $STATE/node2-private-ip) - $(cat $STATE/node1-private-ip) $(ip1)"
    for _ in $(seq 60); do
      kubectl get nodes -l companion.io/node=gpu -o name | grep -q . && break
      sleep 10
    done
    kubectl wait --for=condition=Ready node -l companion.io/node=gpu --timeout=10m
    kubectl get nodes -o wide
    ;;
  images)
    # The stale-metrics proxy of E11 runs in the decode pod on node 1 (image companion/edge:dev).
    # The engine pods pull the vLLM image and the model at the same time, so this is not on the critical path.
    ssh1 "cd companion && sudo docker build -q -t companion/edge:dev -f images/edge/Dockerfile . \
      && sudo docker save companion/edge:dev | sudo k3s ctr images import -"
    ;;
  wait)
    kubectl -n companion rollout status ds/lmcache-server --timeout=20m
    kubectl -n companion rollout status deploy/vllm-prefill --timeout=45m
    kubectl -n companion rollout status deploy/vllm-decode --timeout=45m
    ;;
  smoke)
    bash cluster/smoke/smoke.sh "$OVERLAY"
    ;;
  e9)
    # E9 with node 1 = 4 x H100: KEDA may add one pod to each pool. Only the two ScaledObjects of the t4 overlay
    # differ from t2, so apply them and leave the running pods.
    kubectl kustomize --load-restrictor LoadRestrictionsNone cluster/manifests/overlays/t4 | uv run --quiet python -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d and d["kind"] == "ScaledObject"]
assert len(docs) == 2, docs
print(yaml.safe_dump_all(docs, sort_keys=False))' | kubectl apply --server-side --force-conflicts -f -
    printf 't4' > "$STATE/overlay"
    kubectl -n companion get scaledobject
    ;;
  report)
    dest="metrics/t2-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$dest"
    kubectl get pods -A -o wide > "$dest/pods.txt"
    kubectl get nodes -o wide --show-labels > "$dest/nodes.txt"
    for app in vllm-prefill vllm-decode; do
      kubectl -n companion logs "deploy/$app" -c modelserver --tail=-1 > "$dest/$app.log" || true  # KV size (E1)
    done
    ssh1 "nvidia-smi" > "$dest/nvidia-smi-node1.txt"
    python3 tools/scrape_now.py "$dest" || true   # the raw /metrics text that DESIGN.md pastes (H-13, H-123)
    python3 tools/run_record.py t2 > "$dest/run.json"
    echo "saved $dest"
    ;;
  *)
    echo "unknown stage: $1"; exit 2 ;;
esac
