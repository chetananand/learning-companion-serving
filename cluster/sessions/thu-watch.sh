#!/usr/bin/env bash
# Thursday 2026-10-01: watch the stock every 30 s (read-only) and print the first decision, then exit.
#   PAIR <region>   a node 2 shape and an H100 shape in one region: the two-node plan
#   ONE <region>    only 4 x H100: the one-node plan (ADR-005, revision 2)
#   NODE2 <region>  a node 2 shape in us-southeast-1 before 19:00 PDT (17:30 until the owner's "1" at 18:20 PDT):
#                   launch node 2 alone, then wait for node 1
#                   (changed at 14:55 PDT: node 2 had stock at 10:05 PDT, and node 1 came there 31 min later)
# Usage: bash cluster/sessions/thu-watch.sh [end HHMM, default 2000]
# The regions of 2 x H100 and 4 x H100 this week: us-southeast-1 (and us-south-2 once).
cd "$(dirname "$0")/../.."
END="${1:-2000}"
while [ "$(TZ=America/Los_Angeles date +%H%M)" -lt "$END" ]; do
  uv run --quiet python cluster/lambda/capacity_watch.py \
    --types gpu_2x_a6000,gpu_1x_h100_sxm5,gpu_2x_h100_sxm5,gpu_4x_h100_sxm5 --once >/dev/null 2>&1
  decision=$(tail -1 metrics/lambda-capacity.jsonl | NOW="$(TZ=America/Los_Angeles date +%H%M)" python3 -c '
import json, os, sys
w = json.load(sys.stdin)["watched"]
n2 = set(w["gpu_2x_a6000"]) | set(w["gpu_1x_h100_sxm5"])
n1 = set(w["gpu_2x_h100_sxm5"]) | set(w["gpu_4x_h100_sxm5"])
if n2 & n1:
    print("PAIR", sorted(n2 & n1)[0], json.dumps(w))
elif w["gpu_4x_h100_sxm5"]:
    print("ONE", w["gpu_4x_h100_sxm5"][0], json.dumps(w))
elif "us-southeast-1" in n2 and int(os.environ["NOW"]) < 1900:
    print("NODE2 us-southeast-1", json.dumps(w))' 2>/dev/null)
  if [ -n "$decision" ]; then echo "$(TZ=America/Los_Angeles date +%H:%M) PDT $decision"; exit 0; fi
  sleep 30
done
echo "no usable stock by $END PDT"; exit 3
