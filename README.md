# Imagine

Point a camera at the room, describe a film, and an agent crew turns the photo
into a short movie with end credits naming the people in the room, then
publishes it as a GitHub release the audience can take home.

Every connection goes through **agentgateway** (1.5.0 or newer): LLM calls, MCP
tool calls, agent-to-agent (A2A) calls, and GitHub. The agents hold no provider
keys. agentgateway injects the credentials, and the only exception is
`vision-mcp`, which keeps `GEMINI_API_KEY` for the Veo call.

The demo runs in two places:

- **On a laptop** (this README): host-native on macOS, because the camera, `say`
  and the fullscreen projector window don't work inside Docker.
- **On Kubernetes with Agent Substrate and kagent** (the KubeCon NA version):
  see [`k8s/README.md`](k8s/README.md).

The old imagine web app is gone from this branch. It is still on `main` and in
git history.

## The crew

```
you ─▶ Director ─▶ agentgateway :3000  LLM  ─▶ Gemini (OpenAI as fallback)
                ─▶ agentgateway :3001  MCP  ─▶ camera, stage, post, publish, vision
                ─▶ agentgateway :3002  A2A  ─▶ Scout   (enriches your idea)
                ─▶ agentgateway :3003  A2A  ─▶ DP      (shapes the shot)
       publish  ─▶ agentgateway :3004  ─▶ api.github.com / uploads.github.com
```

- **Director** (`agent/director.py`): the agent you talk to.
- **Scout** and **DP** (`agent/crew.py`): A2A agents that write the prompt.
  [`PROMPTS.md`](PROMPTS.md) explains where the words come from.
- **MCP servers** (`servers/`): agentgateway spawns them over stdio and
  federates them, with `vision-mcp` (`mcp-server/`) over HTTP, behind one
  endpoint.

## Setup

You need macOS, Python 3, ffmpeg, a webcam (or an iPhone through Continuity
Camera), and Docker for Keycloak and Jaeger.

```bash
./imagine install                      # virtualenv + dependencies
./scripts/install-gateway.sh           # puts agentgateway v1.5.0 in bin/
cp .env.example .env && $EDITOR .env   # GEMINI_API_KEY at minimum
```

[`.env.example`](.env.example) documents every setting. The ones you'll most
likely change:

| Variable | What it does |
| --- | --- |
| `GEMINI_API_KEY` | Required. agentgateway uses it for the LLM and image calls, and vision-mcp uses it for Veo |
| `OPENAI_API_KEY` | Optional fallback for the Director's reasoning |
| `GITHUB_TOKEN`, `GITHUB_REPO`, `GITHUB_RELEASE_TAG` | Where the film gets published. The token is only used by the gateway |
| `AGW_VIRTUAL_KEY` | The agents' identity at the gateway (see below) |
| `CAST_FILE` | The names for the credits. Defaults to `./cast`, then `./cast.txt` |
| `CREDITS_TITLE`, `CREDITS_*` | Credits heading, style, voice and music |
| `IMAGE_MODEL`, `VIDEO_MODEL` | Gateway model names, not provider ids. Run `./imagine models` to see what your key can use |

Edit [`cast`](cast) to list the companies or people in the room.

## Run

```bash
./imagine preflight   # day-of checks
./imagine start       # bring everything up, then open the Director
```

Then talk to it:

```
you › launch the camera
you › take the photo, count down from 3
you › make my audience do a Japanese dance
```

### The `./imagine` CLI

`./imagine` is a small stdlib-only Python script. Each subcommand runs one of
the `scripts/*.sh` files.

| Command | What it does |
| --- | --- |
| `install` | Create the virtualenv and install dependencies |
| `start` | Start the services, then open the Director |
| `up` / `down` | Start or stop the services only |
| `demo` | Open the Director (the services must already be up) |
| `status` | Show what is running and where |
| `verify` | Smoke-test every path through the gateway. Run it before a talk |
| `preflight` | Day-of checks |
| `logs [service] [-f]` | Tail service logs (for example `agentgateway` or `viewfinder`) |
| `models` | List the models your Gemini/OpenAI keys can use |
| `cameras` / `camera on\|off` | List camera indices, or release or take back the camera |
| `login` / `logout` / `auth on\|off\|status` | Keycloak sign-in and the gateway's MCP auth policy |
| `jaeger [up\|down]` | Start or stop Jaeger for the trace view |
| `clean` | Remove outputs, `data.db` and `.pids` |
| `k8s up\|demo\|studio\|forward\|status\|down\|purge` | The Kubernetes version (see [`k8s/README.md`](k8s/README.md)) |

[`RUNBOOK.md`](RUNBOOK.md) is the run of show: what to do from T-60 minutes,
on stage, and when something breaks.

## Virtual keys and budgets

The agents don't send a provider key. They send `AGW_VIRTUAL_KEY`, a
**virtual** key that only agentgateway knows about. It can't call Gemini or
OpenAI directly. It is an identity the gateway charges spending against.
`gateway/config.yaml` stores only its SHA-256, so nothing secret is committed:

```yaml
llm:
  policies:
    apiKey:
      mode: strict
      keys:
      - keyHash: sha256:<printf '%s' "$AGW_VIRTUAL_KEY" | shasum -a 256>
        metadata: {name: director}   # `name` is required when a key has budgets
        budgets:
        - name: daily-spend
          limit: {unit: USD, amount: 10}
          window: {rolling: 24h}
          onBudgetExceeded: Block
```

