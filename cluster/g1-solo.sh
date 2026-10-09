#!/usr/bin/env bash
# Engine-only Gate G1 on node 1 alone (2 x H100 SXM), when node 2 has no stock (DEBATE-LOG point 13).
# Node 1 runs a one-node k3s cluster with only the engine: the LMCache server, vllm-prefill, and
# vllm-decode with its routing sidecar. The G1 tests run on the node (tools/g1_tests.py, httpx only).
# The screenshot test needs the browser service on node 2, so it moves to the next two-node session.
# The launch needs the owner's approval (A7): python3 cluster/lambda/lambda_ctl.py launch --node node1-t2 ...
# Usage: bash cluster/g1-solo.sh <stage>
#   ips | bootstrap | kubeconfig | tunnel | engine | wait | tests | restart | challenger | report
# OVERLAY=t2 (default), t2-k0 (fallback: NIXL only, no LMCache), or t2-vllm026 (fallback: vLLM v0.26.0).
# In a T2 session (node 1 joined to node 2, cluster/t2.sh), the stages tests, restart, challenger, and report
# also work: KUBECONFIG_G1=cluster/state/kubeconfig bash cluster/g1-solo.sh tests
set -euo pipefail
cd "$(dirname "$0")/.."
STATE=cluster/state
mkdir -p "$STATE"
export KUBECONFIG="${KUBECONFIG_G1:-$PWD/$STATE/kubeconfig-node1}"
SSH_KEY="$HOME/.ssh/lambda_ai"
ip() { cat "$STATE/node1-ip"; }
ssh1() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$(ip)" "$@"; }
pod_ip() { kubectl -n companion get pods -l "app=$1" -o jsonpath='{.items[0].status.podIP}'; }
OUT="metrics/g1-solo"
OVERLAY="${OVERLAY:-t2}"

case "${1:?stage}" in
  ips)
    python3 - <<'PY'
import pathlib, sys
sys.path.insert(0, "cluster/lambda")
from lambda_api import get
for inst in get("/instances")["data"]:
    if inst.get("name") == "companion-node1" and inst.get("status") == "active":
        pathlib.Path("cluster/state/node1-ip").write_text(inst["ip"])
        pathlib.Path("cluster/state/node1-private-ip").write_text(inst.get("private_ip") or "")
        print("node 1:", inst["ip"], inst["instance_type"]["name"], inst["region"]["name"])
        break
else:
    sys.exit("no active companion-node1 instance")
PY
    ;;
  bootstrap)
    rsync -az --exclude .venv --exclude docs/spec/source --exclude metrics --exclude cluster/state \
      -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" ./ "ubuntu@$(ip):companion/"
    ssh1 "cd companion && sudo bash cluster/bootstrap/k3s-server.sh $(cat $STATE/node1-private-ip) $(ip) gpu"
    ssh1 "python3 -m pip install --user --quiet 'httpx>=0.27'"
    ;;
  kubeconfig)
    ssh1 "sudo cat /etc/rancher/k3s/k3s.yaml" > "$KUBECONFIG"
    chmod 600 "$KUBECONFIG"
    ;;
  tunnel)
    nohup ssh -i "$SSH_KEY" -N -o ExitOnForwardFailure=yes -L 6443:127.0.0.1:6443 "ubuntu@$(ip)" \
      > "$STATE/tunnel-node1.log" 2>&1 &
    echo $! > "$STATE/tunnel-node1.pid"
    sleep 3
    kubectl get nodes -o wide
    ;;
  engine)
    set -a; source "$HOME/.env"; set +a
    source cluster/versions.env
    kubectl create namespace companion --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n companion create secret generic companion-secrets --from-literal=HF_TOKEN="${HF_TOKEN:?}" \
      --dry-run=client -o yaml | kubectl apply -f -
    kubectl apply -f "https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${NVIDIA_DEVICE_PLUGIN_VERSION}/deployments/static/nvidia-device-plugin.yml"
    # Only the engine objects of the t2 overlay.
    kubectl kustomize --load-restrictor LoadRestrictionsNone "cluster/manifests/overlays/$OVERLAY" | uv run python -c '
import sys, yaml
keep = {("DaemonSet", "lmcache-server"), ("Deployment", "vllm-prefill"), ("Deployment", "vllm-decode")}
docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
out = [d for d in docs if (d["kind"], d["metadata"]["name"]) in keep
       or (d["kind"] == "Service" and d["metadata"]["name"].startswith("vllm-"))]
print(yaml.safe_dump_all(out))' | kubectl apply --server-side --force-conflicts -f -
    ;;
  wait)
    kubectl -n companion rollout status ds/lmcache-server --timeout=20m
    kubectl -n companion rollout status deploy/vllm-prefill --timeout=45m
    kubectl -n companion rollout status deploy/vllm-decode --timeout=45m
    ;;
  tests)
    P="$(pod_ip vllm-prefill)"; D="$(pod_ip vllm-decode)"
    run() { ssh1 "cd companion && python3 tools/g1_tests.py $* --out $OUT" || true; }
    run toolcalls --base "http://$P:8000"
    run prefix --base "http://$P:8000" --metrics "http://$P:8000/metrics"
    run ttft --base "http://$P:8000"
    run itl --base "http://$D:8200"
    run split --base "http://$D:8000" --prefill "$P:8000" --metrics "http://$D:8200/metrics"
    run cputier --base "http://$P:8000" --metrics "http://$P:8000/metrics"
    ;;
  restart)
    kubectl -n companion rollout restart deploy/vllm-prefill
    kubectl -n companion rollout status deploy/vllm-prefill --timeout=30m
    P="$(pod_ip vllm-prefill)"
    ssh1 "cd companion && python3 tools/g1_tests.py restart --base http://$P:8000 --metrics http://$P:8000/metrics --out $OUT" || true
    ;;
  challenger)
    # Glimmer on GPU 0 for the tool-call suite only (ADR-003 decision rule). The Gemma 4 prefill pod stops first.
    kubectl -n companion scale deploy/vllm-prefill --replicas=0
    kubectl apply -f cluster/manifests/g1/challenger.yaml
    kubectl -n companion rollout status deploy/vllm-challenger --timeout=45m
    C="$(pod_ip vllm-challenger)"
    ssh1 "cd companion && python3 tools/g1_tests.py toolcalls --base http://$C:8000 --out $OUT/challenger" || true
    kubectl -n companion delete deploy/vllm-challenger
    kubectl -n companion scale deploy/vllm-prefill --replicas=1
    ;;
  report)
    dest="metrics/g1-solo-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$dest"
    rsync -az -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" "ubuntu@$(ip):companion/$OUT/" "$dest/"
    for app in vllm-prefill vllm-decode; do
      kubectl -n companion logs "deploy/$app" -c modelserver --tail=-1 > "$dest/$app.log" || true  # KV cache size (E1)
    done
    kubectl -n companion logs ds/lmcache-server --tail=-1 > "$dest/lmcache-server.log" || true
    ssh1 "nvidia-smi" > "$dest/nvidia-smi.txt"
    kubectl get pods -A -o wide > "$dest/pods.txt"
    echo "saved $dest"
    ;;
  *)
    echo "unknown stage: $1"; exit 2 ;;
esac
