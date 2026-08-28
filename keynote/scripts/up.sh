#!/usr/bin/env bash
# Start everything except the Director. Run `make demo` after this.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

PY="$PWD/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv -- run: make install"; exit 1; }
[ -n "${GEMINI_API_KEY:-}" ] || { echo "GEMINI_API_KEY not set in ./.env"; exit 1; }
mkdir -p .pids logs

start() {  # name, command...
  local name=$1; shift
  if [ -f ".pids/$name" ] && kill -0 "$(cat ".pids/$name")" 2>/dev/null; then
    echo "  $name already up"; return
  fi
  "$@" >"logs/$name.log" 2>&1 &
  echo $! > ".pids/$name"
  echo "  $name -> pid $(cat ".pids/$name")  (logs/$name.log)"
}

echo "starting:"
# vision-mcp: its Gemini egress goes back through the gateway (the double hop)
GEMINI_BASE_URL="${AGW_LLM:-http://localhost:3000}" \
MCP_PORT="${VISION_MCP_PORT:-8000}" \
  start vision-mcp "$PY" -c \
  "import sys; sys.path.insert(0,'../mcp-server'); from vision_mcp.server import main; main()"

start viewfinder "$PY" -m servers.viewfinder
start scout "$PY" -m agent.crew scout
start dp    "$PY" -m agent.crew dp

sleep 2
# agentgateway last: it spawns the stdio MCP servers itself
# Prefer the pinned project-local binary over whatever is on PATH.
if [ -x ./bin/agentgateway ]; then
  AGW=./bin/agentgateway
elif command -v agentgateway >/dev/null; then
  AGW=agentgateway
  echo "  note: using agentgateway from PATH, not a pinned build."
  echo "        ./scripts/install-gateway.sh pins one into ./bin"
else
  echo "  !! no agentgateway. run: ./scripts/install-gateway.sh"
  exit 1
fi
echo "  gateway: $($AGW --version 2>&1 | head -1)"
PATH="$PWD/.venv/bin:$PATH" start agentgateway "$AGW" -f ./gateway/config.yaml

sleep 3
echo
if curl -sf "http://localhost:${PREVIEW_PORT:-8888}/healthz" >/dev/null 2>&1; then
  echo "  viewfinder: http://localhost:${PREVIEW_PORT:-8888}/  <-- put this on the projector"
else
  echo "  !! viewfinder not answering -- see logs/viewfinder.log (camera permission?)"
fi
echo
if curl -sf http://localhost:16686/ >/dev/null 2>&1; then
  echo "  jaeger:     http://localhost:16686/   (service: agentgateway)"
else
  echo "  jaeger:     not running -- \`make jaeger\` BEFORE \`make up\`, traces only connect at startup"
fi
echo
echo "up. UI is probably http://localhost:15000/ui (or :4000/ui -- check logs/agentgateway.log)"
echo "next: make verify"
