#!/usr/bin/env bash
# Argo CD for k8s/agents/ (k8s/argocd/imagine.yaml): git branch
# kubecon-na is the source of truth, so a merged PR -- the platform agent's
# token-limit change -- reaches the cluster with no kubectl.
#
#   scripts/k8s-argocd.sh            apply the Application, then status
#   scripts/k8s-argocd.sh status     sync state, the commit it's on vs. the branch, the live limit
#   scripts/k8s-argocd.sh sync       refresh now (Argo CD otherwise polls git every ~3 min)
#   scripts/k8s-argocd.sh down       remove the Application (k8s/agents/ stays in the cluster)
#   scripts/k8s-argocd.sh ui         Argo CD UI on https://localhost:8443 (user admin)
set -euo pipefail
cd "$(dirname "$0")/.."

CTX=kind-${KIND_CLUSTER:-kind}
APP=imagine
k() { kubectl --context "$CTX" "$@"; }

status() {
  k -n argocd get application "$APP" \
    -o jsonpath='{.status.sync.status} / {.status.health.status}, at {.status.sync.revision}{"\n"}' \
    | sed 's/^/argocd: /'
  echo "branch: $(git ls-remote origin refs/heads/kubecon-na | cut -f1) (origin kubecon-na)"
  echo "limit:  $(scripts/token-limit.sh)"
  # A failed sync (bad YAML in a merged PR) shows here.
  k -n argocd get application "$APP" \
    -o jsonpath='{.status.operationState.phase} {.status.operationState.message}{"\n"}' \
    | sed 's/^/last:   /'
}

k get namespace argocd >/dev/null 2>&1 || { echo "Argo CD is not installed (namespace argocd)"; exit 1; }
case "${1:-up}" in
  up) k apply -f k8s/argocd/imagine.yaml >/dev/null; status ;;
  status) status ;;
  sync)
    k -n argocd annotate application "$APP" argocd.argoproj.io/refresh=normal --overwrite >/dev/null
    echo "refresh asked; the sync follows in a few seconds"
    sleep 5; status ;;
  down)
    # No resources-finalizer on the Application, so this leaves k8s/agents/ running.
    k -n argocd delete application "$APP" --ignore-not-found ;;
  ui)
    echo "user admin, password: kubectl --context $CTX -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
    k -n argocd port-forward svc/argocd-server 8443:443 ;;
  *) echo "usage: $0 [up|status|sync|down|ui]"; exit 2 ;;
esac
