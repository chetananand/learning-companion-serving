#!/usr/bin/env bash
# Apply one experiment variant (docs/runbooks/experiments.md), from the laptop through the tunnel.
#   router:  control/router/render.py --set KEY=VALUE, then helm upgrade of the router chart on node 2
#   engine:  tools/variant.py (presets, flags, env, sidecar flags), then kubectl apply and a rollout wait
#   warm:    the E8 switches of the warm controller. Each variant sets both (default warmup and ramp),
#            so no variant keeps the switches of an earlier variant.
# It writes cluster/state/variant.json, and tools/run_record.py copies it into each run.json.
# Usage: bash cluster/variant.sh <name> [--router K=V]... [--preset P]... [--arg D:F=V]... [--drop D:F]...
#                                       [--env D:N=V]... [--sidecar-arg D:F=V]... [--warm K=V]...
#   bash cluster/variant.sh baseline
#   bash cluster/variant.sh layout-a --router pd_mode=off --preset layout-a
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:?variant name}"; shift
STATE=cluster/state
export KUBECONFIG="${KUBECONFIG:-$PWD/$STATE/kubeconfig}"
# The overlay of the running cluster: cluster/one.sh and cluster/t2.sh write it at the bring-up. On one node, the
# t2 engine objects ask for a node that does not exist (companion.io/node=gpu), so the overlay must match.
OVERLAY="${OVERLAY:-$(cat "$STATE/overlay" 2>/dev/null || echo t2)}"
ROUTER=(); ENGINE=(); WARM_MODE=warmup; WARM_RAMP_MODE=ramp
while [[ $# -gt 0 ]]; do
  case "$1" in
    --router) ROUTER+=(--set "$2"); shift 2 ;;
    --preset|--arg|--drop|--env|--sidecar-arg) ENGINE+=("$1" "$2"); shift 2 ;;
    --warm)
      case "$2" in
        WARM_MODE=*) WARM_MODE="${2#*=}" ;;
        WARM_RAMP_MODE=*) WARM_RAMP_MODE="${2#*=}" ;;
        *) echo "unknown warm switch $2 (WARM_MODE, WARM_RAMP_MODE)"; exit 2 ;;
      esac
      shift 2 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done
DIR="$STATE/variants/$NAME"
mkdir -p "$DIR"

echo "== router (${#ROUTER[@]} overrides)"
uv run python -m control.router.render --out "$DIR/router" ${ROUTER[@]+"${ROUTER[@]}"} >/dev/null
if [[ -f "$STATE/node2-ip" ]]; then
  source cluster/versions.env
  rsync -az -e "ssh -i $HOME/.ssh/lambda_ai" "$DIR/router/" "ubuntu@$(cat $STATE/node2-ip):companion-variant/"
  ssh -i "$HOME/.ssh/lambda_ai" "ubuntu@$(cat $STATE/node2-ip)" \
    "sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm upgrade companion-router \
       oci://ghcr.io/llm-d/charts/llm-d-router-gateway --version $LLMD_ROUTER_VERSION -n companion \
       -f companion-variant/router-values.yaml --reuse-values=false"
  kubectl apply -f "$DIR/router/inference-objectives.yaml" -f "$DIR/router/agent-router.yaml"
  # A new pod reads the new config for sure (a config change alone may not restart the router pod).
  # It also starts each variant with an empty prefix index.
  kubectl -n companion rollout restart deploy -l app.kubernetes.io/instance=companion-router \
    || echo "no router Deployment with this label: VERIFY the chart labels at G0"
  for d in $(kubectl -n companion get deploy -l app.kubernetes.io/instance=companion-router -o name); do
    kubectl -n companion rollout status "$d" --timeout=5m
  done
fi

echo "== engine (${#ENGINE[@]} args, overlay $OVERLAY)"
uv run python tools/variant.py --overlay "$OVERLAY" ${ENGINE[@]+"${ENGINE[@]}"} > "$DIR/engine.yaml"
# Session 1: an LMCache key has no KV dtype (chunk hash, model, rank, group, salt). After a change of the KV
# format (for example fp8kv), a pod can load the data of the other format. So the tier starts empty: restart
# the LMCache server before the engine pods restart (a new format changes their args, so they restart).
NEW_KV=$(grep -o -- '--kv-cache-dtype=[a-z0-9_]*' "$DIR/engine.yaml" | sort -u | tr '\n' ' ' || true)
OLD_KV=$(cat "$STATE/kv-dtype" 2>/dev/null || true)
if [[ "$NEW_KV" != "$OLD_KV" ]]; then
  echo "== the KV format changes ('$OLD_KV' -> '$NEW_KV'): restart the LMCache server, so the tier is empty"
  kubectl -n companion rollout restart ds/lmcache-server
  kubectl -n companion rollout status ds/lmcache-server --timeout=10m
fi
printf '%s' "$NEW_KV" > "$STATE/kv-dtype"
kubectl apply --server-side --force-conflicts -f "$DIR/engine.yaml"
kubectl -n companion rollout status deploy/vllm-prefill --timeout=40m
kubectl -n companion rollout status deploy/vllm-decode --timeout=40m

echo "== warm controller: WARM_MODE=$WARM_MODE WARM_RAMP_MODE=$WARM_RAMP_MODE"
kubectl -n companion set env deploy/warm-controller WARM_MODE="$WARM_MODE" WARM_RAMP_MODE="$WARM_RAMP_MODE"
kubectl -n companion rollout status deploy/warm-controller --timeout=5m
# Session 1: each run after a variant switch began with 5 calls of 503 no_endpoints (all in the first 1.4 s):
# the new router pod did not know the engine pods yet. Wait until the full path answers 3 small calls in a row.
ok=0
for _ in $(seq 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 localhost:8080/v1/chat/completions \
    -H 'content-type: application/json' -H 'X-Tenant-Id: owner' -H 'X-Request-Class: interactive' \
    -H "X-Session-Id: variant-ready-$NAME" \
    -d '{"model":"companion","max_tokens":1,"messages":[{"role":"user","content":"Ready?"}]}' || true)
  if [[ "$code" == 200 ]]; then ok=$((ok + 1)); [[ $ok -ge 3 ]] && break; else ok=0; fi
  sleep 2
done
echo "== the full path answers: $ok calls in a row with 200"

python3 - "$NAME" "$OVERLAY" "$DIR" "${ROUTER[*]:-}" "${ENGINE[*]:-}" "WARM_MODE=$WARM_MODE WARM_RAMP_MODE=$WARM_RAMP_MODE" <<'PY'
import json, sys, time, pathlib
name, overlay, d, router_sets, engine_opts, warm = sys.argv[1:7]
router = pathlib.Path(d, "router", "epp-config.yaml").read_text()
record = {"name": name, "overlay": overlay, "applied_ts": time.time(),
          "router_overrides": router_sets.replace("--set ", "").split(), "engine_options": engine_opts,
          "warm": warm.split(), "engine_yaml": str(pathlib.Path(d, "engine.yaml")), "epp_config": router}
pathlib.Path("cluster/state/variant.json").write_text(json.dumps(record, indent=1))
print("variant", name, "applied")
PY
