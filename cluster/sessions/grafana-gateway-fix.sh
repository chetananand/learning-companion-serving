#!/usr/bin/env bash
# Fault 33 (2026-09-29): the gateway dashboard had two wrong panel queries. Render it again for every run of the
# day, then run the full panel check again, so each check.json matches the fixed dashboards.
set -uo pipefail
cd "$(dirname "$0")/../.."
RUNS=(cap-b1 cap-b2 cap-b3 cap-b3-2 e1-8k e2-soak e3-c-50 e3-c-100 e3-c-150 e5-d2560 e6-on e5-d4096
      e15-seqs-16 e15-seqs-32 e15-mnbt-8k e6-noprefix e6-fp8kv e3-a-50 e3-a-100 e3-a-150
      e4-hop e4-m2 e4-hop-nixl e10-fcfs e10-edf e12-abort e14-on-int e14-on-batch e14-off-int e14-off-batch
      e8-c-warmup e8-c-immediate e13-gate e16-k2 e16-k2-restart e16-k0 e16-k0-restart e7-precise e7-approx
      e8-a-ramp e8-a-jump e11-pass e11-frozen e11-stall e1-24k e1-24k-60s-cut)
ARGS=(); for r in "${RUNS[@]}"; do ARGS+=("metrics/$r"); done
uv run python tools/grafana_shots.py render --runs "${ARGS[@]}" --dashboards companion-gateway --force
uv run python tools/grafana_shots.py check --runs "${ARGS[@]}"
echo "gateway fix done: $?"
