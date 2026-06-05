#!/usr/bin/env bash
# Create a local kind cluster for the vision app (idempotent).
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-vision}"

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "kind cluster '${CLUSTER_NAME}' already exists."
else
  echo "Creating kind cluster '${CLUSTER_NAME}'..."
  kind create cluster --name "${CLUSTER_NAME}"
fi

kubectl cluster-info --context "kind-${CLUSTER_NAME}"
