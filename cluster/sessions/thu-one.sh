#!/usr/bin/env bash
# Thursday 2026-10-01, the one-node layout (ADR-005, revision 2, and the 8 x A100 addendum), after
# cluster/sessions/thu-one-up.sh. The owner records the screen, so the session runs in phases with pauses:
#   demo-check   (the default) T-1: make demo-check. Then the owner records the demo in the UI.
#   experiments  T-2: cluster/sessions/wed-experiments.sh: E3 with 32 decode sequences, and E7 again.
#   e9           T-3, T-4: E9, one pool at a time (cluster/one.sh hold). The owner records the dashboard
#                "Pods / replicas / KEDA" in Grafana during it.
#   capture      T-5: the evidence before the terminate (Grafana and Prometheus stop with the node).
# A long phase starts only if it and the capture (45 minutes) end before the first stop rule of the spend guard.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
[[ "$(cat cluster/state/overlay)" == one* ]] || { echo "cluster/state/overlay is not one: run thu-one-up.sh"; exit 1; }
GW=http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
SHAPE=$(uv run --quiet python cluster/lambda/lambda_ctl.py list | grep -o "companion-one id=[0-9a-f]* gpu_[a-z0-9_]*" \
  | awk '{print $3}')
fits() {  # fits <minutes>: the phase and the capture end before the first stop rule of the running shape
  local left; left=$(uv run --quiet python cluster/lambda/spend_guard.py --minutes-left "${SHAPE:-gpu_4x_h100_sxm5}") || return 1
  echo "minutes before the first stop rule: $left (this phase: $1, the capture: 45)"
  (( $1 + 45 <= left ))
}

case "${1:-demo-check}" in
  demo-check)
    step "T-1 demo-check (the demo fixes)"
    make demo-check
    step "T-1 done. Next: the owner records the demo in the UI (http://localhost:8501), then: thu-one.sh experiments"
    ;;
  experiments)
    if fits 110; then
      step "T-2 E3 with 32 decode sequences, and E7 again"
      bash cluster/sessions/wed-experiments.sh
    else
      step "T-2 SKIPPED: not enough time before the first stop rule"
    fi
    step "T-2 done. Next: the owner starts the screen recording of Grafana, then: thu-one.sh e9"
    ;;
  e9)
    if fits 45; then
      step "T-3 E9 decode-heavy load: only the decode pool may grow"
      bash cluster/one.sh e9
      bash cluster/one.sh hold vllm-prefill
      bash cluster/loadgen.sh e9-decode capacity --target "$GW" --tokens 1000 --max-tokens 4000 --levels 48 --repeats 4
      step "T-4 E9 prefill-heavy load: only the prefill pool may grow"
      bash cluster/one.sh hold vllm-decode
      bash cluster/one.sh release vllm-prefill
      bash cluster/loadgen.sh e9-prefill capacity --target "$GW" --tokens 24000 --max-tokens 64 --levels 16 --repeats 20
      bash cluster/one.sh release vllm-decode
    else
      step "T-3 and T-4 SKIPPED: E9 does not end before the first stop rule"
    fi
    step "E9 done. Next: thu-one.sh capture"
    ;;
  capture)
    step "T-5 the evidence before the terminate: report, backup with the Prometheus snapshot, Grafana images"
    RUNS=""
    for r in e3-c32-50 e3-c32-100 e3-c32-150 e3-a32-50 e3-a32-100 e3-a32-150 e7b-precise e7b-approx \
             e3-c32-r015 e3-c32-r030 e3-a32-r015 e3-a32-r030 e3-a32-r045 e7b-approx2 e9-decode e9-prefill; do
      [[ -f "metrics/$r/window.json" ]] && RUNS="$RUNS $r"
    done
    echo "runs with data: ${RUNS:- none}"
    RUNS="${RUNS# }" bash cluster/one.sh capture
    step "capture done. Next: terminate companion-one, the ledger, and the commit"
    ;;
  *) echo "unknown phase $1 (demo-check, experiments, e9, capture)"; exit 2 ;;
esac
