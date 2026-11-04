#!/usr/bin/env bash
# Stop the port-forwards. With --purge, also delete the demo: the `mcp`
# namespace and everything the demo put in `default` (the cluster, kagent,
# Substrate and otel-system stay).
set -uo pipefail
cd "$(dirname "$0")/.."
CTX=kind-${KIND_CLUSTER:-kind}

for f in .pids/pf-*; do
  [ -f "$f" ] || continue
  pid=$(cat "$f")
  pkill -P "$pid" 2>/dev/null
  kill "$pid" 2>/dev/null
  rm -f "$f"
done
echo "port-forwards stopped"

[ "${1:-}" = "--forwards-only" ] && exit 0
if [ "${1:-}" = "--purge" ]; then
  k() { kubectl --context "$CTX" "$@"; }
  ate() { "${ATE_BIN:-kubectl-ate}" --context "$CTX" "$@"; }
  # The agents first, so kagent removes their actors and templates.
  k -n default delete -f k8s/kagent/agents.yaml -f k8s/kagent/harness.yaml -f k8s/kagent/egress-policy.yaml --ignore-not-found
  # Argo CD first, or it would put k8s/agents/ back on the next commit. No
  # resources-finalizer, so this deletes only the Application.
  k -n argocd delete application imagine-agents --ignore-not-found 2>/dev/null
  ate delete actor studio -a default 2>/dev/null
  ate delete actor-template studio -a default 2>/dev/null
  k delete -f k8s/studio/routes.yaml -f k8s/agents/ -f k8s/mcp/github.yaml -f k8s/app.yaml -f k8s/workerpool.yaml --ignore-not-found
  k -n default delete secret provider-keys agw-virtual-key agw-publish-key --ignore-not-found
  k -n default delete configmap gateway-costs --ignore-not-found
  k delete namespace mcp --wait=false
  echo "demo deleted from default; namespace mcp deleting (kagent and the cluster are left alone)"
fi
