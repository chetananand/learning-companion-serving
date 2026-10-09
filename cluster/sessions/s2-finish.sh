#!/usr/bin/env bash
# After session 2 (2026-09-29): the E1 24K rerun (fault 29 cut 7 streams at level 24 in session 1), then the
# automated demo record (S2-1, make demo-check). The cluster is on the base setup.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
GW=http://companion-gateway-envoy.envoy-gateway-system.svc.cluster.local/v1
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
step "E1 24K rerun (the route timeout is 300 s now)"
[ -d metrics/e1-24k ] && mv metrics/e1-24k metrics/e1-24k-60s-cut
bash cluster/loadgen.sh e1-24k capacity --target "$GW" --tokens 24000 --levels 1,2,4,8,12,16,24 --repeats 2
step "demo-check (S2-1, the acceptance record J6)"
make demo-check
step "finish done"
