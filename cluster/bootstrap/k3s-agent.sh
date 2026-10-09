#!/usr/bin/env bash
# Node 1 (2 or 4 x H100): a k3s agent. Run on the node:
#   sudo bash k3s-agent.sh <server-private-ip> <token|-> <private-ip> <public-ip>
# With "-", the token comes from stdin (cluster/t2.sh pipes it from node 2, so no log shows it).
set -euo pipefail
SERVER_IP="${1:?server private ip}"
TOKEN="${2:?join token}"
if [[ "$TOKEN" == - ]]; then read -r TOKEN; fi
PRIVATE_IP="${3:?private ip}"
PUBLIC_IP="${4:?public ip}"
source "$(dirname "$0")/../versions.env"

nvidia-smi -L
command -v nvidia-container-runtime >/dev/null || { echo "missing nvidia-container-runtime"; exit 1; }
apt-get install -y wireguard-tools >/dev/null

# Host folders of the pods. Some images run as a non-root user (the app: 65534), and hostPath creates
# root-owned folders. Mode 1777 (as /tmp) lets each pod write. Only our own pods run on this node.
mkdir -p /var/lib/companion/app-data /var/lib/companion/qdrant \
  /lambda/nfs/companion-state/models /lambda/nfs/companion-state/qdrant-snapshots
chmod 1777 /var/lib/companion/app-data /var/lib/companion/qdrant \
  /lambda/nfs/companion-state/models /lambda/nfs/companion-state/qdrant-snapshots

# Reachability check first (action A9 if this fails: firewall rule for 6443/tcp, 10250/tcp, 51820/udp).
timeout 5 bash -c "</dev/tcp/$SERVER_IP/6443" || { echo "cannot reach $SERVER_IP:6443 (see action A9)"; exit 2; }

curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" K3S_URL="https://$SERVER_IP:6443" \
  K3S_TOKEN="$TOKEN" sh -s - agent \
  --node-ip "$PRIVATE_IP" --node-external-ip "$PUBLIC_IP" \
  --default-runtime nvidia \
  --kube-proxy-arg nodeport-addresses=127.0.0.1/32 \
  --node-label companion.io/node=gpu
