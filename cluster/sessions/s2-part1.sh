#!/usr/bin/env bash
# S2-2 to S2-13 of docs/runbooks/experiments.md, in order (session 2, run on 2026-09-29 after session 1;
# the owner approved it). S2-1 (the demo recording) runs with the owner, before this script.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
# E14 sends a long synthetic user message: it skips edge, like E1 and E9 (runbook note 7).
GW=http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
replay() { bash cluster/loadgen.sh "$1" replay --capture "$CAP" --mix "$2" --rate "$3" --duration "$4" "${@:5}"; }
delete_decode_at() {  # note 1: delete the decode pod at $2 s
  mkdir -p "metrics/$1"
  (sleep "$2"; date -u +%Y-%m-%dT%H:%M:%SZ > "metrics/$1/delete-time.txt"
   kubectl -n companion delete pod -l app=vllm-decode --wait=false) &
}
restart_decode() {  # note 2
  kubectl -n companion rollout restart deploy/vllm-decode
  kubectl -n companion rollout status deploy/vllm-decode --timeout=15m
}
ghost_run() {  # note 3: clear the decode prefix cache at 150 s, then count the ghosts
  mkdir -p "metrics/$1"
  (sleep 150; date -u +%Y-%m-%dT%H:%M:%SZ > "metrics/$1/clear-time.txt"
   kubectl -n companion exec deploy/vllm-decode -c modelserver -- python3 -c \
     "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8200/reset_prefix_cache', method='POST')).status)") &
  replay "$1" m2 "$R50" 300
  local D; D=$(kubectl -n companion get pods -l app=vllm-decode -o jsonpath='{.items[0].status.podIP}')
  python3 tools/ghosts.py --log "metrics/$1/envoy-access.log" --pod "$D" \
    --after "$(cat "metrics/$1/clear-time.txt")" --out "metrics/$1/ghosts.json"
}

step "S2-2 E4: the LMCache hop, then the NIXL hop"
bash cluster/loadgen.sh e4-hop tool -m tools.pd_probe hop --decode {decode} --prefill {prefill} --lmcache {lmcache} --capture "$CAP"
replay e4-m2 m2 "$R50" 300
bash cluster/variant.sh nixl-hop --preset nixl-hop
bash cluster/loadgen.sh e4-hop-nixl tool -m tools.pd_probe hop --decode {decode} --prefill {prefill} --lmcache {lmcache} --capture "$CAP"

step "S2-3 E10: FCFS, then EDF (the base first: S2-2 ends on nixl-hop)"
bash cluster/variant.sh baseline
replay e10-fcfs m4 "$R100" 420 --extra noisy:0.25
bash cluster/variant.sh edf --router flow_control.ordering=edf-ordering-policy
replay e10-edf m4 "$R100" 420 --extra noisy:0.25

step "S2-4 E12 aborts"
bash cluster/variant.sh baseline
replay e12-abort m4 "$R100" 420 --abort 0.2

step "S2-5 E14 queue order"
bash cluster/loadgen.sh e14-on-int tool -m tools.queue_order --target "$GW" --long-class interactive --rounds 5 --capture "$CAP"
bash cluster/loadgen.sh e14-on-batch tool -m tools.queue_order --target "$GW" --long-class batch --rounds 5 --capture "$CAP"
bash cluster/variant.sh nosplit --router pd_non_cached_tokens=1000000
bash cluster/loadgen.sh e14-off-int tool -m tools.queue_order --target "$GW" --long-class interactive --rounds 5 --capture "$CAP"
bash cluster/loadgen.sh e14-off-batch tool -m tools.queue_order --target "$GW" --long-class batch --rounds 5 --capture "$CAP"

step "back to the base for the demo"
bash cluster/variant.sh baseline
step "session 2 part 1 done"
