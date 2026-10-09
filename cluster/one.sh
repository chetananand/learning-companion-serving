#!/usr/bin/env bash
# One-node bring-up (ADR-005, revision 2): one 4 x H100 node runs all pods, if no node 2 shape has stock.
# The launch (lambda_ctl node key one-t4: approved for 2026-10-01 only, and only when no region has stock for
# both a node 2 shape and a node 1 shape; the launch tool checks this):
#   printf 'gpu_4x_h100_sxm5\n' | uv run python cluster/lambda/lambda_ctl.py launch --node one-t4 --region <r> --approve
# The node is the k3s server with the label companion.io/node=control. The stage ips writes its address into
# the node 1 and node 2 state files, so the scripts that use ssh to node 1 or node 2 reach this node.
# Usage: bash cluster/one.sh <stage>
#   ips        read the node address from the Lambda API (read-only), and write cluster/state/overlay (one)
#   up         the G0 stages: bootstrap, kubeconfig, tunnel, images, secrets, install one, restore
#   placed     after the install: wait until HAMi placed the GPU pods (10 min at most), then check the plan.
#              A wrong plan fails here, before the long wait for the model.
#   pin        the fallback if binpack fails: the node 2 GPU pods use the last GPU (nvidia.com/use-gpuuuid),
#              and the engine pods never use it (nvidia.com/nouse-gpuuuid). HAMi v2.10.0 reads both.
#   wait       wait for the LMCache tier and the engine pods
#   gpus       VERIFY the GPU plan (tools/gpu_plan.py) and the KV cache memory of vLLM (a full GPU)
#   smoke      the smoke test of the overlay
#   e9         the KEDA limits of one-e9 (2 pods for each pool), and cluster/state/overlay (one-e9)
#   hold P     hold pool P (vllm-prefill or vllm-decode) at one replica (the KEDA pause annotation)
#   release P  remove the hold of pool P
#   report     save the bring-up evidence: pods, nodes, the GPU plan, the engine logs, nvidia-smi, the run record
#   capture    before the terminate: report, the backup with the Prometheus snapshot, and the Grafana check and
#              images of the runs in RUNS (Grafana and Prometheus stop with the node)
set -euo pipefail
cd "$(dirname "$0")/.."
STATE=cluster/state
export KUBECONFIG="$PWD/$STATE/kubeconfig"
SSH_KEY="$HOME/.ssh/lambda_ai"
ssh_one() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$(cat "$STATE/node2-ip")" "$@"; }
overlay() { cat "$STATE/overlay" 2>/dev/null || echo one; }
pool() {
  case "${1:-}" in vllm-prefill|vllm-decode) echo "$1" ;; *) echo "pool: vllm-prefill or vllm-decode" >&2; exit 2 ;; esac
}

case "${1:?stage}" in
  ips)
    python3 - <<'PY'
import pathlib, sys
sys.path.insert(0, "cluster/lambda")
from lambda_api import get
state = pathlib.Path("cluster/state")
found = [i for i in get("/instances")["data"] if i.get("name") == "companion-one" and i.get("status") == "active"]
if not found:
    sys.exit("no active companion-one instance")
one = found[0]
for node in ("node1", "node2"):
    (state / f"{node}-ip").write_text(one["ip"])
    (state / f"{node}-private-ip").write_text(one.get("private_ip") or "")
