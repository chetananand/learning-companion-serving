#!/usr/bin/env bash
# Platform install on the k3s server (node 2), in the order of ADR-010. Idempotent (helm upgrade -i).
# Usage: bash cluster/install.sh <overlay: sim|dev|t2|t4>. The G0 runbook runs it on node 2 (helm is there)
# with SKIP_SECRETS=1 SKIP_RENDER=1, after the laptop made the secrets and rendered the router files.
set -euo pipefail
OVERLAY="${1:?overlay: sim|dev|t2|t4}"
cd "$(dirname "$0")/.."
source cluster/versions.env

step() { printf '\n== %s\n' "$*"; }

if [[ "${SKIP_SECRETS:-0}" != 1 ]]; then
step "Secrets from the local ~/.env (never committed)"
set -a; source "$HOME/.env"; set +a
for ns in companion guard data; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$ns" create secret generic companion-secrets \
    --from-literal=HF_TOKEN="${HF_TOKEN:?}" --from-literal=NOTION_API_KEY="${NOTION_API_KEY:-}" \
    --from-literal=TAVILY_API_KEY="${TAVILY_API_KEY:-}" --from-literal=OVERFLOW_API_KEY="${SUPERLINKED_API_KEY:-}" \
    --dry-run=client -o yaml | kubectl apply -f -
done
# The tokenizer sidecar (vllm-render) of the llm-d router chart reads this secret (G0, 2026-09-28).
kubectl -n companion create secret generic llm-d-hf-token --from-literal=HF_TOKEN="${HF_TOKEN:?}" \
  --dry-run=client -o yaml | kubectl apply -f -
fi

step "GPU sharing: HAMi on node 2, the NVIDIA device plugin on node 1"
helm repo add hami-charts https://project-hami.github.io/HAMi >/dev/null
# The HAMi device plugin runs on nodes with the label gpu=on (its chart default). Only node 2 gets it:
# node 1 uses the NVIDIA device plugin (G0, 2026-09-28: without the label, no GPU pod could start).
kubectl label node -l companion.io/node=control gpu=on --overwrite
helm upgrade -i hami hami-charts/hami --version "$HAMI_CHART_VERSION" -n kube-system
# Session 1 (2026-09-29): a GPU pod made before the HAMi webhook runs keeps the default scheduler and stays Pending.
kubectl -n kube-system rollout status deploy/hami-scheduler --timeout=10m
kubectl apply -f "https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${NVIDIA_DEVICE_PLUGIN_VERSION}/deployments/static/nvidia-device-plugin.yml"
kubectl -n kube-system patch daemonset nvidia-device-plugin-daemonset --type merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"companion.io/node":"gpu"}}}}}'

step "Gateway API and Inference Extension CRDs"
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"
# The llm-d router CRDs (InferenceObjective and InferenceModelRewrite, group llm-d.ai). The router chart has
# no CRDs, and our manifests hold the InferenceObjectives (G0, 2026-09-28: the apply failed without them).
kubectl apply --server-side -f "https://github.com/llm-d/llm-d-router/releases/download/${LLMD_ROUTER_VERSION}/manifests.yaml"

step "Monitoring: kube-prometheus-stack and the DCGM exporter"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm upgrade -i kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --version "$KUBE_PROMETHEUS_STACK_VERSION" -n monitoring --create-namespace \
  --set grafana.service.type=NodePort --set grafana.service.nodePort=30300 \
  --set prometheus.service.type=NodePort --set prometheus.service.nodePort=30909 \
  --set prometheus.prometheusSpec.scrapeInterval=5s \
  --set prometheus.prometheusSpec.nodeSelector."companion\.io/node"=control \
  --set prometheus.prometheusSpec.enableAdminAPI=true \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.ruleSelectorNilUsesHelmValues=false \
  --set grafana.nodeSelector."companion\.io/node"=control \
  --set grafana.imageRenderer.enabled=true \
  --set grafana.imageRenderer.image.tag="$GRAFANA_IMAGE_RENDERER_VERSION" \
  --set grafana.imageRenderer.nodeSelector."companion\.io/node"=control
# Grafana, its image renderer, and Prometheus stay on node 2, so the images of a session render after
# node 1 stops (tools/grafana_shots.py). The admin API gives the Prometheus snapshot of the backup.
helm repo add gpu-helm-charts https://nvidia.github.io/dcgm-exporter/helm-charts >/dev/null
helm upgrade -i dcgm-exporter gpu-helm-charts/dcgm-exporter --version "$DCGM_EXPORTER_CHART_VERSION" -n monitoring \
  --set serviceMonitor.enabled=true

step "KEDA"
helm repo add kedacore https://kedacore.github.io/charts >/dev/null
helm upgrade -i keda kedacore/keda --version "$KEDA_CHART_VERSION" -n keda --create-namespace \
  --set prometheus.operator.enabled=true --set prometheus.operator.serviceMonitor.enabled=true

step "Envoy Gateway with the AI Gateway values, the token rate-limit add-on (Redis), and the InferencePool add-on"
helm template eg-crds oci://docker.io/envoyproxy/gateway-crds-helm --version "$ENVOY_GATEWAY_VERSION" \
  --set crds.gatewayAPI.enabled=false --set crds.envoyGateway.enabled=true \
  | grep -v '^Pulled:' | grep -v '^Digest:' | kubectl apply --server-side -f -
helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm --version "$ENVOY_GATEWAY_VERSION" \
  -n envoy-gateway-system --create-namespace --skip-crds \
  -f "https://raw.githubusercontent.com/envoyproxy/ai-gateway/${ENVOY_AI_GATEWAY_VERSION}/manifests/envoy-gateway-values.yaml" \
  -f "https://raw.githubusercontent.com/envoyproxy/ai-gateway/${ENVOY_AI_GATEWAY_VERSION}/examples/token_ratelimit/envoy-gateway-values-addon.yaml" \
  -f "https://raw.githubusercontent.com/envoyproxy/ai-gateway/${ENVOY_AI_GATEWAY_VERSION}/examples/inference-pool/envoy-gateway-values-addon.yaml" \
  --set config.envoyGateway.rateLimit.backend.redis.url=redis.platform.svc.cluster.local:6379
helm upgrade -i envoy-ai-gateway-crd oci://docker.io/envoyproxy/ai-gateway-crds-helm --version "$ENVOY_AI_GATEWAY_VERSION" \
  -n envoy-ai-gateway-system --create-namespace
helm upgrade -i envoy-ai-gateway oci://docker.io/envoyproxy/ai-gateway-helm --version "$ENVOY_AI_GATEWAY_VERSION" \
  -n envoy-ai-gateway-system

step "Our manifests (overlay: $OVERLAY)"
[[ "${SKIP_RENDER:-0}" == 1 ]] || uv run python -m control.router.render   # node 2 has no uv: render on the laptop
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
# Server-side apply: the dashboard ConfigMap is too large for the client-side annotation.
kubectl kustomize --load-restrictor LoadRestrictionsNone "cluster/manifests/overlays/$OVERLAY" \
  | kubectl apply --server-side --force-conflicts -f -

step "llm-d router (EPP) with our rendered values"
# The chart tag keeps the "v" (GHCR has v0.11.0, not 0.11.0; checked 2026-09-27).
helm upgrade -i companion-router oci://ghcr.io/llm-d/charts/llm-d-router-gateway --version "$LLMD_ROUTER_VERSION" \
  -n companion -f cluster/manifests/router/generated/router-values.yaml

step "Done. Next: make smoke OVERLAY=$OVERLAY"
