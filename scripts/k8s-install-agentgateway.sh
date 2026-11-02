#!/usr/bin/env bash
# Install the Gateway API and agentgateway's controller into the kind cluster,
# so k8s/gateway-api/ can program agentgateway. Run once per cluster, before
# scripts/k8s-up.sh. Safe to re-run.
#
#   KIND_CLUSTER        kind cluster name (default: kind)
#   GATEWAY_API_VERSION (default: v1.6.2, what agentgateway v1.6.0 is built against)
#   AGW_VERSION         (default: v1.6.0)
set -euo pipefail

CTX=kind-${KIND_CLUSTER:-kind}
GATEWAY_API_VERSION=${GATEWAY_API_VERSION:-v1.6.2}
AGW_VERSION=${AGW_VERSION:-v1.6.0}

echo "== Gateway API $GATEWAY_API_VERSION"
kubectl --context "$CTX" apply --server-side -f \
  "https://github.com/kubernetes-sigs/gateway-api/releases/download/$GATEWAY_API_VERSION/standard-install.yaml" >/dev/null

echo "== agentgateway $AGW_VERSION (controller in agentgateway-system)"
helm --kube-context "$CTX" upgrade -i agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds \
  --version "$AGW_VERSION" --namespace agentgateway-system --create-namespace >/dev/null
helm --kube-context "$CTX" upgrade -i agentgateway oci://cr.agentgateway.dev/charts/agentgateway \
  --version "$AGW_VERSION" --namespace agentgateway-system --wait >/dev/null
kubectl --context "$CTX" get gatewayclass agentgateway
