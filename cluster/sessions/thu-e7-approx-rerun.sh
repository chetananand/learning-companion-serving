#!/usr/bin/env bash
# Thursday 2026-10-01 (fault 36): rerun E7 with the approx index. vLLM v0.30.0 POST /reset_prefix_cache returns
# HTTP 200 with {"success": false} when blocks are in use (24 live sessions), so the first approx clear most likely
# failed (no dip, 0 ghosts). This run asks with reset_running_requests=true and saves the body as evidence.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
GW=http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
RUN=e7b-approx2
[ -d metrics/e7b-approx ] && [ ! -d metrics/e7b-approx-reset-failed ] && mv metrics/e7b-approx metrics/e7b-approx-reset-failed
E7B=(--router pd_mode=off --preset layout-a --preset devmode
     --drop vllm-prefill:--kv-transfer-config --drop vllm-decode:--kv-transfer-config)
step "E7 again, approx index, with a checked reset"
bash cluster/variant.sh "$RUN" "${E7B[@]}" --router prefix_index=approx
mkdir -p "metrics/$RUN"
(sleep 150; date -u +%Y-%m-%dT%H:%M:%SZ > "metrics/$RUN/clear-time.txt"
 kubectl -n companion exec deploy/vllm-decode -c modelserver -- python3 -c \
   "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8200/reset_prefix_cache?reset_running_requests=true', method='POST')).read().decode())" \
   > "metrics/$RUN/clear-result.json" 2>&1) &
bash cluster/loadgen.sh "$RUN" tool -m tools.ghost_probe --target "$GW" --capture "$CAP" \
  --sessions 24 --interval 12 --duration 300 --clear-at 150
B=$(kubectl -n companion get pods -l app=vllm-decode -o jsonpath='{.items[0].status.podIP}')
python3 tools/ghosts.py --log "metrics/$RUN/envoy-access.log" --pod "$B" \
  --after "$(cat "metrics/$RUN/clear-time.txt")" --out "metrics/$RUN/ghosts.json"
echo "reset result: $(cat "metrics/$RUN/clear-result.json")"
step "back to the base"
bash cluster/variant.sh baseline
step "E7 approx rerun done"
