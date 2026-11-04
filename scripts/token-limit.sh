#!/usr/bin/env bash
# The demo knob: change how many LLM tokens the agents get, live. agentgateway's
# controller pushes the change to the proxy; the next LLM call sees it.
#
#   scripts/token-limit.sh                 show the current limit
#   scripts/token-limit.sh 200000          200000 tokens per minute
#   scripts/token-limit.sh 500 Hours       500 tokens per hour
#   scripts/token-limit.sh reset           back to k8s/agents/token-limit.yaml
#
# With Argo CD (k8s/argocd/), git is the source of truth: the platform agent
# changes the limit by PR instead. This is the fallback. Argo CD leaves a live
# change alone (no selfHeal) until the next commit on kubecon-na, which puts
# the file's value back.
set -euo pipefail
cd "$(dirname "$0")/.."

CTX=kind-${KIND_CLUSTER:-kind}
k() { kubectl --context "$CTX" -n default "$@"; }
show() {
  k get agentgatewaypolicy llm-token-limit \
    -o jsonpath='{range .spec.traffic.rateLimit.local[*]}{.tokens} tokens per {.unit}, per {.key}{"\n"}{end}'
}

case "${1:-}" in
  "") show ;;
  reset) k apply -f k8s/agents/token-limit.yaml >/dev/null; show ;;
  *)
    tokens=$1 unit=${2:-Minutes}
    k patch agentgatewaypolicy llm-token-limit --type merge -p \
      "{\"spec\":{\"traffic\":{\"rateLimit\":{\"local\":[{\"tokens\":$tokens,\"unit\":\"$unit\",\"key\":\"apiKey.name\"}]}}}}" >/dev/null
    show ;;
esac
