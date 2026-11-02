# The keynote demo on Agent Substrate (kind)

The same show as the laptop version (`../README.md`). The agents only run
while they're being asked something, and the photo comes from your phone
(or a drone, via the phone).

```
 phone ──(wifi)──▶ agentgateway :3005 ─▶ viewfinder (photo inbox + /stage page)
 browser ────────▶ agentgateway :3007 ─▶ atenet-router ── wakes ──▶ studio actor
 studio page, ./imagine k8s demo ▶ :3007/director/ ─▶ kagent ─▶ Director actor
                                                         │
     Director ─▶ agentgateway :3000 LLM ─▶ Gemini        │  namespace default
     Director ─▶ agentgateway :3002/:3003 ─▶ kagent ─▶ Scout, DP actors
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
     Director ─▶ mcp-gateway :3001 ─▶ camera, stage, post, publish, vision-mcp
                       └─ publish needs a Keycloak sign-in      namespace mcp
```

- **`default`**: agentgateway, the viewfinder, one WorkerPool (`imagine`, 2
  workers), the kagent Agents, and atespace `default`, which holds every actor.
  - Director, Scout and DP are kagent Agents (`kagent/`). kagent starts one
    actor per conversation and suspends it when idle.
  - The studio page is a raw Substrate actor (`studio/`) that sleeps until
    you open it.
  - With 2 workers, several actors share a worker: `kubectl ate get workers`.
- **`mcp`**: its own agentgateway, `mcp-gateway`, in front of the five MCP
  servers, plus Keycloak for the publish sign-in. The servers call back into
  `default` for the viewfinder, the image model (:3000) and GitHub (:3004).
- **Provider keys** live only in the `provider-keys` Secrets: agentgateway gets
  all of them, vision-mcp only the Gemini key (Veo goes direct). Actors carry
  just the virtual key, and their egress policy (`kagent/egress-policy.yaml`)
  lets them reach **only** the two gateways and the trace collector.

## Layout

| Path | What |
| --- | --- |
| `app.yaml` | The viewfinder (`default`) |
| `workerpool.yaml` | The one WorkerPool, `imagine`: 2 gVisor workers for every actor |
| `gateway-api/` | agentgateway (`default`), through the Gateway API: LLMs, agents, GitHub, photos, studio |
| `kagent/` | Director, Scout and DP as kagent Agents (`Dockerfile.kagent`); see `kagent/README.md` |
| `studio/` | The web page at :3007, a raw Substrate actor that sleeps until opened; see `studio/README.md` |
| `mcp/` | Namespace `mcp`: the MCP gateway (`gateway.yaml`), the MCP servers (`servers.yaml`), Keycloak |
| `RUNBOOK.md` | What `./imagine k8s up` does, step by step, and what to check when something fails on stage |

## Bring it up

Prerequisites:
- the kind cluster from `kind/`
- Substrate from github.com/linsun/substrate:
  `hack/install-ate-kind.sh --atenet-dataplane=agentgateway --deploy-ate-system`
- the Gateway API and agentgateway's controller: `scripts/k8s-install-agentgateway.sh`
- kagent 1.0.0-alpha17

```bash
./imagine k8s up          # build, push, deploy everything, port-forwards
./imagine k8s status      # deployments, worker pool, workers, actors
./imagine k8s demo        # talk to the Director
./imagine k8s down        # stop the port-forwards
./imagine k8s purge       # delete the demo (default's objects and namespace mcp)
```

Options:
- `SKIP_BUILD=1` reuses the images already pushed.
- `AGENTS="scout"` waits only for those Agents.

`k8s-up.sh` also installs Substrate's OTel collector and Jaeger
(`otel-system`) when they're missing, and points Substrate's controller, API
server and atelet at it (`OTEL_EXPORTER_OTLP_ENDPOINT`), which the workers then
inherit. The release install of Substrate leaves both out.

## The token limit (demo)

`gateway-api/token-limit.yaml` caps the agents' LLM tokens (2000 per minute,
per virtual key). Change it live, with no restart:

```bash
scripts/token-limit.sh            # show
scripts/token-limit.sh 200000     # raise it on stage
scripts/token-limit.sh reset      # back to the file
```

## Actors and workers

