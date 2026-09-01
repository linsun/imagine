# Imagine — the AGNTCon + MCPCon Japan demo

> **Branch layout.** This lives on `agntcon-japan`; `main` is still the imagine
> web app, untouched. The demo's files sit in `keynote/` so the branch stays a
> clean, reviewable diff against main, and `./imagine` at the repo root is a
> shim onto them — so the command is always `./imagine`, never the folder name.
> GitHub releases are repo-level, not branch-level, so the QR code and the
> `agntcon-mcpcon-japan-2026` release tag are unaffected by any of this.

Transform the room into a movie and
publish it as a GitHub release. Every connections goes through **agentgateway**. Use agentgateway 1.5 or newer.

The agents don't use the keys, the credentials were injected via agentgateway. The MCP servers also don't use keys, except `vision-mcp` keeps `GEMINI_API_KEY` for the Veo call. The public MCP is gated via agentgateway auth using keycloak as the IDP.

## Setup

```bash
brew install ...                       # nothing special; python3 + a webcam
cd imagine                             # repo root, on the agntcon-japan branch
./imagine install
./keynote/scripts/install-gateway.sh   # pins agentgateway >= v1.5.0 into bin/
cp keynote/.env.example keynote/.env && $EDITOR keynote/.env   # GEMINI_API_KEY at minimum
```

## Run

```bash
./imagine start     # everything up, then the Director. This is the one command.
```

Or a step at a time:

```bash
./imagine up        # vision-mcp, scout, dp, then agentgateway (which spawns the stdio servers)
./imagine verify    # ← do this before a talk. Twelve checks, in risk order.
./imagine demo      # the Director REPL
./imagine status    # what is running, and where
./imagine down
```

`./imagine` is a plain stdlib Python script that shells out to the same
`scripts/*.sh`.

Then just talk to it:

```
you › take a photo of the room and make my audience do a Japanese dance
```

## Virtual keys and budgets (agentgateway 1.5.0)

The agents no longer send a placeholder string as their API key — they send
`AGW_VIRTUAL_KEY`, a **virtual** key that exists only in agentgateway. It
cannot talk to Gemini or OpenAI; it is an identity to charge. `gateway/config.yaml`
stores only its SHA-256, so nothing secret is committed:

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

## GitHub through the gateway

The publish MCP server used to read `GITHUB_TOKEN` and call `api.github.com`
itself. It no longer holds a credential at all — the same move already made for
Gemini, applied to the third provider.

```yaml
gateways:
  github-gw:
    port: 3004
routes:
- name: github-api
  gateways: [github-gw]
  matches:
  - path: {pathPrefix: /api}
  policies:
    urlRewrite:
      path: {prefix: /}            # /api/user -> api.github.com/user
  backends:
  - host: api.github.com:443
    policies:
      backendAuth:
        key:
          value: $GITHUB_TOKEN     # the gateway's copy, not the server's
      backendTLS: {}
- name: github-uploads             # same shape, /uploads -> uploads.github.com
```

**One port, two routes.** Release assets go to `uploads.github.com` and
everything else to `api.github.com` — two different services, so they are two
*routes* selected by path prefix. Not two hosts on one backend, and not two
backends on one route: both of those mean load balancing, which would send half
your API calls to the upload host.

`backendAuth`'s default location is `Authorization: Bearer <value>`, which is
exactly what GitHub wants, and it *overwrites* whatever the caller sent.


### Both sides of the hop

The route is guarded going in as well as going out. Inbound it requires the
same virtual key the agents use on the LLM listener; outbound `backendAuth`
replaces that header with the GitHub token:

```yaml
  policies:
    apiKey:                    # who are you
      mode: strict
      keys:
      - keyHash: sha256:...
        metadata: {name: publish}
  backends:
  - host: api.github.com:443
    policies:
      backendAuth:             # what you may borrow
        key: {value: $GITHUB_TOKEN}
```

## Publishing needs a person (Keycloak + MCP auth)

Publishing is the one action in the show that touches the real world, so the
gateway refuses it unless a person has signed in. This is agentgateway's MCP
auth feature — `mcpAuthentication` + `mcpAuthorization` on the MCP listener,
validating a real Keycloak JWT — not the apiKey policy.

```yaml
mcp:
  policies:
    mcpAuthentication:
      mode: optional
      issuer: http://localhost:8080/realms/my-realm
      audiences: [publish-mcp-server]
      provider: {keycloak: {}}       # first-class: derives JWKS, serves metadata
    mcpAuthorization:
      rules:
      - deny: 'mcp.tool.target == "publish" && !has(jwt.sub)'
```

`mode: optional` so the four other targets stay open — the authorization rule
is what refuses publish, and only publish. `provider: keycloak` is why Keycloak
and not a bare JWKS URL: agentgateway derives Keycloak's non-standard JWKS path,
serves protected-resource metadata, and returns `401` + `WWW-Authenticate` so a
standard MCP client can discover where to log in.

The person's Keycloak identity authorizes
  the publish *tool call* at the MCP listener; the gateway then injects the
  *GitHub* credential outbound via `backendAuth`. The person never holds the
  GitHub token; the GitHub token never carries the person's identity.

The gateway fetches the JWKS while *parsing* its config, so
Keycloak must be up before the gateway starts — otherwise the whole config is
rejected (the running gateway keeps its old config; it does not die). `up.sh`
and `preflight` check this, and `./imagine auth off` drops the policy in one
command if Keycloak is unavailable. Start Keycloak with your seed realm:

```bash
docker run -d --name keycloak -p 8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -v keycloak:/opt/keycloak/data/import:ro \
  quay.io/keycloak/keycloak:26.4.1 start-dev --import-realm
```

`./imagine login` / `logout` / `auth status` exist as manual controls, but the
demo path needs none of them.

## What's here

| | |
| --- | --- |
| `gateway/config.yaml` | Three listeners: `:3000` LLM, `:3001` MCP, `:3002`/`:3003` A2A (one per agent) |
| `servers/viewfinder.py` | **long-lived** process that owns the camera and serves the MJPEG preview |
| `servers/camera_mcp.py` | stdio, stateless. `preview_url`, `capture(countdown)`, `release`, `resume`, `list_images`, `load_image` |
| `servers/stage_mcp.py` | stdio. `announce` (macOS `say`, offline), `show` |
| `servers/post_mcp.py` | stdio. `add_credits` — Star Wars crawl over a starfield, ffmpeg |
| `servers/publish_mcp.py` | stdio. `publish_video` (release asset), `open_pr`, `gallery_url` |
| `agent/director.py` | The agent you talk to |
| `agent/crew.py` | Scout + DP as A2A servers |
| `agent/tools.py` | MCP client + the handle↔bytes swap |
| `cast.txt` | The companies in the room. Edit this. |
| `scripts/verify.sh` | The smoke test. Run it before believing anything |
| `scripts/preflight.sh` | Morning-of checklist |

`../mcp-server` (your existing vision-mcp) is reused unchanged except for
`genai_client.py`, which now honours `GEMINI_BASE_URL`.

