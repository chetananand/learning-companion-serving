#!/usr/bin/env bash
# Gate G0 runbook (06-experiments.md): bring up node 2 in stages, from the laptop.
# The launch itself needs the owner's approval (A7): python3 cluster/lambda/lambda_ctl.py launch --node node2 ...
# Usage: bash cluster/g0.sh <stage>
#   ips        read the node IPs from the Lambda API (read-only) into cluster/state/
#   bootstrap  copy the repo to node 2 and install the k3s server
#   kubeconfig copy the kubeconfig to cluster/state/kubeconfig (server 127.0.0.1:6443, through the tunnel)
#   tunnel     start the SSH tunnel in the background (6443, Grafana, UI, API, edge, Prometheus)
#   images     build our images on node 2 and load them into k3s
#   secrets    create the namespaces and the secrets from ~/.env (laptop, through the tunnel)
#   sim        install the platform (helm on node 2) and our manifests with the llm-d simulator, then smoke
#   install t2 the same with the t2 overlay, for an experiment session (the engine pods wait for node 1)
#   dev        switch the engine pods to the dev model (gemma-4-E4B-it, P/D on the two A6000 GPUs)
#   ingest     run the ingest job (Notion -> Qdrant) and show its counts
#   report     save the G0 evidence: pods, versions, NodePort bind addresses, HAMi slices
#   backup     before each terminate: the Qdrant snapshot and the capture files to cluster/state/backups/
#              (PROM_SNAPSHOT=1: also a Prometheus snapshot, for Grafana images later; it can be large)
#   restore    after install: the newest Qdrant snapshot and the capture files back to node 2
#   freeze     after the capture runs: copy app-calls.jsonl to frozen.jsonl, the fixed replay input
set -euo pipefail
cd "$(dirname "$0")/.."
STATE=cluster/state
mkdir -p "$STATE"
export KUBECONFIG="$PWD/$STATE/kubeconfig"
SSH_KEY="$HOME/.ssh/lambda_ai"
ip() { cat "$STATE/node2-ip"; }
ssh2() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$(ip)" "$@"; }

case "${1:?stage}" in
  ips)
    python3 - <<'PY'
import json, pathlib, sys
sys.path.insert(0, "cluster/lambda")
from lambda_api import get
state = pathlib.Path("cluster/state")
for inst in get("/instances")["data"]:
    if inst.get("name") == "companion-node2" and inst.get("status") == "active":
        (state / "node2-ip").write_text(inst["ip"])
        (state / "node2-private-ip").write_text(inst.get("private_ip") or "")
        print("node 2:", inst["ip"], "private:", inst.get("private_ip"), "region:", inst["region"]["name"])
        break
else:
    sys.exit("no active companion-node2 instance")
