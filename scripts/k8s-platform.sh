#!/usr/bin/env bash
# The platform agent (k8s/platform/): kagent's declarative runtime, in
# namespace `kagent`, on kagent's own worker pool. It troubleshoots the demo,
# raises the LLM token limit when asked, and opens PRs against
# linsun/imagine kubecon-na.
#
#   scripts/k8s-platform.sh            apply it (and the GitHub token Secret)
#   scripts/k8s-platform.sh status     is it ready, and which tools it got
#   scripts/k8s-platform.sh down       remove it
#
# GitHub token: PLATFORM_GITHUB_TOKEN from .env if set, else GITHUB_TOKEN.
# Best is a fine-grained token for linsun/imagine only, with Contents and
# Pull requests read & write.
set -euo pipefail
cd "$(dirname "$0")/.."

CTX=kind-${KIND_CLUSTER:-kind}
NS=kagent
k() { kubectl --context "$CTX" -n "$NS" "$@"; }

status() {
  k get agent platform harness platform 2>/dev/null || true
  k get remotemcpserver github kagent-tool-server
  echo "github tools discovered: $(k get remotemcpserver github \
    -o jsonpath='{range .status.discoveredTools[*]}{.name}{" "}{end}' | wc -w | tr -d ' ')"
}

case "${1:-up}" in
  status) status; exit ;;
  down)
    k delete -f k8s/platform/agent.yaml -f k8s/platform/github-mcp.yaml \
      -f k8s/platform/harness.yaml --ignore-not-found
    k delete secret platform-github --ignore-not-found
    exit ;;
  up) ;;
  *) echo "usage: $0 [up|status|down]"; exit 2 ;;
esac

[ -f .env ] && { set -a; . ./.env; set +a; }
TOKEN=${PLATFORM_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}
[ -n "$TOKEN" ] || { echo "set PLATFORM_GITHUB_TOKEN (or GITHUB_TOKEN) in ./.env"; exit 1; }

k create secret generic platform-github \
  --from-literal=authorization="Bearer $TOKEN" \
  --dry-run=client -o yaml | k apply -f - >/dev/null
k apply -f k8s/platform/harness.yaml -f k8s/platform/github-mcp.yaml \
  -f k8s/platform/agent.yaml
status
echo
echo "Talk to it in the kagent UI (agent kagent/platform), e.g.:"
echo "  why did the last film fail?"
echo "  raise the token limit to 500000"
echo "  open a PR that fixes it"
