#!/usr/bin/env bash
# Jaeger, for the trace view you show while Veo is working.
#   ./scripts/jaeger.sh up | down | status
set -uo pipefail
NAME=keynote-jaeger
IMAGE="${JAEGER_IMAGE:-jaegertracing/all-in-one:1.60}"

case "${1:-up}" in
up)
  if ! command -v docker >/dev/null; then
    echo "docker not found. Install Docker Desktop, or run Jaeger another way --"
    echo "agentgateway just needs an OTLP collector on localhost:4317."
    exit 1
  fi
  if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "already running -> http://localhost:16686/"
    exit 0
  fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" \
    -e COLLECTOR_OTLP_ENABLED=true \
    -p 16686:16686 -p 4317:4317 -p 4318:4318 \
    "$IMAGE" >/dev/null
  for _ in $(seq 1 30); do
    curl -sf http://localhost:16686/ >/dev/null 2>&1 && break
    sleep 0.5
  done
  echo "Jaeger UI -> http://localhost:16686/"
  echo "  pick service 'agentgateway', Find Traces."
  echo "  NOTE: agentgateway only connects to the collector at startup --"
  echo "        if it was already running, restart it: make down && make up"
  ;;
down)
  docker rm -f "$NAME" >/dev/null 2>&1 && echo "stopped" || echo "not running"
  ;;
status)
  if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "running -> http://localhost:16686/"
  else
    echo "not running"
  fi
  ;;
*)
  echo "usage: $0 [up|down|status]"; exit 1 ;;
esac
