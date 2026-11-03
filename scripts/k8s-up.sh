#!/usr/bin/env bash
# Put the keynote demo on Agent Substrate, in the kind cluster on this laptop.
#
#   ./imagine k8s up        (or: scripts/k8s-up.sh)
#
# Two namespaces:
#   default   agentgateway (k8s/gateway-api/: LLMs, agents, GitHub, photos,
#             studio), the viewfinder (k8s/app.yaml), the worker pool
#             (k8s/workerpool.yaml), and the agents: Director, Scout, DP as
#             kagent Agents (k8s/kagent/) and the studio page as a raw
#             Substrate actor (k8s/studio/). Actors run only while asked
#             something.
#   mcp       the MCP gateway, the five MCP servers and Keycloak (k8s/mcp/)
# Plus otel-system: the OTel collector and Jaeger the gateways and the
# Director send traces to (Substrate's kind manifest).
#
# Needs Substrate (github.com/linsun/substrate:
#   hack/install-ate-kind.sh --atenet-dataplane=agentgateway --deploy-ate-system),
# agentgateway's controller (scripts/k8s-install-agentgateway.sh) and kagent.
#
# Safe to re-run. Env:
#   KIND_CLUSTER    kind cluster name (default: kind)
#   SUBSTRATE_DIR   substrate checkout (default: ~/src/github.com/linsun/substrate)
#   SKIP_BUILD=1    reuse the images already in localhost:5001
#   AGENTS="scout"  only wait for these Agents (default: scout dp director)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

CTX=kind-${KIND_CLUSTER:-kind}
NS=default
MCP_NS=mcp
REG=localhost:5001
SUBSTRATE_DIR=${SUBSTRATE_DIR:-$HOME/src/github.com/linsun/substrate}
AGENTS=${AGENTS:-scout dp director}
PHOTO_INBOX_KEY=${PHOTO_INBOX_KEY:-sk-imagine-photos}
k() { kubectl --context "$CTX" "$@"; }
# The kubectl-ate on PATH, unless ATE_BIN says otherwise. It must match the
# cluster's Substrate version: the API moves quickly, and a mismatched plugin
# fails with "Unimplemented" (the substrate checkout's bin/ build does today).
ATE_BIN=${ATE_BIN:-kubectl-ate}
ate() { "$ATE_BIN" --context "$CTX" "$@"; }

[ -n "${GEMINI_API_KEY:-}" ] || { echo "GEMINI_API_KEY not set in ./.env"; exit 1; }
[ -n "${AGW_VIRTUAL_KEY:-}" ] || { echo "AGW_VIRTUAL_KEY not set in ./.env"; exit 1; }
k get ns ate-system >/dev/null 2>&1 || { echo "Substrate is not installed on $CTX"; exit 1; }
k get crd agentgatewaypolicies.agentgateway.dev >/dev/null 2>&1 || {
  echo "agentgateway's controller is not installed on $CTX: scripts/k8s-install-agentgateway.sh"; exit 1; }
k get crd agents.api.kagent.dev >/dev/null 2>&1 || { echo "kagent is not installed on $CTX"; exit 1; }
command -v "$ATE_BIN" >/dev/null || { echo "no $ATE_BIN on PATH (set ATE_BIN)"; exit 1; }
ate get atespaces >/dev/null 2>&1 || {
  echo "$ATE_BIN can't talk to Substrate on $CTX; its version must match the cluster's:"
  ate get atespaces; exit 1; }

# Digest of an image in the local registry. Substrate only accepts images
# pinned by digest, never a tag.
digest() {  # repo tag -- empty if the image is not in the registry
  { curl -sf -I -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
    "http://$REG/v2/$1/manifests/$2" || true; } | awk -F': ' 'tolower($1)=="docker-content-digest"{print $2}' | tr -d '\r'
}

curl -sf -o /dev/null "http://$REG/v2/" || {
  echo "no registry at $REG (the kind-registry container). Recreate it:"
  echo "  docker run -d --restart=always --name kind-registry -p 127.0.0.1:5001:5000 registry:2"
  echo "  docker network connect kind kind-registry"
  exit 1; }

