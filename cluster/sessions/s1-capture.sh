#!/usr/bin/env bash
# S1-1 of docs/runbooks/experiments.md, in one script (session 1, 2026-09-29).
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
bash cluster/loadgen.sh cap-sets questions build --out /data/prompts
bash cluster/loadgen.sh cap-b2 questions drive --sets /data/prompts/b2.jsonl --concurrency 4
make sweep
bash cluster/loadgen.sh cap-b1 questions drive --sets /data/prompts/b1.jsonl --concurrency 6
bash cluster/loadgen.sh cap-b3 questions drive --sets /data/prompts/b3.jsonl --concurrency 4
kubectl -n companion get jobs
bash cluster/g0.sh freeze
uv run python tools/grafana_shots.py check --runs metrics/cap-b1
echo "capture done"