```bash
alias ate=kubectl-ate              # /usr/local/bin; must match the cluster's version
ate get workers                   # 2 workers; ACTORS shows how many each one hosts
ate get actors -a default         # WORKER POD shows where each actor runs
ate suspend actor studio -a default
```

Substrate places an actor on the less loaded of two workers it samples. Each
kagent actor books 1 CPU / 1Gi of a worker's 2 CPU / 2Gi, and the studio
500m / 256Mi, so the studio plus a film's three agents fill both workers, two
actors each. A session left RUNNING takes room too; suspend old ones if a new
actor gets "no worker room".

## The photo

Say **"use my latest photo"** (tool `camera_latest_photo`); the newest upload
wins. The Director needn't be running when the photo arrives.

- **iOS Shortcut:** Get Latest Photos (1) → Convert Image to JPEG → Get
  Contents of URL, set up as follows:
  - URL: `http://<laptop-ip>:3005/upload`
  - Method: POST
  - Header: `Authorization: Bearer <PHOTO_INBOX_KEY>` (default `sk-imagine-photos`)
  - Request Body: File

  For a drone, use Find Photos sorted by date, limit 1. It works once the
  drone app has saved the shot to Photos.
- **No setup:** open `http://<laptop-ip>:3005/inbox#key=<PHOTO_INBOX_KEY>`
  on the phone.
- **Venue wifi that blocks device-to-device traffic:** put the laptop on the
  phone's hotspot, then run `./imagine k8s forward` again for the new IP.
- **Live instead:** open `http://localhost:8888/` in Safari (Continuity
  Camera) and say "take the photo, count down from 3".

## On stage

1. Open `http://localhost:8888/stage` fullscreen and click once to arm sound.
2. Open the studio (`http://localhost:3007/`) or run `./imagine k8s demo`.
3. Say "use my latest photo", then "make them dance, <theme>", then "publish".
4. `ate get actors -a default` and `ate get workers` show the agents waking,
   and sharing the two workers, during the film.

## Things that bit us, so they don't bite again

- **The studio uses Alpine, not Debian (`Dockerfile.agents`).** On arm64 with
  pointer authentication (Apple Silicon), Debian's libc and libpython sign
  return addresses with a per-process key. A restored actor is a new process
  with a new key, so it dies ("Illegal instruction"), and the golden-snapshot
  or resume probe gets `connection refused`. Alpine's musl and Python contain
  no such instructions. `scripts/check-pac.py` checks this on every build.
- **Substrate ignores the image's `WORKDIR`.** The templates set
  `PYTHONPATH=/app`.
- **Images must be pinned by digest.** `k8s-up.sh` pushes to
  `localhost:5001` and resolves the digest.
- **Only literal env values.** Nothing secret goes in a template.
- **Match the `kubectl-ate` build to the cluster.** An older plugin lacks
  newer commands; `k8s-up.sh` builds one from the substrate checkout.
- **Never suspend mid-turn.** A suspend cuts in-flight requests, and a film
  turn takes about 2 minutes.

## Publishing sign-in (Keycloak)

Keycloak runs in `mcp` (`mcp/keycloak.yaml`), with the same image, realm seed
(`keycloak-seed/`, loaded into a Secret) and `mcp-client` as the laptop
version. The MCP gateway's policy is also the same: `publish` tools are hidden
and refused without a signed-in user (`jwt.sub`).

- **Sign in:** say "publish" on the studio page or in `./imagine k8s demo`.
  You log in at Keycloak on `http://localhost:8080` (a port-forward). The token
  is sent with each request, and the Director uses it for its MCP calls. The
  Director has no browser, so it never signs in itself.
- **Issuer:** tokens say `iss=http://localhost:8080/realms/my-realm`, because
  `KC_HOSTNAME` pins it. The MCP gateway checks that issuer, but fetches the
  signing keys in-cluster (`jwks` → `keycloak.mcp.svc`).
- **Order matters:** the MCP gateway loads the keys while parsing its config,
  so Keycloak must be ready first. `k8s-up.sh` waits for it.
- **Admin console:** `http://localhost:8080`, `admin` / `admin` (local only).

## Tracing

Both gateways (`imagine-gateway`, `mcp-gateway`) and the Director
(`imagine-director`) send traces to the OTel collector in `otel-system`, which
feeds Jaeger: `http://localhost:16686`.
