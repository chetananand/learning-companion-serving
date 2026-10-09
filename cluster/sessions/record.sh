#!/usr/bin/env bash
# Browser recordings on the node (2026-10-01, the owner is away and asked for a screen recording):
#   demo     the demo questions in the Streamlit UI, one new chat for each (tools/record_video.py demo)
#   grafana  the Grafana dashboard "Pods / replicas / KEDA" during an E9 load (tools/record_video.py grafana)
# The recorder runs in the image companion/browser:dev with the host network of the node, so it reaches the
# NodePorts on 127.0.0.1. The Grafana password goes from the chart secret on the node into the container only.
# Output: metrics/videos/<name>/ (video.webm, screenshots, record.json).
# Usage: bash cluster/sessions/record.sh demo <name> [ids]          (default ids: D-03,D-07,D-08,D-09)
#        bash cluster/sessions/record.sh grafana <name> <seconds>
set -euo pipefail
cd "$(dirname "$0")/../.."
SSH_KEY="$HOME/.ssh/lambda_ai"
NODE=$(cat cluster/state/node2-ip)
ssh_node() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$NODE" "$@"; }
KIND="${1:?demo|grafana}"; NAME="${2:?name}"
VID=/var/lib/companion/app-data/videos
RUN="sudo docker run --rm --network host --shm-size 1g --user root -v \$HOME/companion/tools:/rec:ro -v $VID:/out"
rsync -az -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" tools/record_video.py "ubuntu@$NODE:companion/tools/"
ssh_node "mkdir -p $VID"
case "$KIND" in
  demo)
    IDS="${3:-D-03,D-07,D-08,D-09}"
    uv run --quiet python -c 'import json; from app.demo_check import QUESTIONS
print(json.dumps([{"id": q.id, "text": q.text, "mode": q.mode} for q in QUESTIONS]))' \
      | ssh_node "cat > $VID/questions.json"
    ssh_node "$RUN companion/browser:dev python /rec/record_video.py demo --questions /out/questions.json \
      --ids $IDS --out /out/$NAME"
    ;;
  grafana)
    SECS="${3:?seconds}"
    ssh_node "export GRAFANA_PASSWORD=\"\$(sudo k3s kubectl -n monitoring get secret kube-prometheus-stack-grafana \
      -o jsonpath='{.data.admin-password}' | base64 -d)\"; sudo --preserve-env=GRAFANA_PASSWORD \
      ${RUN#sudo } -e GRAFANA_PASSWORD companion/browser:dev python /rec/record_video.py grafana --seconds $SECS \
      --out /out/$NAME"
    ;;
  *) echo "unknown kind $KIND (demo, grafana)"; exit 2 ;;
esac
mkdir -p "metrics/videos/$NAME"
rsync -az --rsync-path="sudo rsync" -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" "ubuntu@$NODE:$VID/$NAME/" "metrics/videos/$NAME/"
ls -la "metrics/videos/$NAME"
