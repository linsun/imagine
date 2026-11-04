# The studio: a web page that sleeps

`http://localhost:3007/` is the keynote's web front end: talk to the Director,
watch the crew work, see the photo and the film. The page is served by an
Agent Substrate actor, `default/studio`, that is suspended until someone opens
it. The first request wakes it, and the page opens by saying how long it slept.

```bash
./imagine k8s up                           # deploys it, asleep
./imagine k8s studio                       # opens the page (and wakes the actor)
./imagine k8s studio sleep                 # back to sleep
./imagine k8s studio status
```

| File | What |
| --- | --- |
| `studio.yaml` | The ActorTemplate: `python -m agent.studio` on :80, MEMORY snapshots |
| `routes.yaml` | agentgateway :3006: `/photo/` and `/stage/` → viewfinder, plus CORS for the page |

`/director/` on :3006 goes to kagent; that route is in
`../agents/agents.yaml`. The actor runs on the same 2 workers as the
agents (`../workerpool.yaml`).

## Why it is a raw actor, not a kagent Agent

kagent starts one actor per A2A session and talks to it over gRPC. A web page is
neither: it is one actor, reached over plain HTTP. So it runs on Substrate
directly, next to the agents kagent manages, in the same atespace.

The trade: kagent suspends its idle actors for you; raw Substrate doesn't. The
studio stays awake until `./imagine k8s studio sleep`. That's deliberate for a
keynote: the page shouldn't drop off mid-talk.

## How the page knows it slept

The snapshot is MEMORY fidelity, so a resume continues the same Python process.
A thread ticks every second; a gap of more than 5 s in the wall clock means the
process wasn't running. `/api/self` reports the last gap, and the page shows it
along with how long the browser waited for the page (the wake itself).

## Two ports, no egress

On Substrate v1, atenet-router takes only mTLS clients on its allowlist, so
agentgateway can't send the browser to the actor. The page comes from
`kubectl ate proxy actor studio -a default 3007:80` (`scripts/k8s-pf.sh`),
which mints a client certificate for `ate-system/atenet-client` and opens a
CONNECT tunnel through the router per connection; the router wakes the actor.
`k8s/substrate-values.yaml` puts that identity on the router's allowlist.

The page calls everything else on agentgateway's :3006, which allows the
page's origin with CORS (`studio-cors` in `routes.yaml`). The actor needs no
egress policy (Substrate denies all egress by default):

- :3007 `/`, `/api/self`: the actor
- :3006 `/director/`: the Director, A2A v1 JSON-RPC (kagent)
- :3006 `/photo/latest`, `/photo/latest.jpg`, `/photo/upload`: the photo inbox. You can drop a photo on the page.
- :3006 `/stage/state`, `/stage/media`: what's on the projector, so the film plays on the page too

Both ports are forwarded to 127.0.0.1 only, so these routes carry no inbox key.

## Signing in to publish

The MCP gateway refuses `publish` without a signed-in person
(`mcp-publish-auth` in `../mcp/gateway.yaml`). The page signs in to
Keycloak itself (🔑 in the header, or automatically when you say "publish"):
Authorization Code + PKCE with the public `mcp-client`, in a popup, or a
full-page redirect if popups are blocked. The token is then sent as
`Authorization` with each message to the Director. kagent's controller
(`auth.mode: insecure`) passes it through to the actor, and the Director uses it
for its MCP calls.

This needs `mcp-client` to allow the studio: redirect URI
`http://localhost:3007/*` and web origin `http://localhost:3007` (the browser
calls Keycloak's token endpoint itself). Both are in `keycloak-seed/`. Keycloak
must be at http://localhost:8080 (`scripts/k8s-pf.sh`), because that is the
issuer the MCP gateway trusts. The token lives in the tab's sessionStorage and is
refreshed while the Keycloak session lasts.
