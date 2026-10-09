#!/usr/bin/env bash
# Grafana check and images for the session 1 runs (a quiet time: no run is measured).
set -uo pipefail
cd "$(dirname "$0")/../.."
RUNS=(cap-b1 cap-b2 cap-b3 cap-b3-2 e1-8k e1-24k e2-soak e3-c-50 e3-c-100 e3-c-150 e5-d2560 e6-on e5-d4096
      e15-seqs-16 e15-seqs-32 e15-mnbt-8k e6-noprefix e6-fp8kv e3-a-50 e3-a-100 e3-a-150)
ARGS=(); for r in "${RUNS[@]}"; do ARGS+=("metrics/$r"); done
uv run python tools/grafana_shots.py check --runs "${ARGS[@]}"
uv run python tools/grafana_shots.py render --runs "${ARGS[@]}"
echo "grafana done: $?"
