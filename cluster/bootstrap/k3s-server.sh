#!/usr/bin/env bash
# Node 2 (2 x A6000): the k3s server. Run on the node: sudo bash k3s-server.sh <private-ip> <public-ip> [role]
# ADR-010: flannel wireguard-native between the nodes, NodePorts only on 127.0.0.1 (L628).
# role: control (default, node 2) or gpu (node 1 alone, for the engine-only G1 run: cluster/g1-solo.sh).
# --advertise-address: G1 (2026-09-28) found that without it, the kubernetes Service points at the public
# IP (from --node-external-ip). The Lambda firewall blocks that path, so no pod reached the API or DNS.
set -euo pipefail
PRIVATE_IP="${1:?private ip}"
PUBLIC_IP="${2:?public ip}"
ROLE="${3:-control}"
source "$(dirname "$0")/../versions.env"

nvidia-smi -L                     # Lambda Stack ships the driver and the NVIDIA container toolkit
command -v nvidia-container-runtime >/dev/null || { echo "missing nvidia-container-runtime"; exit 1; }
apt-get install -y wireguard-tools >/dev/null

# Host folders of the pods. Some images run as a non-root user (the app: 65534), and hostPath creates
# root-owned folders. Mode 1777 (as /tmp) lets each pod write. Only our own pods run on this node.
mkdir -p /var/lib/companion/app-data /var/lib/companion/qdrant \
  /lambda/nfs/companion-state/models /lambda/nfs/companion-state/qdrant-snapshots
chmod 1777 /var/lib/companion/app-data /var/lib/companion/qdrant \
  /lambda/nfs/companion-state/models /lambda/nfs/companion-state/qdrant-snapshots

curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" sh -s - server \
  --node-ip "$PRIVATE_IP" --node-external-ip "$PUBLIC_IP" \
  --advertise-address "$PRIVATE_IP" \
  --flannel-backend=wireguard-native \
  --default-runtime nvidia \
  --disable traefik --disable servicelb \
  --kube-proxy-arg nodeport-addresses=127.0.0.1/32 \
  --write-kubeconfig-mode 600 \
  --node-label "companion.io/node=$ROLE"

until kubectl get nodes >/dev/null 2>&1; do sleep 2; done
kubectl get nodes -o wide
echo "join token: /var/lib/rancher/k3s/server/node-token"
