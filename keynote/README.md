# imagine — the MCPCon + AGNTCon Japan demo

> **Branch layout.** This lives on `agntcon-japan`; `main` is still the imagine
> web app, untouched. The demo's files sit in `keynote/` so the branch stays a
> clean, reviewable diff against main, and `./imagine` at the repo root is a
> shim onto them — so the command is always `./imagine`, never the folder name.
> GitHub releases are repo-level, not branch-level, so the QR code and the
> `agntcon-mcpcon-japan-2026` release tag are unaffected by any of this.

One photo of the room → one still → one film with music → played out loud →
published as a GitHub release, with a QR code on the last frame. Every arrow
crosses **agentgateway**.

No browser is required to run any of it.

## The one line the demo exists to prove

```
$ env | grep -iE 'gemini|openai'      # on the agent host
$                                     # nothing
```

…and it still photographs the room, transforms it, makes the film, and publishes it.
`GEMINI_API_KEY` lives in agentgateway. So does the failover to OpenAI. Even
`vision-mcp` — which calls Gemini itself — gets its credential from the gateway
via `GEMINI_BASE_URL`. That's the **double hop**, and it's the thing no other
agentgateway demo does.

## Setup

```bash
brew install ...                       # nothing special; python3 + a webcam
cd imagine                             # repo root, on the agntcon-japan branch
./imagine install
./keynote/scripts/install-gateway.sh   # pins agentgateway >= v1.5.0-beta.1 into bin/
cp keynote/.env.example keynote/.env && $EDITOR keynote/.env   # GEMINI_API_KEY at minimum
```

## Run

**Running it live? See [RUNBOOK.md](RUNBOOK.md).**
**Film not what you wanted? See [PROMPTS.md](PROMPTS.md).**

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
`scripts/*.sh`; `make` still works and does the identical thing.

**Why not Docker?** The three things this does on stage — open the MacBook
camera, speak with `say`, and put a fullscreen window on the projector — are
host-native on macOS and none of them survive containerisation. The backing
services (gateway, Jaeger, vision-mcp) could be containerised; the camera and
the projector cannot, so the demo would still need a host process and you would
be debugging two runtimes instead of one on the morning of a keynote.

Then just talk to it:

```
you › take a photo of the room and make my audience do a Japanese dance
```

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

## Version requirement: agentgateway >= v1.5.0-beta.1

**The double hop needs a build newer than v1.4.1.** This is a real gap, not a
config mistake.

The google-genai SDK sends image requests to the bare Gemini API path:

```
POST /v1beta/models/gemini-2.5-flash-image:generateContent
```

v1.4.1's `extract_model_from_path` only understands **Vertex**-shaped paths
(`/publishers/google/models/X:generateContent`) and Bedrock's (`/model/X`).
A bare path falls through to body parsing — and Gemini request bodies have no
`model` field — so you get:

```
400  LLM request body is missing string field 'model'   (code: missing_model)
```

Fixed on `main` and in **v1.5.0-beta.1**, which added the branch that keeps the
whole path when there is no `/publishers/` segment, plus a test named
`extract_model_from_path_handles_bare_gemini_api_paths`.

**Two ways forward:**

1. **Upgrade** to v1.5.0-beta.1, pin that exact binary, re-run `make verify`.
   You get the full first-class treatment: token counts, cost attribution,
   guardrails, model attribution.
2. **Stay on v1.4.1** and uncomment the `image-gw` fallback at the bottom of
   `gateway/config.yaml`, then set `GEMINI_BASE_URL=http://localhost:3010`.
   That is a plain HTTP route with `backendAuth` injecting the credential, so
   vision-mcp *still* holds no API key — you keep the security story and the
   access logs, but lose token/cost metering for the image call (it becomes the
   same Passthrough tier Veo already sits in).

Option 1 is better for the talk. Option 2 is the safe harbour if the beta
misbehaves anywhere else — decide by day 3, not day 6.

## Gotchas that cost real time

**`frontendPolicies.http.maxBufferSize`** — agentgateway buffers the entire LLM
response to parse it for token accounting, and the default cap is **2 MiB**.
A real 1920x1080 photo coming back from Nano Banana exceeds that, giving
`502 failed to process LLM response: response was too large`. The same limit
applies to the request as a `413`. Set to 32 MiB, which is what agentgateway's
own UI ships as its default. Streaming and `routeType: passthrough` are exempt,
but you would lose token accounting.

**Trailing slash on HTTP MCP targets** — FastMCP serves `/mcp`; `/mcp/` 307s and
agentgateway does not follow redirects. It looks like the server is down.

**Bare Gemini paths need >= v1.5.0-beta.1** — v1.4.1 only parses Vertex-shaped
paths, so `/v1beta/models/X:generateContent` fails with `missing_model`.

**Models are addressed by the name in `llm.models[]`**, not the provider's id.

## Design decisions worth knowing

**Handles, not bytes.** The model never sees an image or a video. Tools return
`img_a1b2c3`; `agent/tools.py` swaps bytes in and out around each MCP call. This
is why a 10 MB film never enters a prompt.

**Publishing uses the GitHub REST API, not the GitHub MCP server.** That server
*cannot* carry an MP4 — `create_or_update_file`'s `content` is a JSON string and
the server base64-encodes it itself, so raw bytes can't survive. It's still an
MCP tool, so the traffic is still governed by the gateway.

**Release asset, not a commit.** A binary in git is effectively forever. A
release asset is deletable — which matters when the file is a photo of a real
audience.

**Video is metered as MCP, not as LLM.** Veo's `:predictLongRunning` falls into
agentgateway's Passthrough arm: no tokens, no cost, no dashboard row. Don't
script a "watch the video cost appear" beat — you'd be pointing at an empty
chart. It *is* metered one layer up, as a tool call.

**Tool names are prefixed** (`camera_capture`, `vision_transform_image`) because
the gateway federates four servers. The prompt uses the prefixed names. Don't
"fix" this with `prefixMode: never` — duplicates get silently dropped.

## One week

**Days 1–2 — does the spine work?**
`make up && make verify`. Step 3 is the one that matters: if `vision-mcp` can't
reach Gemini *through* the gateway, the talk's thesis is wrong and you need to
know on day one. Then camera permission, and one real PR merged end to end.

**Days 3–4 — the show.**
Real audience photo. Tune the Scout and DP prompts (they're in `agent/crew.py`)
— this is where the output quality actually comes from, and it's worth more of
your time than any code here. Wire the QR to `gallery_url()`. Shoot fallback
photos into `fallback/`.

**Day 5 — break it on purpose.**
Kill Gemini mid-run and watch the failover. Unplug the camera. Turn off wifi and
see what still works. Fix what you find.

**Days 6–7 — rehearse.**
`make preflight` each morning. **Record a complete backup of the demo working**
into `backup/demo-recording.mp4` and rehearse cutting to it. This is the highest
value hour of the week.

### Cut order, if you run out of time
The still-image step (go photo → video directly) · the DP agent (Director writes
the shot) · the PR (show the film, point at a pre-published URL).
**Never cut:** `failureMode: failOpen`, the offline rehearsal, the backup recording.

## Still on you

- **Consent**, announced before the camera opens. APPI treats facial images as
  personal data and this ends up on a public URL.
- **Which repo** — `GITHUB_REPO` defaults to a separate gallery repo on purpose.
- **Check `main` for rulesets.** You cannot approve your own PR, so a
  required-approval rule makes the live merge impossible.
- **The QR** — generate it now from `gallery_url()`; that URL is predictable
  before anything exists.
