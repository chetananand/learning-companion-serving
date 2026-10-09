#!/usr/bin/env bash
# Thursday 2026-10-01 on 8 x A100 80 GB: E3 with 32 decode sequences and E7 again, with load levels for this GPU.
# The levels of session 1 come from the H100 soak (100% = 0.9 scripts each second). On the A100, 0.45 already gave a
# TTFT p50 of 16 s (e3-c32-50), so these runs use 0.15, 0.30, and 0.45 scripts each second for both layouts.
# e3-c32-50 (layout C at 0.45) is the high level of layout C.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
GW=http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
replay() { bash cluster/loadgen.sh "$1" replay --capture "$CAP" --mix "$2" --rate "$3" --duration "$4"; }
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

step "E3 layout C, 32 decode sequences (the variant e3-c32 runs now): 0.15 and 0.30 scripts each second"
replay e3-c32-r015 m4 0.15 600
scrape_at e3-c32-r030 300
replay e3-c32-r030 m4 0.30 600

step "E3 layout A, 32 sequences on each pod: 0.15, 0.30, and 0.45 scripts each second"
bash cluster/variant.sh e3-a32 --router pd_mode=off --preset layout-a \
  --arg vllm-prefill:--max-num-seqs=32 --arg vllm-decode:--max-num-seqs=32
replay e3-a32-r015 m4 0.15 600
scrape_at e3-a32-r030 300
replay e3-a32-r030 m4 0.30 600
replay e3-a32-r045 m4 0.45 600

E7B=(--router pd_mode=off --preset layout-a --preset devmode
     --drop vllm-prefill:--kv-transfer-config --drop vllm-decode:--kv-transfer-config)
step "E7 again, precise index"
bash cluster/variant.sh e7b-precise "${E7B[@]}"
ghost_run e7b-precise
step "E7 again, approx index"
bash cluster/variant.sh e7b-approx "${E7B[@]}" --router prefix_index=approx
ghost_run e7b-approx

step "back to the base"
bash cluster/variant.sh baseline
step "A100 must-do runs done"
