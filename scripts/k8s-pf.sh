#!/usr/bin/env bash
# Port-forwards from the laptop into the cluster, restarted if they drop.
#
#   :3005  photo inbox, via agentgateway  -- on ALL interfaces, so the phone
#          on the same wifi (or the laptop tethered to the phone) can reach it
#   :3007  the studio page: `kubectl ate proxy` to the studio actor, through
#          atenet-router, which wakes it (Substrate v1's router takes only
#          mTLS clients, so not via agentgateway)
#   :3006  what the page calls, via agentgateway (with CORS): the Director at
#          /director/ (also for ./imagine k8s demo), /photo/ and /stage/
#   :8888  viewfinder: /stage for the projector, / for the live camera
#   :3001  the MCP gateway (namespace mcp)
#   :16686 Jaeger     :15000 agentgateway UI     :15001 MCP gateway UI
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

CTX=kind-${KIND_CLUSTER:-kind}
NS=default
MCP_NS=mcp
PHOTO_INBOX_KEY=${PHOTO_INBOX_KEY:-sk-imagine-photos}
mkdir -p .pids logs

"$(dirname "$0")/k8s-down.sh" --forwards-only >/dev/null 2>&1 || true

fwd() {  # name, address, namespace, target, ports
  local name=$1 addr=$2 ns=$3 target=$4 ports=$5
  ( while true; do
      kubectl --context "$CTX" -n "$ns" port-forward --address "$addr" "$target" $ports \
        >>"logs/pf-$name.log" 2>&1 || true
      sleep 1
    done ) </dev/null >/dev/null 2>&1 &    # detached: never hold the caller's pipe open
  echo $! > ".pids/pf-$name"
}

ate_proxy() {  # name, actor, ports -- like fwd, through atenet-router
  local name=$1 actor=$2 ports=$3
  ( while true; do
      "${ATE_BIN:-kubectl-ate}" --context "$CTX" proxy actor "$actor" -a "$NS" $ports \
        >>"logs/pf-$name.log" 2>&1 || true
      sleep 1
    done ) </dev/null >/dev/null 2>&1 &
  echo $! > ".pids/pf-$name"
}

fwd photos   0.0.0.0   "$NS"   svc/agentgateway 3005:3005
ate_proxy studio studio 3007:80
fwd studio-api 127.0.0.1 "$NS" svc/agentgateway 3006:3006
fwd stage    127.0.0.1 "$NS"   svc/viewfinder   8888:8888
fwd jaeger   127.0.0.1 otel-system svc/jaeger   16686:16686    # Substrate's Jaeger
fwd agw-ui   127.0.0.1 "$NS"   deploy/agentgateway 15000:15000
fwd mcp      127.0.0.1 "$MCP_NS" svc/mcp-gateway  3001:3001
fwd mcp-ui   127.0.0.1 "$MCP_NS" deploy/mcp-gateway 15001:15000
# Keycloak: where you sign in to publish. The token's issuer is this address.
fwd keycloak 127.0.0.1 "$MCP_NS" svc/keycloak     8080:8080
sleep 3

ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<laptop-ip>")
cat <<EOF

  stage (projector)   http://localhost:8888/stage     click once to arm sound
  live camera         http://localhost:8888/          Safari, for the iPhone
  Director (terminal) ./imagine k8s demo       via :3006/director/
  studio (web)        http://localhost:3007/          wakes the studio actor; ./imagine k8s studio sleep
  agents (actors)     kubectl ate get actors -a default    workers: kubectl ate get workers
  traces              http://localhost:16686/     agents + gateway + Substrate itself
  agentgateway UI     http://localhost:15000/ui
  MCP gateway         http://localhost:3001/mcp    UI: http://localhost:15001/ui
  keycloak (sign-in)  http://localhost:8080/        admin console: admin / admin

  PHONE -> photo inbox
    page      http://$ip:3005/inbox#key=$PHOTO_INBOX_KEY
    Shortcut  POST http://$ip:3005/upload
              header  Authorization: Bearer $PHOTO_INBOX_KEY
              body    File = the latest photo (converted to JPEG)

  forwards run in the background; ./imagine k8s down stops them
EOF
