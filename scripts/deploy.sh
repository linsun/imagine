#!/usr/bin/env bash
# Deploy the vision app to the current kube context (default namespace).
# Creates the gemini-secret (from .env or $GEMINI_API_KEY), then applies the
# kustomization and waits for rollout.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="default"

# Load .env if present so GEMINI_API_KEY is available.
if [[ -f "${ROOT}/.env" ]]; then
  set -a; source "${ROOT}/.env"; set +a
fi

if [[ -z "${GOOGLE_API_KEY:-}" ]]; then
  echo "ERROR: GEMINI_API_KEY is not set (put it in .env or export it)." >&2
  exit 1
fi

echo "Creating secret..."
kubectl create secret generic gemini-secret \
  --namespace "${NS}" \
  --from-literal=GEMINI_API_KEY="${GOOGLE_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Applying manifests..."
kubectl apply -k "${ROOT}/k8s"

echo "Waiting for rollouts..."
kubectl -n "${NS}" rollout status deploy/vision-mcp --timeout=120s
kubectl -n "${NS}" rollout status deploy/backend --timeout=120s
kubectl -n "${NS}" rollout status deploy/frontend --timeout=120s

cat <<EOF

Deployed. Access the app with:

  kubectl port-forward -n ${NS} svc/frontend 8088:80

Then open http://localhost:8088
(Using localhost lets the browser webcam work without TLS.)
EOF
