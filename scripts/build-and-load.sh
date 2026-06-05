#!/usr/bin/env bash
# Build the three images and load them into the kind cluster.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Building images..."
docker build -t vision-mcp:dev "${ROOT}/mcp-server"
docker build -t vision-backend:dev "${ROOT}/backend"
docker build -t vision-frontend:dev "${ROOT}/frontend"

echo "Loading images into the current kind cluster"
kind load docker-image vision-mcp:dev 
kind load docker-image vision-backend:dev 
kind load docker-image vision-frontend:dev 

echo "Done."