if [ -z "${SKIP_BUILD:-}" ]; then
  echo "== images -> $REG"
  # Services (Debian), the studio (Alpine: no pointer-authentication code, so
  # its actor survives a memory snapshot on arm64 -- see Dockerfile.agents),
  # and the agents behind kagent's BYO contract (agent/kagent_runtime.py).
  # The pod has no macOS `say`: record the film's spoken lines here, with the
  # real voice, and bake them into the image (servers/post_mcp.py VOICE_DIR).
  .venv/bin/python -m servers.post_mcp --render-voices 2>/dev/null
  docker build -q --platform linux/arm64 -t $REG/imagine-keynote:dev . >/dev/null
  docker build -q --platform linux/arm64 -f Dockerfile.agents -t $REG/imagine-agents:dev . >/dev/null
  docker build -q --platform linux/arm64 -f Dockerfile.kagent -t $REG/imagine-agents-kagent:dev . >/dev/null
  docker build -q --platform linux/arm64 -t $REG/imagine-vision:dev mcp-server >/dev/null
  docker run --rm $REG/imagine-agents:dev python /usr/local/bin/check-pac.py \
    "/lib/*.so*" "/usr/lib/**/*.so*" "/usr/local/lib/**/*.so*" >/dev/null \
    || { echo "imagine-agents contains PAC-signed code; the studio actor will not restore"; exit 1; }
  for img in imagine-keynote imagine-agents imagine-agents-kagent imagine-vision; do
    docker push -q $REG/$img:dev >/dev/null
  done
fi
IMAGE="$REG/imagine-agents-kagent@$(digest imagine-agents-kagent dev)"
STUDIO_IMAGE="$REG/imagine-agents@$(digest imagine-agents dev)"
echo "   agents: $IMAGE"

echo "== config (namespaces $NS, $MCP_NS)"
k create namespace "$MCP_NS" --dry-run=client -o yaml | k apply -f - >/dev/null
# Provider credentials. agentgateway gets all of them; in `mcp` only
# vision-mcp reads the Gemini key (Veo goes direct). The agents never see
# them -- they cannot even reach the providers.
k -n "$NS" create secret generic provider-keys \
  --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  --from-literal=GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
  --dry-run=client -o yaml | k apply -f - >/dev/null
k -n "$MCP_NS" create secret generic provider-keys \
  --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
  --dry-run=client -o yaml | k apply -f - >/dev/null
# The settings for the MCP servers: .env, read the way bash reads it.
KIND_CLUSTER=${CTX#kind-} scripts/k8s-env.sh
hash=$(printf '%s' "$PHOTO_INBOX_KEY" | shasum -a 256 | cut -d' ' -f1)
# The agents' virtual key: only its hash goes into the gateway's Secrets, so
# the gateway always accepts exactly the key in .env.
agw_hash=$(printf '%s' "$AGW_VIRTUAL_KEY" | shasum -a 256 | cut -d' ' -f1)
# API keys for the gateway's policies, as hashes plus the metadata the
# policies use (`apiKey.name`: the token limit's bucket).
apikey() {  # secret entry hash name
  k -n "$NS" create secret generic "$1" \
    --from-literal="$2"="{\"keyHash\":\"sha256:$3\",\"metadata\":{\"name\":\"$4\"}}" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
}
apikey agw-virtual-key director "$agw_hash" director
apikey agw-publish-key publish "$agw_hash" publish
apikey photo-inbox-key phone "$hash" phone
k -n "$NS" create configmap gateway-costs \
  --from-file=base-costs.json=gateway/base-costs.json \
  --dry-run=client -o yaml | k apply -f - >/dev/null

echo "== traces (otel-system)"
# The release install of Substrate leaves out its kind collector and Jaeger;
# without them every gateway logs an export error every 5 seconds.
if ! k -n otel-system get deploy opentelemetry-collector >/dev/null 2>&1; then
  k apply -f "$SUBSTRATE_DIR/manifests/ate-install/kind/otel-collector.yaml" >/dev/null
fi
k -n otel-system rollout status deploy/opentelemetry-collector --timeout=180s >/dev/null && echo "   collector ready"
k -n otel-system rollout status deploy/jaeger --timeout=180s >/dev/null && echo "   jaeger ready"

# Two settings `kagent install` leaves out of its Substrate release, added
# with k8s/substrate-values.yaml (kagent's own values are kept):
#   - the OTLP endpoint; without it the chart turns export off on the workers
#   - ate-system/atenet-client on the router's allowlist, for the studio's
#     `kubectl ate proxy` (scripts/k8s-pf.sh)
# Only when one is missing: the upgrade restarts the Substrate components
# (atelet: on the node).
OTLP_ENDPOINT=http://opentelemetry-collector.otel-system.svc:4317
have=$(k -n ate-system get deploy/ate-controller -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="OTEL_EXPORTER_OTLP_ENDPOINT")].value}')
allowed=$(k -n ate-system get configmap atenet-router-agentgateway-config -o jsonpath='{.data}' | grep -c 'ns/ate-system/sa/atenet-client' || true)
if [ "$have" != "$OTLP_ENDPOINT" ] || [ "${allowed:-0}" -eq 0 ]; then
  chart=$(helm --kube-context "$CTX" -n ate-system get metadata substrate -o json | python3 -c 'import sys,json; print(json.load(sys.stdin)["version"])')
  helm --kube-context "$CTX" -n ate-system upgrade substrate oci://ghcr.io/kagent-dev/substrate/helm/substrate \
    --version "$chart" --reuse-values -f k8s/substrate-values.yaml --wait --timeout 5m >/dev/null
  echo "   substrate $chart: OTLP endpoint and studio proxy access set"
