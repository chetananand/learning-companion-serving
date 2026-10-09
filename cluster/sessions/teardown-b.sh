#!/usr/bin/env bash
# Teardown part B (2026-09-29), after node 1 stops: the Prometheus snapshot first (the data is then safe), then
# the Grafana check and images for the runs of session 2 and the E1 rerun (runbook, teardown).
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
PROM_SNAPSHOT=1 bash cluster/g0.sh backup
RUNS=(e4-hop e4-m2 e4-hop-nixl e10-fcfs e10-edf e12-abort e14-on-int e14-on-batch e14-off-int e14-off-batch
      e8-c-warmup e8-c-immediate e13-gate e16-k2 e16-k2-restart e16-k0 e16-k0-restart e7-precise e7-approx
      e8-a-ramp e8-a-jump e11-pass e11-frozen e11-stall e1-24k)
ARGS=(); for r in "${RUNS[@]}"; do ARGS+=("metrics/$r"); done
uv run python tools/grafana_shots.py check --runs "${ARGS[@]}"
uv run python tools/grafana_shots.py render --runs "${ARGS[@]}"
echo "teardown B done: $?"
