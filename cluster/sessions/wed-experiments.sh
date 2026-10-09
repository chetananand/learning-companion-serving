#!/usr/bin/env bash
# Wednesday 2026-09-30, the must-do items 2 and 3 (after the bring-up and the demo check, item 1).
#   E3 again with 32 decode sequences (E15: 32 was much better than 24), both arms, the same loads as Tuesday.
#   E7 again: layout A with no KV connector, and long sessions that span the clear of pod B (tools/ghost_probe.py).
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
GW=http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
replay() { bash cluster/loadgen.sh "$1" replay --capture "$CAP" --mix "$2" --rate "$3" --duration "$4"; }
# A raw /metrics scrape in the middle of a load run (H-13, H-123): DESIGN.md pastes it.
scrape_at() { (sleep "$2"; python3 tools/scrape_now.py "metrics/$1/scrape-mid" >/dev/null 2>&1) & }
ghost_run() {  # clear the prefix cache of pod B (vllm-decode) at 150 s of the probe, then count the ghosts
  mkdir -p "metrics/$1"
  (sleep 150; date -u +%Y-%m-%dT%H:%M:%SZ > "metrics/$1/clear-time.txt"
   kubectl -n companion exec deploy/vllm-decode -c modelserver -- python3 -c \
     "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8200/reset_prefix_cache', method='POST')).status)") &
  bash cluster/loadgen.sh "$1" tool -m tools.ghost_probe --target "$GW" --capture "$CAP" \
    --sessions 24 --interval 12 --duration 300 --clear-at 150
  local B; B=$(kubectl -n companion get pods -l app=vllm-decode -o jsonpath='{.items[0].status.podIP}')
  python3 tools/ghosts.py --log "metrics/$1/envoy-access.log" --pod "$B" \
    --after "$(cat "metrics/$1/clear-time.txt")" --out "metrics/$1/ghosts.json"
}

step "W-1 E3 arm C with 32 decode sequences"
bash cluster/variant.sh e3-c32 --arg vllm-decode:--max-num-seqs=32
replay e3-c32-50 m4 "$R50" 600
scrape_at e3-c32-100 300
replay e3-c32-100 m4 "$R100" 600
replay e3-c32-150 m4 "$R150" 600

step "W-2 E3 arm A with 32 sequences on each pod"
bash cluster/variant.sh e3-a32 --router pd_mode=off --preset layout-a \
  --arg vllm-prefill:--max-num-seqs=32 --arg vllm-decode:--max-num-seqs=32
replay e3-a32-50 m4 "$R50" 600
scrape_at e3-a32-100 300
replay e3-a32-100 m4 "$R100" 600
replay e3-a32-150 m4 "$R150" 600

E7B=(--router pd_mode=off --preset layout-a --preset devmode
     --drop vllm-prefill:--kv-transfer-config --drop vllm-decode:--kv-transfer-config)
step "W-3 E7 again, precise index"
bash cluster/variant.sh e7b-precise "${E7B[@]}"
ghost_run e7b-precise
step "W-4 E7 again, approx index"
bash cluster/variant.sh e7b-approx "${E7B[@]}" --router prefix_index=approx
ghost_run e7b-approx

step "back to the base"
bash cluster/variant.sh baseline
step "wednesday experiments done"