fi

echo "== keycloak (publish sign-in, namespace $MCP_NS)"
# The realm seed holds a user's password hash, so it stays out of git
# (keycloak-seed/) and goes into a Secret, not the manifest.
[ -f keycloak-seed/my-realm-realm.json ] || { echo "missing keycloak-seed/my-realm-realm.json"; exit 1; }
k -n "$MCP_NS" create secret generic keycloak-seed --from-file=keycloak-seed/ \
  --dry-run=client -o yaml | k apply -f - >/dev/null
k apply -f k8s/mcp/keycloak.yaml >/dev/null
# Before the MCP gateway: it fetches Keycloak's signing keys while loading its config.
k -n "$MCP_NS" rollout status deploy/keycloak --timeout=300s >/dev/null && echo "   keycloak ready"

echo "== always-on services"
k apply -f k8s/app.yaml -f k8s/mcp/servers.yaml >/dev/null
# The controller pushes route and policy changes live: no restart needed.
k apply -f k8s/gateway-api/ -f k8s/mcp/gateway.yaml >/dev/null
# The controller creates each gateway's Deployment a moment after its Gateway.
for i in $(seq 1 30); do
  k -n "$NS" get deploy agentgateway >/dev/null 2>&1 && k -n "$MCP_NS" get deploy mcp-gateway >/dev/null 2>&1 && break
  sleep 2
done
MCP_DEPLOYS="camera-mcp stage-mcp post-mcp publish-mcp vision-mcp"
# :dev tags and ConfigMaps change without the pod spec changing.
k -n "$NS" rollout restart deploy viewfinder >/dev/null
k -n "$MCP_NS" rollout restart deploy $MCP_DEPLOYS >/dev/null
for d in agentgateway viewfinder; do
  k -n "$NS" rollout status deploy/$d --timeout=180s >/dev/null && echo "   $d ready"
done
for d in mcp-gateway $MCP_DEPLOYS; do
  k -n "$MCP_NS" rollout status deploy/$d --timeout=180s >/dev/null && echo "   $MCP_NS/$d ready"
done

echo "== substrate workers"
# The worker image matches the installed Substrate: same release as the
# ate-controller (e.g. v1.0.0-rc1).
version=$(k -n ate-system get deploy ate-controller -o jsonpath='{.spec.template.spec.containers[0].image}' | sed 's/.*://')
export WORKER_IMAGE=${WORKER_IMAGE:-ghcr.io/kagent-dev/substrate/ateworker-gvisor:$version}
envsubst '${WORKER_IMAGE}' < k8s/workerpool.yaml | k apply -f - >/dev/null
# Workers made before the controller had the OTLP endpoint (above) keep
# OTEL_TRACES_EXPORTER=none. Replace them -- but not under a RUNNING actor,
# which would die with its worker; then say how to do it later.
stale=$(k -n "$NS" get pods -l ate.dev/worker-pool=imagine -o jsonpath='{range .items[*]}{.metadata.name}={.spec.containers[0].env[?(@.name=="OTEL_TRACES_EXPORTER")].value}{"\n"}{end}' \
  | awk -F= '$2 != "otlp" {print $1}')
if [ -n "$stale" ]; then
  running=$(ate get actors -a "$NS" 2>/dev/null | grep -c ACTOR_STATE_RUNNING || true)
  if [ "${running:-0}" -eq 0 ]; then
    echo "   replacing $(echo $stale | wc -w | tr -d ' ') worker(s) to pick up the OTLP endpoint"
    k -n "$NS" delete pod $stale --timeout=120s >/dev/null   # gone before the ready count below
  else
    echo "   workers have OTLP export off, but $running actor(s) are RUNNING; once they're suspended:"
    echo "     kubectl --context $CTX -n $NS delete pods -l ate.dev/worker-pool=imagine"
  fi
