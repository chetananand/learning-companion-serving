#!/usr/bin/env bash
# Thursday 2026-10-01: node 2 runs alone in us-southeast-1. Watch for a node 1 shape there (read-only) for at most
# <minutes>, then print "NODE1 <shape>" (4 x H100 first: E9 can then run too) or "TIMEOUT". Terminate node 2 on
# TIMEOUT (each try costs at most about 4.30 USD).
cd "$(dirname "$0")/../.."
MINUTES="${1:-60}"
END=$(( $(date +%s) + MINUTES * 60 ))
while [ "$(date +%s)" -lt "$END" ]; do
  uv run --quiet python cluster/lambda/capacity_watch.py --types gpu_2x_h100_sxm5,gpu_4x_h100_sxm5 --once >/dev/null 2>&1
  shape=$(tail -1 metrics/lambda-capacity.jsonl | python3 -c '
import json, sys
w = json.load(sys.stdin)["watched"]
for t in ("gpu_4x_h100_sxm5", "gpu_2x_h100_sxm5"):
    if "us-southeast-1" in w.get(t, []):
        print(t)
        break' 2>/dev/null)
  if [ -n "$shape" ]; then echo "$(TZ=America/Los_Angeles date +%H:%M) PDT NODE1 $shape"; exit 0; fi
  sleep 30
done
echo "$(TZ=America/Los_Angeles date +%H:%M) PDT TIMEOUT: no node 1 shape in us-southeast-1 for $MINUTES min"; exit 3
