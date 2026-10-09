#!/usr/bin/env bash
# From the laptop: SSH tunnels to node 2 (NFR-02). Usage: bash cluster/bootstrap/tunnel.sh <node2-public-ip>
# 6443 kube API, 3000 Grafana, 8501 Streamlit, 8000 Companion API, 8080 edge, 9090 Prometheus.
set -euo pipefail
NODE2="${1:?node 2 public ip}"
exec ssh -i ~/.ssh/lambda_ai -N -o ExitOnForwardFailure=yes \
  -L 6443:127.0.0.1:6443 -L 3000:127.0.0.1:30300 -L 8501:127.0.0.1:30851 \
  -L 8000:127.0.0.1:30800 -L 8080:127.0.0.1:30808 -L 9090:127.0.0.1:30909 "ubuntu@$NODE2"
