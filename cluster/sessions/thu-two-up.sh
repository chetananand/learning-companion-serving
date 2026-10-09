#!/usr/bin/env bash
# Thursday 2026-10-01, the two-node plan (a node 2 shape and an H100 shape have stock in one region): the
# bring-up after both launches (runbook steps B2 to B10). The launches stay manual, so each one is reported:
#   printf '<shape>\n' | uv run python cluster/lambda/lambda_ctl.py launch --node node2|node2-h100 --region R --approve
#   (when node 2 is active) printf '<shape>\n' | ... launch --node node1-t2|node1-t4 --region R --approve
# The engine runs the t2 overlay (one pod for each pool) for the must-do items. With node1-t4, cluster/t2.sh e9
# later lets KEDA add one pod to each pool (E9). A failed stage stops the script.
# Parts: "node2" (while node 1 has no stock yet), "node1" (after the node 1 launch), or both (no argument).
set -euo pipefail
cd "$(dirname "$0")/../.."
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
PART="${1:-all}"
if [[ "$PART" == all || "$PART" == node2 ]]; then
step "node 2: ips, bootstrap, kubeconfig, tunnel"
for s in ips bootstrap kubeconfig tunnel; do bash cluster/g0.sh "$s"; done
step "node 2: images and secrets"
bash cluster/g0.sh images
bash cluster/g0.sh secrets
step "node 2: install t2, then restore the data"
bash cluster/g0.sh install t2
bash cluster/g0.sh restore
fi
[[ "$PART" == node2 ]] && { step "node 2 is ready. Next: launch node 1, then: bash cluster/sessions/thu-two-up.sh node1"; exit 0; }
step "node 1: wait until it is active, then ips, join, images"
for _ in $(seq 60); do
  uv run --quiet python cluster/lambda/lambda_ctl.py list | grep -q "companion-node1 .* status=active" && break
  sleep 20
done
OVERLAY=t2 bash cluster/t2.sh ips
bash cluster/t2.sh join
bash cluster/t2.sh images
step "engine: wait, smoke, report"
bash cluster/t2.sh wait
bash cluster/t2.sh smoke
bash cluster/t2.sh report
step "bring-up done. Next: bash cluster/sessions/thu-two.sh"
