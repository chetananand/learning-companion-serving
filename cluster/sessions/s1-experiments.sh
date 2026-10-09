#!/usr/bin/env bash
# S1-2 to S1-10 of docs/runbooks/experiments.md, in order (session 1, 2026-09-29). A driver Job with some
# failed turns shows FAILED; that is not a stop (runbook), so the script goes on and the log keeps it.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
GW=http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
replay() { bash cluster/loadgen.sh "$1" replay --capture "$CAP" --mix "$2" --rate "$3" --duration "$4"; }

step "S1-2 E1 (8K, then 24K)"
bash cluster/loadgen.sh e1-8k capacity --target "$GW" --tokens 8000 --levels 1,2,4,8,16,24,32
bash cluster/loadgen.sh e1-24k capacity --target "$GW" --tokens 24000 --levels 1,2,4,8,12,16,24 --repeats 2

step "S1-3 E2 soak and knee"
bash cluster/loadgen.sh e2-soak replay --capture "$CAP" --mix soak --rate 0.05 --ramp 0.05 --duration 1200
uv run python -m notebook.proof knee e2-soak --env >> cluster/state/session.env
source cluster/state/session.env
echo "rates: R50=$R50 R70=$R70 R100=$R100 R150=$R150"

step "S1-4 E3 arm C"
replay e3-c-50 m4 "$R50" 600
replay e3-c-100 m4 "$R100" 600
replay e3-c-150 m4 "$R150" 600

step "S1-5 E5 decode chunk 2560"
bash cluster/loadgen.sh e5-d2560 tool -m tools.pd_probe split --decode {decode} --prefill {prefill} --capture "$CAP"

step "S1-6 E6 on"
replay e6-on m2 "$R100" 420

step "S1-7 E5 decode chunk 4096"
bash cluster/variant.sh e5-d4096 --arg vllm-decode:--max-num-batched-tokens=4096
bash cluster/loadgen.sh e5-d4096 tool -m tools.pd_probe split --decode {decode} --prefill {prefill} --capture "$CAP"

step "S1-8 E15"
bash cluster/variant.sh seqs-16 --arg vllm-decode:--max-num-seqs=16 && replay e15-seqs-16 m4 "$R100" 420
bash cluster/variant.sh seqs-32 --arg vllm-decode:--max-num-seqs=32 && replay e15-seqs-32 m4 "$R100" 420
bash cluster/variant.sh mnbt-8k --arg vllm-prefill:--max-num-batched-tokens=8192 && replay e15-mnbt-8k m4 "$R100" 420

step "S1-9 E6 off and fp8"
bash cluster/variant.sh noprefix --drop vllm-prefill:--enable-prefix-caching --arg vllm-prefill:--no-enable-prefix-caching \
  --drop vllm-decode:--enable-prefix-caching --arg vllm-decode:--no-enable-prefix-caching && replay e6-noprefix m2 "$R100" 420
bash cluster/variant.sh fp8kv --arg vllm-prefill:--kv-cache-dtype=fp8 --arg vllm-decode:--kv-cache-dtype=fp8 \
  && replay e6-fp8kv m2 "$R100" 420

step "S1-10 E3 arm A"
bash cluster/variant.sh layout-a --router pd_mode=off --preset layout-a
replay e3-a-50 m4 "$R50" 600
replay e3-a-100 m4 "$R100" 600
replay e3-a-150 m4 "$R150" 600

step "back to the base"
bash cluster/variant.sh baseline
step "session 1 experiments done"
