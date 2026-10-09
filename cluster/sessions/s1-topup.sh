#!/usr/bin/env bash
# S1-1 again for b3 and the sweep, after fault 26 (the Prompt Guard lock). Then freeze again.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
bash cluster/loadgen.sh cap-b3-2 questions drive --sets /data/prompts/b3.jsonl --concurrency 4
kubectl -n companion create job "sweep-$(date +%s)" --from=cronjob/sweep
bash cluster/g0.sh freeze
kubectl -n companion get jobs | grep -E "sweep|cap-b3"
echo "top-up done"
