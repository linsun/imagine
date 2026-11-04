# The agents: kagent Agents and their gateway

Everything here except `harness.yaml.tmpl` is plain YAML that Argo CD syncs
from git (`../argocd/imagine-agents.yaml`, branch `kubecon-na`).

Director, Scout and DP run through kagent (kagent 1.0.0-rc1,
`api.kagent.dev/v1alpha3`), in namespace `default`. kagent compiles each Agent
into an ActorTemplate, starts one actor per session, and owns its lifecycle.

| File | What |
| --- | --- |
| `harness.yaml.tmpl` | The one BYO `Harness` all three share: image (by digest), command, env, pool, snapshots. A template, filled in and applied by `scripts/k8s-up.sh`; not synced by Argo CD |
| `egress-policy.yaml` | kagent `EgressPolicy`: the two gateways' ports + the OTel collector, nothing else |
| `agents.yaml` | The three `Agent`s: the shared Harness + an inline description |
| `gateway.yaml` | agentgateway (`default`): the Gateway, its listeners, proxy settings, tracing |
| `models.yaml`, `llm-policy.yaml` | :3000 the LLMs, and who may call them (the agents' virtual key) |
| `token-limit.yaml` | :3000 how many tokens they get: the demo knob, changed by PR on stage |
| `routes.yaml` | :3002 Scout, :3003 DP, :3006/director/: the routes to kagent's A2A endpoint |
| `github.yaml` | :3004 GitHub for publish-mcp, with the real token swapped in |

The worker pool is `../workerpool.yaml` (2 workers, shared with the studio),
and the routes to kagent's A2A endpoint (:3002, :3003, :3006/director/) are
`routes.yaml`.

```bash
./imagine k8s up          # builds Dockerfile.kagent, applies this folder
./imagine k8s demo        # the client speaks A2A v1 to the Director
kubectl get agents,harnesses
```

## How the agents meet kagent's BYO contract

All three run `agent/kagent_runtime.py` (`python -m agent.kagent_runtime`),
from `Dockerfile.kagent`, through one Harness. The runtime takes which agent it
is from kagent's config (`runtime.name`, the Agent's name), and the shared env
is the Director's; Scout and DP ignore what they don't use. It does what kagent expects of a
BYO image (`go/core/internal/substrate/actor_template.go` and
`go/core/internal/a2agateway/` at `v1.0.0-rc1`):

1. **A2A v1 over gRPC on :80.** kagent's gateway dials the actor through
   `atenet-router`. The port comes from kagent's config.
2. **`GET /readyz` on :8081**, kagent's wakeup probe. It only answers once
   gRPC is listening.
3. **Every task saved to kagent's controller.** The runtime saves each update
   to the controller's TaskStoreService and settles the final one. kagent's
   gateway returns the result from that store, not from the agent's stream,
   so an agent that skips this never answers.
4. **Config from kagent.** `KAGENT_CONFIG_JSON` and `KAGENT_AGENT_CARD_JSON`
   are written to `/config`, and the runtime reads both from there.

kagent's own Python package, `kagent-core`, does items 3 and 4. It isn't on
PyPI for 1.0, so `agent/requirements-kagent.txt` installs it from the kagent
repo at the cluster's tag; bump that tag when you upgrade kagent. kagent-core
needs a newer OpenTelemetry than `Dockerfile.agents` (the studio) pins, so the
kagent image is built separately.

The A2A 0.3 servers (`agent/a2a_server.py`, `agent/crew.py`) are unchanged.
The laptop still uses them. Scout and DP share their LLM call
(`crew.answer()`) between both servers.

## How kagent runs them

- **Snapshots keep `/data` only.** kagent pins `SNAPSHOT_FIDELITY_VOLUMES`, so
  a resumed actor is a fresh process. After every turn the Director saves its
  conversation, the latest photo, the last film and the crew's contexts to
  `/data/director.json`, and reads it back on a cold start. The photo and
  film files are already in `/data/outputs`.
- **One actor per session.** Each new A2A conversation gets its own actor. The
  client sends its first message without a contextId and prints the one kagent
  assigns; `DIRECTOR_CONTEXT=<it>` gets that same session (and Director) back
  after a restart. The Director remembers Scout's and DP's contexts, so it
  keeps reusing the same two actors instead of starting a new one for every
  question.
- **The crew is reached through kagent.** The Director calls Scout and DP
  (`CREW_A2A_PROTOCOL=v1`) at agentgateway :3002 and :3003.
  `routes.yaml` sends those to
  `kagent-controller.kagent:8083/agents/default/<name>/`, because kagent picks
  the actor names.
- **Resources come from kagent's defaults.** A Harness has no CPU or memory
  field; kagent books 1 CPU / 1Gi per actor, so a 2 CPU / 2Gi worker
  (`../workerpool.yaml`) holds two of them.
- **32 env vars per actor, kagent's included.** kagent adds about a dozen of
  its own (trust bundle, config, telemetry), so keep the Harness list short.

## Tested, and not yet

Tested locally:
- the runtime, as a process and as the built Alpine/arm64 image, against a
  stand-in for kagent's controller TaskStore: tasks created, updated,
  completed and settled
- unary and streaming gRPC calls, with progress lines arriving as WORKING
  updates
- the Director's restore after a cold restart
- the Director's v1 crew call and the client's v1 stream, against the a2a-sdk
  JSON-RPC server

On the cluster: the Director, Scout and DP through kagent's gateway and
controller, in the old `imagine` namespace. Not yet in `default`.
