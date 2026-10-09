#!/usr/bin/env bash
# Thursday 2026-10-01, the one-node layout (ADR-005, revision 2): the bring-up after the launch of companion-one.
# A failed stage stops the script. If the stage placed fails because a node 2 GPU pod is on a second GPU:
#   bash cluster/one.sh pin && bash cluster/one.sh placed, then run the stages from wait on.
set -euo pipefail
cd "$(dirname "$0")/../.."
step() { echo "== $(date -u +%H:%M:%S) UTC $*"; }
step "ips"
bash cluster/one.sh ips
step "up: bootstrap, kubeconfig, tunnel, images, secrets, install one, restore"
bash cluster/one.sh up
step "placed: the GPU plan, before the model loads"
bash cluster/one.sh placed
step "wait: the LMCache tier and the engine pods"
bash cluster/one.sh wait
step "gpus: the GPU plan and the KV cache memory (a full GPU for vLLM)"
bash cluster/one.sh gpus
step "smoke"
bash cluster/one.sh smoke
step "report"
bash cluster/one.sh report
step "bring-up done. Next: bash cluster/sessions/thu-one.sh"
