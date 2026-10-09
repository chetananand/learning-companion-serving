#!/usr/bin/env bash
# Teardown part A (2026-09-29), before node 1 stops: the engine report and the data backup.
set -uo pipefail
cd "$(dirname "$0")/../.."
source cluster/state/session.env
bash cluster/t2.sh report
bash cluster/g0.sh backup
echo "teardown A done"