If you change `AGW_VIRTUAL_KEY`, update the `keyHash` to match.

## GitHub through the gateway

The publish MCP server has no GitHub credential. It calls
`localhost:3004`, and the gateway adds the token on the way out:

```yaml
routes:
- name: github-api
  gateways: [github-gw]              # :3004
  matches:
  - path: {pathPrefix: /api}
  policies:
    urlRewrite:
      path: {prefix: /}              # /api/user -> api.github.com/user
  backends:
  - host: api.github.com:443
    policies:
      backendAuth:
        key:
          value: $GITHUB_TOKEN       # the gateway's copy, not the server's
      backendTLS: {}
- name: github-uploads               # same shape, /uploads -> uploads.github.com
```

**One port, two routes.** Release assets go to `uploads.github.com` and
everything else goes to `api.github.com`. These are two different services, so
they are two *routes* selected by path prefix. Two hosts on one backend, or two
backends on one route, would both mean load balancing, which would send half
your API calls to the upload host.

The route is guarded in both directions. Inbound, it requires the same virtual
key the agents use on the LLM listener. Outbound, `backendAuth` replaces that
header with `Authorization: Bearer $GITHUB_TOKEN`.

## Publishing needs a person (Keycloak + MCP auth)

Publishing is the only action in the show that affects the real world, so the
gateway refuses it unless a person has signed in. This uses agentgateway's MCP
auth (`mcpAuthentication` + `mcpAuthorization` on the MCP listener), which
validates a real Keycloak JWT:

```yaml
mcp:
  policies:
    mcpAuthentication:
      mode: optional
      issuer: http://localhost:8080/realms/my-realm
      audiences: [publish-mcp-server]
      provider: {keycloak: {}}       # derives JWKS, serves resource metadata
    mcpAuthorization:
      rules:
      - deny: mcp.tool.target == "publish" && !has(jwt.sub)
```

- `mode: optional` keeps the other four targets open. The authorization rule
  refuses `publish` and nothing else.
- With `provider: keycloak`, agentgateway works out Keycloak's JWKS path, serves
  protected-resource metadata, and returns `401` + `WWW-Authenticate`, so a
  standard MCP client can discover where to log in.
- The person's identity authorizes the *tool call*. The gateway then injects the
  *GitHub* credential outbound. The person never holds the GitHub token, and the
  GitHub token never carries the person's identity.

The gateway fetches the JWKS when it loads its config, so **Keycloak must be up
before the gateway starts**. Otherwise the config is rejected and the running
gateway keeps its old one. `up.sh` and `preflight` check this. If Keycloak isn't
available, `./imagine auth off` removes the policy. Start Keycloak with the seed
realm:

```bash
docker run -d --name keycloak -p 8080:8080 -p 8443:8443 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -v "$(pwd)/keycloak-seed:/opt/keycloak/data/import:ro" \
  quay.io/keycloak/keycloak:26.4.1 start-dev --import-realm
```

## Traces

`agent/tracing.py` puts each film under one trace, covering the LLM calls, MCP
tool calls and A2A hops. Run `./imagine jaeger` and open
<http://localhost:16686/>. agentgateway and the agents send OTLP to
`localhost:4317`.

## What's here

| Path | What |
| --- | --- |
| `imagine` | The CLI |
| `gateway/config.yaml` | agentgateway on the laptop: `:3000` LLM, `:3001` MCP, `:3002`/`:3003` A2A, `:3004` GitHub |
| `agent/director.py` | The Director, as a terminal chat loop |
| `agent/crew.py` | Scout and DP as A2A servers |
| `agent/tools.py`, `agent/store.py` | MCP client, and the handle store so the model never sees image or video bytes |
| `agent/auth.py` | Browser login to Keycloak |
| `agent/tracing.py` | One trace per film |
| `agent/a2a_server.py`, `agent/client.py`, `agent/kagent_runtime.py` | The Director as an A2A server, its terminal client, and the kagent runtime for Kubernetes |
| `agent/studio.py`, `agent/studio.html` | The studio web page, served by a Substrate actor in Kubernetes |
| `servers/viewfinder.py` | Long-lived camera preview, owned by the browser |
| `servers/camera_mcp.py` | Stateless camera tools: `preview_url`, `capture`, `release`, `resume`, `list_images`, `load_image` |
| `servers/stage_mcp.py` | `announce` (macOS `say`, offline) and `show` on the projector |
| `servers/post_mcp.py` | `add_credits`: Star Wars-style crawl over a starfield, rendered with ffmpeg |
| `servers/publish_mcp.py` | `publish_video` (release asset), `open_pr`, `gallery_url` |
| `servers/_serve.py` | Runs an MCP server over stdio (laptop) or streamable HTTP (Kubernetes) |
| `mcp-server/` | vision-mcp (image + Veo video), unchanged except `genai_client.py` honours `GEMINI_BASE_URL` |
| `cast` | The names in the credits. Edit this |
| `k8s/` | The Kubernetes version: kind, agentgateway via the Gateway API, kagent agents, Argo CD, the platform agent |
| `scripts/` | What the CLI runs: `up.sh`, `down.sh`, `verify.sh`, `preflight.sh`, `k8s-*.sh`, `token-limit.sh` and others |
| `RUNBOOK.md`, `PROMPTS.md` | Run of show, and how the prompts are built |
