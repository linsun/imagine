# Runbook: what `./imagine k8s up` does, by hand

`./imagine k8s up` runs `scripts/k8s-up.sh`. It does the nine phases below, in
order. Each phase is safe to re-run on its own, so when one thing breaks, you
redo that phase, not all of them.

The second half is the live-debug cheat sheet: symptom, where to look, what to
re-run.

Where things live:

| Namespace | What |
| --- | --- |
| `default` | agentgateway (LLMs :3000, Scout :3002, DP :3003, GitHub :3004, photos :3005, studio's API and Director :3006), the viewfinder, the worker pool, the kagent Agents, and atespace `default` with every actor |
| `mcp` | the MCP gateway (:3001), the five MCP servers, Keycloak |
| `otel-system` | the OTel collector and Jaeger |

## 0. Shell setup (every new terminal)

```bash
# Use the config: it maps localhost:5001 to the registry for the node's
# containerd. Without it, every pod on a localhost:5001 image is ImagePullBackOff.
kind create cluster --name kagent --config k8s/kind/kind-config.yaml
```

```bash
curl https://raw.githubusercontent.com/kagent-dev/kagent/refs/heads/main/scripts/get-kagent | bash
# kagent uninstall
# follow kagent doc: https://kagent.dev/docs/kagent/1.x/setup/installation/#install-kagent
kagent --config kagent-config.yaml install --namespace kagent
kubectl rollout status deployment/kagent-controller -n kagent --timeout=300s
```

```bash
export GWAPI_VERSION=1.6.3
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v$GWAPI_VERSION/standard-install.yaml
helm upgrade -i --create-namespace \
  --namespace agentgateway-system \
  --version v1.7.0 agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds
  helm upgrade -i -n agentgateway-system agentgateway oci://cr.agentgateway.dev/charts/agentgateway \
--version v1.7.0
```

```bash
cd ~/imagine
set -a; . ./.env; set +a          # GEMINI_API_KEY, AGW_VIRTUAL_KEY, PHOTO_INBOX_KEY, ...
export CTX=kind-kagent NS=default MCP_NS=mcp REG=localhost:5001
```

`kubectl ate` is the plugin at `/usr/local/bin/kubectl-ate`. It must come
from the same Substrate release as the cluster: kagent 1.0.0-rc1 installs
kagent-dev/substrate v1.0.0-rc1, so take `kubectl-ate-darwin-arm64` from that
release (not agent-substrate/substrate). The scripts use the `kubectl-ate` on
PATH (override with `ATE_BIN`). A mismatched plugin gets "Unimplemented".

## 1. Preflight: are the platforms there?

```bash
kubectl --context $CTX get ns ate-system                               # Substrate
kubectl --context $CTX get crd agentgatewaypolicies.agentgateway.dev  # agentgateway controller
kubectl --context $CTX get crd agents.api.kagent.dev                  # kagent
curl -sf http://$REG/v2/ && echo registry ok                          # kind-registry
```

If the registry is down:

```bash
docker run -d --restart=always --name kind-registry -p 127.0.0.1:5001:5000 registry:2
docker network connect kind kind-registry
```

## 2. Images: build, check, push

Four images, all `linux/arm64`, all tagged `:dev` in the local registry:

| Image | Dockerfile | Runs |
| --- | --- | --- |
| `imagine-keynote` | `Dockerfile` (Debian) | viewfinder, the MCP servers |
| `imagine-vision` | `mcp-server` | vision-mcp (Gemini images, Veo video) |
| `imagine-agents` | `Dockerfile.agents` (Alpine) | the **studio** actor |
| `imagine-agents-kagent` | `Dockerfile.kagent` (Alpine) | **Director, Scout, DP** on kagent |

```bash
.venv/bin/python -m servers.post_mcp --render-voices     # bakes the spoken lines (macOS `say`) into the image
docker build --platform linux/arm64 -t $REG/imagine-keynote:dev .
docker build --platform linux/arm64 -f Dockerfile.agents -t $REG/imagine-agents:dev .
docker build --platform linux/arm64 -t $REG/imagine-vision:dev mcp-server
docker build --platform linux/arm64 -f Dockerfile.kagent -t $REG/imagine-agents-kagent:dev .
```

Check that the studio's image has no pointer-authentication code. Without this
check, a MEMORY snapshot dies on resume with "Illegal instruction" on Apple
silicon:

```bash
docker run --rm $REG/imagine-agents:dev python /usr/local/bin/check-pac.py "/lib/*.so*" "/usr/lib/**/*.so*" "/usr/local/lib/**/*.so*" | tail -1   # PACIASP total: 0
```

```bash
for i in imagine-keynote imagine-agents imagine-vision imagine-agents-kagent; do docker push $REG/$i:dev; done
```

Substrate only accepts images **pinned by digest**, never by tag. Look up the
two digests the actors use:

```bash
digest() { curl -sI -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json' http://$REG/v2/$1/manifests/dev | awk -F': ' 'tolower($1)=="docker-content-digest"{print $2}' | tr -d '\r'; }
export IMAGE=$REG/imagine-agents-kagent@$(digest imagine-agents-kagent)   # Director, Scout, DP
export STUDIO_IMAGE=$REG/imagine-agents@$(digest imagine-agents)          # studio
echo $IMAGE; echo $STUDIO_IMAGE
```

## 3. Config: namespace, secrets, settings

```bash
kubectl --context $CTX create namespace $MCP_NS --dry-run=client -o yaml | kubectl --context $CTX apply -f -
```

**Provider keys.** agentgateway gets all of them. In `mcp`, only vision-mcp
reads one: the Gemini key, because Veo goes direct. The agents never see them
and cannot reach the providers:

```bash
kubectl --context $CTX -n $NS create secret generic provider-keys \
  --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  --from-literal=GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
  --dry-run=client -o yaml | kubectl --context $CTX apply -f -
kubectl --context $CTX -n $MCP_NS create secret generic provider-keys \
  --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
  --dry-run=client -o yaml | kubectl --context $CTX apply -f -
```

**`imagine-env` ConfigMap, in `mcp`.** The MCP servers read their settings
from it. It is `.env` minus the secrets and the laptop-only URLs:
GEMINI/GOOGLE/OPENAI keys, GITHUB_TOKEN, AGW_LLM, AGW_MCP, SCOUT_A2A, DP_A2A,
PREVIEW_BROWSER and PHOTO_INBOX_KEY are dropped. Build it with the script,
which reads `.env` the way bash does:

```bash
export KIND_CLUSTER=kagent
scripts/k8s-env.sh --diff       # what would change
scripts/k8s-env.sh --restart    # apply, then restart every Deployment that reads it
kubectl --context $CTX -n $MCP_NS get configmap imagine-env -o yaml   # what the pods actually got
```

**Don't** use `kubectl create configmap --from-env-file=.env`. kubectl keeps
each value exactly as written: `KEY="a b"` arrives with its quotes (they end up
on screen in the credits), and `KEY=a  # note` arrives with its comment (a
GitHub repo name that doesn't exist).

**Gateway API keys.** The cluster stores only **hashes** of the keys.
`apiKey.name` (`director`, `publish`, `phone`) is the bucket the token limit
counts against:

```bash
apikey() { kubectl --context $CTX -n $NS create secret generic "$1" --from-literal="$2"="{\"keyHash\":\"sha256:$3\",\"metadata\":{\"name\":\"$4\"}}" --dry-run=client -o yaml | kubectl --context $CTX apply -f -; }
agw_hash=$(printf '%s' "$AGW_VIRTUAL_KEY" | shasum -a 256 | cut -d' ' -f1)
photo_hash=$(printf '%s' "${PHOTO_INBOX_KEY:-sk-imagine-photos}" | shasum -a 256 | cut -d' ' -f1)
apikey agw-virtual-key director "$agw_hash" director
apikey agw-publish-key publish  "$agw_hash" publish
apikey photo-inbox-key phone    "$photo_hash" phone
kubectl --context $CTX -n $NS create configmap gateway-costs --from-file=base-costs.json=gateway/base-costs.json --dry-run=client -o yaml | kubectl --context $CTX apply -f -
```

## 4. Traces: the collector and Jaeger

Both gateways and the Director send traces to
`opentelemetry-collector.otel-system`. The release install of Substrate
doesn't include it, and without it each gateway logs
`BatchSpanProcessor.ExportError ... DNS resolution which failed` every 5
seconds. Install it from Substrate's kind manifests (collector + Jaeger):

```bash
kubectl --context $CTX apply -f ~/src/github.com/linsun/substrate/manifests/ate-install/kind/otel-collector.yaml
kubectl --context $CTX -n otel-system rollout status deploy/opentelemetry-collector deploy/jaeger --timeout=180s
```

**Substrate's own settings.** `kagent install` leaves three things out of the
Substrate Helm release. `k8s/substrate-values.yaml` adds them, keeping
kagent's values:

- **The OTLP endpoint.** Without it the chart turns export off on the workers
  (`OTEL_*_EXPORTER=none`) and Jaeger never shows an actor. The file also
  samples every trace instead of 1%.
- **The kind registry for atelet.** atelet pulls actor images itself, not
  through containerd, so the node's `certs.d` mapping doesn't reach it. Inside
  atelet's pod `localhost:5001` is nothing, and every agent's template fails
  with `GoldenActorCrashed ... dial tcp [::1]:5001: connect: connection refused`.
  `--localhost-registry-replacement=kind-registry:5000` sends those pulls to
  the registry container.
- **The studio proxy on the router's allowlist** (phase 9).

```bash
helm --kube-context $CTX -n ate-system upgrade substrate oci://ghcr.io/kagent-dev/substrate/helm/substrate \
  --version 1.0.0-rc1 --reuse-values -f k8s/substrate-values.yaml --wait
kubectl --context $CTX -n ate-system get ds/atelet -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -o 'localhost-registry-replacement=[^"]*'
```

The upgrade restarts atelet, the router and the controller. Don't
`kubectl set env` or patch the components instead: the chart's worker setting
stays `none`, and the next Helm upgrade reverts it.

Workers that already exist keep `none` until they're replaced. The script
deletes them in phase 7 when they do, unless an actor is RUNNING (it would die
with its worker); then it prints the command to run later. Check the workers
with (`otlp` is the good answer):

```bash
kubectl --context $CTX -n $NS get pods -l ate.dev/worker-pool=imagine \
  -o jsonpath='{range .items[*]}{.metadata.name} {.spec.containers[0].env[?(@.name=="OTEL_TRACES_EXPORTER")].value}{"\n"}{end}'
```

A `kagent install` (reinstall or upgrade) drops all three settings;
`./imagine k8s up` puts them back.

## 5. Keycloak (sign-in for "publish")

Keycloak has to be up before the MCP gateway, because that gateway fetches
Keycloak's signing keys when it loads its config.

```bash
kubectl --context $CTX -n $MCP_NS create secret generic keycloak-seed --from-file=keycloak-seed/ --dry-run=client -o yaml | kubectl --context $CTX apply -f -
kubectl --context $CTX apply -f k8s/mcp/keycloak.yaml
kubectl --context $CTX -n $MCP_NS rollout status deploy/keycloak --timeout=300s
```

## 6. Always-on services and the two gateways

```bash
kubectl --context $CTX apply -f k8s/app.yaml                 # viewfinder (default)
kubectl --context $CTX apply -f k8s/mcp/servers.yaml         # the 5 MCP servers (mcp)
kubectl --context $CTX apply -f k8s/gateway-api/             # agentgateway (default): listeners, routes, policies
kubectl --context $CTX apply -f k8s/mcp/gateway.yaml         # mcp-gateway (mcp): :3001, publish sign-in
```

`kubectl apply` alone does **not** pick up a new `:dev` image or a changed
ConfigMap, because the pod spec didn't change. Restart the Deployments to load
them:

```bash
kubectl --context $CTX -n $NS rollout restart deploy viewfinder
kubectl --context $CTX -n $MCP_NS rollout restart deploy camera-mcp stage-mcp post-mcp publish-mcp vision-mcp
kubectl --context $CTX -n $NS rollout status deploy/agentgateway deploy/viewfinder --timeout=180s
kubectl --context $CTX -n $MCP_NS rollout status deploy/mcp-gateway deploy/camera-mcp deploy/stage-mcp deploy/post-mcp deploy/publish-mcp deploy/vision-mcp --timeout=180s
```

The `agentgateway` and `mcp-gateway` Deployments are created by the controller
from their Gateways, not applied by us. Route and policy changes reach them
live, without a restart.

## 7. Workers and the agents on kagent

One WorkerPool, `imagine`, with **2** gVisor workers. Every actor runs on it:
kagent's session actors and the studio.

```bash
export WORKER_IMAGE=ghcr.io/kagent-dev/substrate/ateworker-gvisor:$(kubectl --context $CTX -n ate-system get deploy ate-controller -o jsonpath='{.spec.template.spec.containers[0].image}' | sed 's/.*://')
envsubst '${WORKER_IMAGE}' < k8s/workerpool.yaml | kubectl --context $CTX apply -f -
kubectl ate --context $CTX get workers          # 2 ACTIVE in pool imagine
```

kagent owns the agents' ActorTemplates and actors. We only declare **what**
to run:

```bash
# One Harness for all three: image (by digest), command, env
GITHUB_REPO="${GITHUB_REPO:-}" GITHUB_RELEASE_TAG="${GITHUB_RELEASE_TAG:-}" \
  envsubst '${IMAGE} ${AGW_VIRTUAL_KEY} ${GITHUB_REPO} ${GITHUB_RELEASE_TAG}' \
  < k8s/kagent/harness.yaml | kubectl --context $CTX apply -f -

# Once only: the three per-agent Harnesses from before
kubectl --context $CTX -n $NS delete harness imagine-director imagine-scout imagine-dp --ignore-not-found

# What the actors may reach (the two gateways + the OTel collector), and the Agents themselves
kubectl --context $CTX apply -f k8s/kagent/egress-policy.yaml -f k8s/kagent/agents.yaml

for a in scout dp director; do kubectl --context $CTX -n $NS wait agent/$a --for=condition=Ready --timeout=300s; done
```

What "Ready" means: kagent compiled the Harness into an ActorTemplate, and
Substrate took its **golden snapshot** (it started the image once and froze it
after the readiness probe answered). No session actors exist yet. kagent
creates one per conversation (A2A contextId) on the first message.

If an Agent never gets Ready, look at its template. A filled-in ERROR column
means the golden snapshot failed, and Substrate doesn't retry it:

```bash
kubectl ate --context $CTX get actor-templates -a $NS        # GOLDEN TAG filled in, ERROR empty
```

`GoldenActorCrashed ... [::1]:5001: connection refused` is the atelet registry
setting from phase 4. Fix that, then change the Harness (a new image digest
does it) so kagent makes a new template, or delete the failed one:
`kubectl ate --context $CTX delete actor-template <name> -a $NS`.

A new image digest means a new template, so **existing sessions keep the old
code**. Click "new session" on the studio page (or restart `./imagine k8s demo`)
to get the new code.

## 8. The studio (a raw Substrate actor)

```bash
kubectl ate --context $CTX get atespaces $NS || kubectl ate --context $CTX create atespace $NS
kubectl --context $CTX apply -f k8s/studio/routes.yaml                                        # :3006 /photo/, /stage/, CORS
```

Templates are **immutable**: to change one, delete the actor and the template,
then recreate both. The script does this only when the rendered template's
hash changed. By hand:

```bash
kubectl ate --context $CTX delete actor studio -a $NS       # only if replacing
kubectl ate --context $CTX delete actor-template studio -a $NS          # only if replacing

IMAGE=$STUDIO_IMAGE TEMPLATE_HASH=manual envsubst '${IMAGE} ${TEMPLATE_HASH}' < k8s/studio/studio.yaml \
  | kubectl ate --context $CTX create actor-template -f -

# wait until goldenTag has a value (an errorMessage means the image failed to start)
kubectl ate --context $CTX get actor-template studio -a $NS -o json | grep -E 'goldenTag|errorMessage'

kubectl ate --context $CTX create actor studio -a $NS --template studio
```

A new actor starts **SUSPENDED** on the golden snapshot. That's correct: the
first request to :3007 (the proxy, phase 9) wakes it.

## 9. Port-forwards

`scripts/k8s-pf.sh` (also `./imagine k8s forward`) runs each forward below
in a restart loop, in the background. Logs go to `logs/pf-<name>.log`.

| Laptop | Target | What |
| --- | --- | --- |
| 0.0.0.0:3005 | svc/agentgateway | photo inbox (the phone uses this over wifi) |
| :3007 | actor default/studio | the studio page, via `kubectl ate proxy` through atenet-router |
| :3006 | svc/agentgateway | what the page calls: the Director at `/director/` (also `./imagine k8s demo`), `/photo/`, `/stage/` |
| :3001 | mcp svc/mcp-gateway | the MCP endpoint (the gateway UI's playground) |
| :8888 | svc/viewfinder | `/stage` on the projector |
| :16686 | otel-system svc/jaeger | traces |
| :15000 | deploy/agentgateway | gateway admin UI |
| :15001 | mcp deploy/mcp-gateway | MCP gateway admin UI |
| :8080 | mcp svc/keycloak | sign-in |

A single forward, by hand:

```bash
kubectl --context $CTX -n $NS port-forward svc/agentgateway 3006:3006
kubectl ate --context $CTX proxy actor studio -a $NS 3007:80
```

The proxy needs `spiffe://cluster.local/ns/ate-system/sa/atenet-client` on
the router's allowlist (phase 4's Helm upgrade, `k8s/substrate-values.yaml`).
Without it every request fails with a CONNECT error in `logs/pf-studio.log`.

---

## Re-run what, when

| You changed | Run |
| --- | --- |
| Director / Scout / DP code (`agent/*.py`) | phase 2 for `imagine-agents-kagent` (build, push, `IMAGE=`), then the Harness line in phase 7. New session. |
| Studio page or server (`agent/studio.*`) | phase 2 for `imagine-agents` (build, check, push, `STUDIO_IMAGE=`), then phase 8 with the delete lines |
| An MCP server (`servers/`, `mcp-server`) | phase 2 for that image, then `kubectl -n mcp rollout restart deploy/<name>` |
| The viewfinder (`servers/viewfinder.py`) | phase 2 for `imagine-keynote`, then `kubectl rollout restart deploy/viewfinder` |
| `.env` | `scripts/k8s-env.sh --restart` (just the settings), or `SKIP_BUILD=1 ./imagine k8s up` (everything, incl. keys) |
| A route or policy (`k8s/gateway-api/`, `k8s/mcp/gateway.yaml`) | `kubectl apply -f` that file. Live, no restart. |
| Everything is weird | `./imagine k8s up` |

## Show: several actors on one worker

```bash
kubectl ate --context $CTX get workers            # ACTORS column per worker, e.g. 2/1000
kubectl ate --context $CTX get actors -a $NS      # WORKER POD column: which worker each actor is on
```

With 2 workers, the studio plus a film (Director, Scout, DP) means at least
two actors share a worker. Each kagent actor books 1 CPU / 1Gi of a worker's
2 CPU / 2Gi, so a worker holds two of them. Old sessions left RUNNING take
room too; if an actor can't be placed ("no worker room"), suspend the stale
ones:

```bash
kubectl ate --context $CTX suspend actor <session-…> -a $NS
```

## Live debug: symptom, where to look

Follow the request's path. Each hop has its own check:

```
browser :3007 ─▶ kubectl ate proxy ─▶ atenet-router ─▶ studio actor
browser :3006 ─▶ port-forward ─▶ agentgateway ─┬
                                (default)      ├─▶ kagent-controller ─▶ Director actor ─┬─▶ :3000 LLM ─▶ Gemini
                                               │                                        ├─▶ mcp-gateway :3001 (mcp) ─▶ camera/vision/post/stage/publish-mcp
                                               │                                        └─▶ :3002/:3003 ─▶ kagent ─▶ Scout/DP actors
                                               └─▶ viewfinder (/photo/, /stage/)
```

**The page won't load (:3007)**

```bash
curl -s localhost:3007/api/self                                              # page server alive?
cat logs/pf-studio.log | tail                                                # proxy dropped, or CONNECT refused (allowlist)?
kubectl ate --context $CTX get actors studio -a $NS                          # SUSPENDED is fine (the request wakes it); CRASHED is not
kubectl --context $CTX -n ate-system logs deploy/atenet-router --tail=50     # wake/routing errors
kubectl ate --context $CTX logs actor studio -a $NS --source lifecycle       # resume history
```

Fastest fix: `./imagine k8s forward`. For a stuck actor:
`kubectl ate --context $CTX resume actor studio -a $NS`.

**The Director doesn't answer, or "waking…" never ends**

```bash
kubectl --context $CTX -n $NS get agents                                     # all READY True?
kubectl ate --context $CTX get actors -a $NS                                 # session-… actors: which one is RUNNING?
kubectl ate --context $CTX get workers                                       # room left? (see "several actors on one worker")
kubectl ate --context $CTX logs actor <session-…> -a $NS --source containers | tail -40
kubectl --context $CTX -n kagent logs deploy/kagent-controller --tail=80     # gateway/task-store errors
```

The Director's actor is the `session-…` row whose TEMPLATE starts
`default/director-`. Fastest fix: **new session** on the page. It starts a
fresh actor from the golden snapshot.

**It stalls about a minute between steps.** Look for `429 Too Many Requests`
on `:3000` in the Director's log. The token limit or the Gemini quota is
being hit:

```bash
scripts/token-limit.sh               # current limit
scripts/token-limit.sh reset         # back to the file's value
kubectl --context $CTX -n $NS logs deploy/agentgateway --tail=50 | grep -i 'rate\|429'
```

**A step fails with "! camera_… / vision_… / post_…"**: that's the MCP server
behind it, in `mcp`.

```bash
kubectl --context $CTX -n $MCP_NS logs deploy/vision-mcp --tail=50           # painting / film (Gemini, Veo)
kubectl --context $CTX -n $MCP_NS logs deploy/camera-mcp --tail=50           # the photo
kubectl --context $CTX -n $MCP_NS logs deploy/mcp-gateway --tail=50          # tool routing, publish sign-in
kubectl --context $CTX -n $MCP_NS rollout restart deploy/vision-mcp          # restart just that one
```

**"No photo in the inbox yet"**

```bash
curl -s localhost:3006/photo/latest                                          # has_photo, age_s
PHOTO_MATCH='.*' scripts/send-photo.sh                                       # newest file in ~/Downloads
```

Or drag the photo onto the studio page.

**Scout or DP fail**: the Director calls them through :3002/:3003, so check
their session actors the same way as the Director's (TEMPLATE `default/scout-…`
or `default/dp-…`).

**A pod in `ImagePullBackOff` on `localhost:5001/...`** (viewfinder, the MCP
servers): the node's containerd has no route to the registry. The cluster has
to be created with `k8s/kind/kind-config.yaml` (phase 0), whose `certs.d`
mount and `config_path` patch map `localhost:5001` to `kind-registry:5000`.
Also check `docker network inspect kind | grep kind-registry` (phase 1).

**An agent's template says `GoldenActorCrashed`**: see the end of phase 7.

**Gateway logs full of `BatchSpanProcessor.ExportError`**: the collector is
missing. Do phase 4.

**No Substrate spans in Jaeger (only the gateways and the Director)**:
Substrate has no OTLP endpoint. Do the second half of phase 4, then replace
the workers once no actor is RUNNING.