PY
    ;;
  bootstrap)
    rsync -az --exclude .venv --exclude docs/spec/source --exclude metrics --exclude cluster/state \
      -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" ./ "ubuntu@$(ip):companion/"
    ssh2 "cd companion && sudo bash cluster/bootstrap/k3s-server.sh $(cat $STATE/node2-private-ip) $(ip)"
    ;;
  kubeconfig)
    ssh2 "sudo cat /etc/rancher/k3s/k3s.yaml" > "$STATE/kubeconfig"
    chmod 600 "$STATE/kubeconfig"
    echo "export KUBECONFIG=$PWD/$STATE/kubeconfig"
    ;;
  tunnel)
    nohup bash cluster/bootstrap/tunnel.sh "$(ip)" > "$STATE/tunnel.log" 2>&1 &
    echo $! > "$STATE/tunnel.pid"
    sleep 3
    kubectl get nodes -o wide
    ;;
  images)
    make images NODE2="$(ip)"
    ;;
  secrets)
    # From the laptop: the secrets come from ~/.env and never leave through a file.
    set -a; source "$HOME/.env"; set +a
    for ns in companion guard data; do
      kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
      kubectl -n "$ns" create secret generic companion-secrets \
        --from-literal=HF_TOKEN="${HF_TOKEN:?}" --from-literal=NOTION_API_KEY="${NOTION_API_KEY:-}" \
        --from-literal=TAVILY_API_KEY="${TAVILY_API_KEY:-}" --from-literal=OVERFLOW_API_KEY="${SUPERLINKED_API_KEY:-}" \
        --dry-run=client -o yaml | kubectl apply -f -
    done
    # The tokenizer sidecar (vllm-render) of the llm-d router chart reads this secret (G0, 2026-09-28).
    kubectl -n companion create secret generic llm-d-hf-token --from-literal=HF_TOKEN="${HF_TOKEN:?}" \
      --dry-run=client -o yaml | kubectl apply -f -
    ;;
  sim|install)
    # The platform install runs on node 2, where the runbook installs helm. The laptop renders first.
    OV="${2:-sim}"
    uv run python -m control.router.render
    rsync -az --exclude .venv --exclude docs/spec/source --exclude metrics --exclude cluster/state \
      -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" ./ "ubuntu@$(ip):companion/"
    ssh2 "command -v helm >/dev/null || curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"
    ssh2 "cd companion && sudo -E env KUBECONFIG=/etc/rancher/k3s/k3s.yaml SKIP_SECRETS=1 SKIP_RENDER=1 PATH=\$PATH:/usr/local/bin bash cluster/install.sh $OV"
    kubectl -n companion rollout status deploy/edge --timeout=10m
    if [[ "$OV" == one || "$OV" == one-e9 ]]; then
      echo "one node: the engine pods load the model (next: bash cluster/one.sh wait)"
    elif [[ "$OV" == t2 || "$OV" == t4 ]]; then
      echo "the engine pods wait for node 1: next bash cluster/g0.sh restore, then cluster/t2.sh"
    else
      bash cluster/smoke/smoke.sh "$OV" || echo "smoke: see the first FAIL line above"
    fi
    ;;
  dev)
    uv run python -m control.router.render
    kubectl kustomize --load-restrictor LoadRestrictionsNone cluster/manifests/overlays/dev \
      | kubectl apply --server-side --force-conflicts -f -
    kubectl -n companion rollout status deploy/vllm-prefill --timeout=30m
    kubectl -n companion rollout status deploy/vllm-decode --timeout=30m
    ;;
  ingest)
    kubectl -n companion create job "ingest-g0-$(date +%s)" --from=cronjob/ingest
    echo "follow with: kubectl -n companion logs -f job/<name>"
    ;;
  backup)
    # Teardown backup (ADR-010, S3), the laptop copy. Qdrant keeps its files on the local disk of node 2.
    mkdir -p "$STATE/backups/capture"
    kubectl -n data port-forward svc/qdrant 16333:6333 >/dev/null 2>&1 &
    pf=$!
    sleep 3
    snap=$(curl -sf -X POST "http://127.0.0.1:16333/collections/bookmarks/snapshots?wait=true" \
      | python3 -c 'import json, sys; print(json.load(sys.stdin)["result"]["name"])')
    curl -sf -o "$STATE/backups/$snap" "http://127.0.0.1:16333/collections/bookmarks/snapshots/$snap"
    kill "$pf"
    rsync -az -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" "ubuntu@$(ip):/var/lib/companion/app-data/capture/" "$STATE/backups/capture/" \
      || echo "no capture files on node 2"
    if [[ "${PROM_SNAPSHOT:-0}" == 1 ]]; then   # the admin API is on (install.sh); Prometheus through the tunnel
      name=$(curl -sf -X POST http://127.0.0.1:9090/api/v1/admin/tsdb/snapshot \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["data"]["name"])')
      pod=$(kubectl -n monitoring get pods -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}')
      # Session 2 (2026-09-29): kubectl cp needs tar in the container, and the Prometheus image has none. The
      # snapshot is in the pod's emptyDir on node 2, so rsync copies it from the node.
      uid=$(kubectl -n monitoring get pod "$pod" -o jsonpath='{.metadata.uid}')
      src="/var/lib/kubelet/pods/$uid/volumes/kubernetes.io~empty-dir/prometheus-kube-prometheus-stack-prometheus-db/snapshots/$name/"
      mkdir -p "$STATE/backups/prometheus-$name"
      rsync -a --rsync-path="sudo rsync" -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" "ubuntu@$(ip):$src" "$STATE/backups/prometheus-$name/"
    fi
    ls -la "$STATE/backups" "$STATE/backups/capture"
    ;;
  restore)
    snap=$(ls -t "$STATE"/backups/*.snapshot 2>/dev/null | head -1 || true)
    if [[ -z "$snap" ]]; then echo "no snapshot in $STATE/backups: run bash cluster/g0.sh ingest"; exit 1; fi
    kubectl -n data rollout status statefulset/qdrant --timeout=10m
    kubectl -n data port-forward svc/qdrant 16333:6333 >/dev/null 2>&1 &
    pf=$!
    sleep 3
    curl -sf -X POST "http://127.0.0.1:16333/collections/bookmarks/snapshots/upload?priority=snapshot&wait=true" \
      -F "snapshot=@$snap"
    echo
    curl -sf "http://127.0.0.1:16333/collections/bookmarks" | python3 -c \
      'import json, sys; r = json.load(sys.stdin)["result"]; print("points:", r["points_count"])'
    kill "$pf"
    if ls "$STATE"/backups/capture/*.jsonl >/dev/null 2>&1; then
      rsync -az --rsync-path="sudo rsync" -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" "$STATE/backups/capture/" \
        "ubuntu@$(ip):/var/lib/companion/app-data/capture/"
      ssh2 "sudo chown -R 65534:65534 /var/lib/companion/app-data/capture"  # the app user writes there
    fi
    ;;
  freeze)
    ssh2 "cd /var/lib/companion/app-data/capture && sudo cp app-calls.jsonl frozen.jsonl && sudo chmod 644 frozen.jsonl \
      && wc -l frozen.jsonl"
    ;;
  report)
    out="metrics/g0-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$out"
    kubectl get pods -A -o wide > "$out/pods.txt"
    kubectl get nodes -o wide --show-labels > "$out/nodes.txt"
    ssh2 "sudo ss -ltnp" > "$out/listen.txt"        # NodePorts must listen on 127.0.0.1 only
    ssh2 "nvidia-smi" > "$out/nvidia-smi.txt"
    python3 tools/run_record.py g0 > "$out/run.json"
    echo "saved $out"
    ;;
  *)
    echo "unknown stage: $1"; exit 2 ;;
esac
