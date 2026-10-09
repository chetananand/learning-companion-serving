#!/usr/bin/env bash
# Thursday 2026-10-01, the two-node plan, after cluster/sessions/thu-two-up.sh:
#   T-1 demo-check and the video of the four fixed questions (must-do item 1)
#   T-2 cluster/sessions/wed-experiments.sh: E3 with 32 decode sequences (item 3) and E7 again (item 2)
#   T-3 E9, only when node 1 is 4 x H100: KEDA may add one pod to each pool (cluster/t2.sh e9)
#   T-4 capture part A (before node 1 stops): the engine report and the backup
# Then, by hand: terminate node 1, run part B (cluster/sessions/thu-two.sh capture-b), and terminate node 2.
# A long step starts only if it and the capture (45 minutes) end before the first stop rule of the spend guard.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
GW=http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
SHAPE=$(uv run --quiet python cluster/lambda/lambda_ctl.py list | grep -o "companion-node1 id=[0-9a-f]* gpu_[a-z0-9_]*" \
  | awk '{print $3}')
fits() {
  local left; left=$(uv run --quiet python cluster/lambda/spend_guard.py --minutes-left "${SHAPE:-gpu_2x_h100_sxm5}") || return 1
  echo "minutes before the first stop rule: $left (this step: $1, the capture: 45)"
  (( $1 + 45 <= left ))
}
RUNS_FILE=cluster/state/thu-runs
if [[ "${1:-}" == capture-b ]]; then  # after node 1 stops: the Prometheus snapshot, then the Grafana images
  PROM_SNAPSHOT=1 bash cluster/g0.sh backup
  ARGS=(); for r in $(cat "$RUNS_FILE"); do ARGS+=("metrics/$r"); done
  uv run python tools/grafana_shots.py check --runs "${ARGS[@]}"
  uv run python tools/grafana_shots.py render --runs "${ARGS[@]}"
  exit 0
fi
: > "$RUNS_FILE"
step "T-1 demo-check, then a video of the four fixed questions in the UI"
make demo-check
bash cluster/sessions/record.sh demo demo-fixes || echo "the demo recording failed (the session goes on)"
if fits 110; then
  step "T-2 E3 with 32 decode sequences, and E7 again"
  bash cluster/sessions/wed-experiments.sh
  echo "e3-c32-50 e3-c32-100 e3-c32-150 e3-a32-50 e3-a32-100 e3-a32-150 e7b-precise e7b-approx" >> "$RUNS_FILE"
else
  step "T-2 SKIPPED: not enough time before the first stop rule"
fi
if [[ "$SHAPE" == gpu_4x_h100_sxm5 ]] && fits 45; then
  step "T-3 E9: KEDA may add one pod to each pool"
  bash cluster/t2.sh e9
  bash cluster/sessions/record.sh grafana e9-decode 900 > metrics/record-e9-decode.log 2>&1 &
  rec=$!
  bash cluster/loadgen.sh e9-decode capacity --target "$GW" --tokens 1000 --max-tokens 4000 --levels 48 --repeats 4
  wait "$rec" || echo "the e9-decode recording failed"
  bash cluster/sessions/record.sh grafana e9-prefill 900 > metrics/record-e9-prefill.log 2>&1 &
  rec=$!
  bash cluster/loadgen.sh e9-prefill capacity --target "$GW" --tokens 24000 --max-tokens 64 --levels 16 --repeats 20
  wait "$rec" || echo "the e9-prefill recording failed"
  echo "e9-decode e9-prefill" >> "$RUNS_FILE"
else
  step "T-3 SKIPPED: node 1 is $SHAPE, or not enough time"
fi
step "T-4 capture part A: the engine report and the backup (before node 1 stops)"
bash cluster/t2.sh report
bash cluster/g0.sh backup
step "Next, by hand: terminate node 1, then: bash cluster/sessions/thu-two.sh capture-b, then terminate node 2"