fi
for i in $(seq 1 90); do
  ready=$(k -n "$NS" get workerpool imagine -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)
  [ "${ready:-0}" -ge 2 ] 2>/dev/null && break; sleep 2
done
echo "   workers ready: ${ready:-0}/2 (substrate $version)"

echo "== agents on kagent (namespace $NS)"
# kagent owns the agents' ActorTemplates and actors: we apply the Agents and
# their one Harness, and kagent starts one actor per session when a request
# comes in.
IMAGE="$IMAGE" AGW_VIRTUAL_KEY="$AGW_VIRTUAL_KEY" \
  GITHUB_REPO="${GITHUB_REPO:-}" GITHUB_RELEASE_TAG="${GITHUB_RELEASE_TAG:-}" \
  envsubst '${IMAGE} ${AGW_VIRTUAL_KEY} ${GITHUB_REPO} ${GITHUB_RELEASE_TAG}' \
  < k8s/kagent/harness.yaml | k apply -f - >/dev/null
k apply -f k8s/kagent/egress-policy.yaml -f k8s/kagent/agents.yaml >/dev/null
# The per-agent Harnesses from before, once no Agent points at them.
k -n "$NS" delete harness imagine-director imagine-scout imagine-dp --ignore-not-found >/dev/null
for agent in $AGENTS; do
  k -n "$NS" wait agent/"$agent" --for=condition=Ready --timeout=300s >/dev/null \
    && echo "   $agent ready" \
    || { k -n "$NS" get agent "$agent" -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'; exit 1; }
done

echo "== studio (a raw actor in atespace $NS, beside kagent's)"
# The page at :3007 (`kubectl ate proxy`, scripts/k8s-pf.sh). Not a kagent
# Agent: one actor, woken by any request, asleep until then. No egress: the
# browser fetches the rest from the gateway's :3006, with CORS.
ate get atespaces "$NS" >/dev/null 2>&1 || ate create atespace "$NS" >/dev/null
k apply -f k8s/studio/routes.yaml >/dev/null
# Left over from Substrate v0.6, when the gateway sent :3007 to atenet-router.
k -n ate-system delete referencegrant imagine-studio --ignore-not-found >/dev/null

# Templates are immutable: (re)create the template when it changed, and the
# actor when it is missing. A new actor starts SUSPENDED on its golden
# snapshot; the first request wakes it.
render() {  # hash
  IMAGE="$STUDIO_IMAGE" TEMPLATE_HASH="$1" envsubst '${IMAGE} ${TEMPLATE_HASH}' < k8s/studio/studio.yaml
}
want=$(render "" | shasum -a 256 | cut -c1-16)
# Unchanged (same image, same template) -> keep the template, the actor and its
# memory. Changed, or a failed golden snapshot -> replace both.
current=$(ate get actor-template studio -a "$NS" -o json 2>/dev/null | python3 -c '
import sys, json
t = json.load(sys.stdin); t = t.get("actorTemplates", [t])[0]
golden = (t.get("status") or {}).get("goldenSnapshotStatus") or {}
env = {e["name"]: e.get("value", "") for e in t["containers"][0].get("env", [])}
print("failed" if golden.get("errorMessage") else env.get("TEMPLATE_HASH", "none"))' 2>/dev/null || true)
if [ "$current" != "$want" ]; then
  if [ -n "$current" ]; then
    ate delete actor studio -a "$NS" >/dev/null 2>&1 || true
    ate delete actor-template studio -a "$NS" >/dev/null
  fi
  render "$want" | ate create actor-template -f - >/dev/null
  printf '   studio golden snapshot '
  tag=
  for i in $(seq 1 90); do
    out=$(ate get actor-template studio -a "$NS" -o json 2>/dev/null || true)
    tag=$(printf '%s' "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); t=d.get("actorTemplates",[d])[0]; print(((t.get("status") or {}).get("goldenSnapshotStatus") or {}).get("goldenTag",""))' 2>/dev/null || true)
    [ -n "$tag" ] && break
    printf '.'; sleep 3
  done
  [ -n "$tag" ] && echo " ready" || { echo " NOT READY:"; printf '%s\n' "$out" | head -40; exit 1; }
fi
ate get actors studio -a "$NS" >/dev/null 2>&1 || ate create actor studio -a "$NS" --template studio >/dev/null
echo "   studio: $(ate get actors studio -a "$NS" 2>/dev/null | tail -1)"

echo
exec "$(dirname "$0")/k8s-pf.sh"