(state / "overlay").write_text("one")  # cluster/variant.sh reads it
import json, time  # a fresh cluster runs the base engine: no variant of an earlier session in the run records
(state / "variant.json").write_text(json.dumps({"name": "baseline", "overlay": "one", "applied_ts": time.time()}))
print("one node:", one["ip"], "private:", one.get("private_ip"), one["instance_type"]["name"], one["region"]["name"])
PY
    ;;
  up)
    for s in bootstrap kubeconfig tunnel images secrets; do
      echo "== $(date -u +%H:%M:%S) UTC g0.sh $s"
      bash cluster/g0.sh "$s"
    done
    echo "== $(date -u +%H:%M:%S) UTC g0.sh install one"
    bash cluster/g0.sh install one
    echo "== $(date -u +%H:%M:%S) UTC g0.sh restore"
    bash cluster/g0.sh restore
    ;;
  placed)
    for _ in $(seq 60); do
      missing=$(kubectl get pods -A -o json | python3 -c '
import json, sys
apps = ("vllm-prefill", "vllm-decode", "sie-embed", "sie-ocr", "guard-safety", "guard-injection")
pods = json.load(sys.stdin)["items"]
print(sum(1 for p in pods if (p["metadata"].get("labels") or {}).get("app") in apps
          and p["status"].get("phase") in ("Running", "Pending")
          and "hami.io/vgpu-devices-allocated" not in (p["metadata"].get("annotations") or {})))')
      [[ "$missing" == 0 ]] && break
      echo "GPU pods not placed yet: $missing"
      sleep 10
    done
    kubectl get pods -A -o json | python3 tools/gpu_plan.py
    ;;
  pin)
    last=$(ssh_one "nvidia-smi --query-gpu=uuid --format=csv,noheader" | tail -1 | tr -d ' ')
    [[ "$last" == GPU-* ]] || { echo "no GPU UUID from nvidia-smi: $last"; exit 1; }
    for d in data/sie-embed data/sie-ocr guard/guard-safety guard/guard-injection; do
      kubectl -n "${d%/*}" patch deploy "${d#*/}" --type merge \
        -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"nvidia.com/use-gpuuuid\":\"$last\"}}}}}"
    done
    for d in vllm-prefill vllm-decode; do
      kubectl -n companion patch deploy "$d" --type merge \
        -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"nvidia.com/nouse-gpuuuid\":\"$last\"}}}}}"
    done
    echo "pinned: the node 2 GPU pods use $last, the engine pods do not. Next: bash cluster/one.sh placed"
    ;;
  wait)
    kubectl -n companion rollout status ds/lmcache-server --timeout=20m
    kubectl -n companion rollout status deploy/vllm-prefill --timeout=45m
    kubectl -n companion rollout status deploy/vllm-decode --timeout=45m
    ;;
  gpus)
    kubectl get pods -A -o json | python3 tools/gpu_plan.py
    ssh_one "nvidia-smi --query-gpu=index,uuid,memory.used,memory.total --format=csv"
    tmp=$(mktemp -d)
    for app in vllm-prefill vllm-decode; do
      kubectl -n companion logs "deploy/$app" -c modelserver --tail=-1 > "$tmp/$app.log"
    done
    python3 tools/gpu_plan.py kv "$tmp/vllm-prefill.log" "$tmp/vllm-decode.log"
    ;;
  smoke)
    bash cluster/smoke/smoke.sh "$(overlay)"
    ;;
  e9)
    # Only the two ScaledObjects differ between one and one-e9: apply them, and leave the running pods.
    kubectl kustomize --load-restrictor LoadRestrictionsNone cluster/manifests/overlays/one-e9 | uv run --quiet python -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if d and d["kind"] == "ScaledObject"]
assert len(docs) == 2, docs
print(yaml.safe_dump_all(docs, sort_keys=False))' | kubectl apply --server-side --force-conflicts -f -
    printf 'one-e9' > "$STATE/overlay"
    kubectl -n companion get scaledobject
    ;;
  hold)
    p=$(pool "${2:-}")
    kubectl -n companion annotate scaledobject "$p" autoscaling.keda.sh/paused-replicas=1 --overwrite
    # KEDA scales the pool down to one pod. Wait for it, so the GPU of the second pod is free for the other pool.
    kubectl -n companion wait --for=jsonpath='{.status.replicas}'=1 "deploy/$p" --timeout=5m
    ;;
  release)
    p=$(pool "${2:-}")
    kubectl -n companion annotate scaledobject "$p" autoscaling.keda.sh/paused-replicas-
    ;;
  report)
    dest="metrics/one-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$dest"
    kubectl get pods -A -o wide > "$dest/pods.txt"
    kubectl get nodes -o wide --show-labels > "$dest/nodes.txt"
    kubectl get pods -A -o json | python3 tools/gpu_plan.py > "$dest/gpu-plan.txt" || true
    for app in vllm-prefill vllm-decode; do
      kubectl -n companion logs "deploy/$app" -c modelserver --tail=-1 > "$dest/$app.log" || true
    done
    ssh_one "nvidia-smi" > "$dest/nvidia-smi.txt"
    python3 tools/scrape_now.py "$dest" || true   # the raw /metrics text that DESIGN.md pastes (H-13, H-123)
    python3 tools/run_record.py "one-$(overlay)" > "$dest/run.json"
    echo "saved $dest"
    ;;
  capture)
    : "${RUNS:?RUNS: the run ids of the session, for the Grafana images}"
    bash cluster/one.sh report
    PROM_SNAPSHOT=1 bash cluster/g0.sh backup
    ARGS=()
    for r in $RUNS; do ARGS+=("metrics/$r"); done
    # Fault 37 (2026-10-01): check exits non-zero when a panel has no data, and set -e then skipped the render.
    uv run python tools/grafana_shots.py check --runs "${ARGS[@]}" || echo "some panels have no data (see above)"
    uv run python tools/grafana_shots.py render --runs "${ARGS[@]}"
    ;;
  *)
    echo "unknown stage: $1"; exit 2 ;;
esac
