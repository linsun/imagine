# The audience is the cast — v2

**Demo design for the MCPCon / AGNTCon Japan keynote.**
agentgateway mediates every arrow: agent→MCP, agent→agent, agent→LLM.
Runway: under 3 weeks. Standalone binary, pin **v1.4.1**. Reusable for KubeCon.

**v2 incorporates:** live prompt entry, a local **Qwen/Ollama Director**, one photo →
one video, bilingual voice announcement, and publishing via a **GitHub PR** instead
of a hosted gallery.

---

## 0. Two corrections, unchanged from v1

**agentgateway is not a CNCF project.** Linux Foundation (2025-08-25), hosted by the
**Agentic AI Foundation** since 2026-06-04. `CHARTER.md` says "a Series of LF
Projects, LLC." kgateway and agentregistry are the CNCF ones. AGNTCon + MCPCon Japan
is an LF event — this gets noticed.

**Pin ** v1.5.0 landed 2026-08-27; cadence is a minor every 4–5 weeks.

---

## 1. Two blockers your v2 choices surfaced

Both were found by reading source, not docs. Read these before anything else.

### 🔴 BLOCKER 1 — the GitHub MCP server cannot carry the MP4

Verified in `pkg/github/repositories.go`. The `create_or_update_file` tool's
`content` parameter is documented as:

> *"Content of the file, exactly as it should appear once written. **Do not
> base64-encode it; this server does that before calling the REST API.**"*

So:

- `content` is a **JSON string**. Raw MP4 bytes aren't valid UTF-8 and can't survive
  the JSON-RPC wire format.
- If you pre-encode, you commit the **base64 text**, not the video.
- `push_files` is worse — Git Trees API, plain UTF-8, no base64 path at all.
- There is **no release-asset tool and no blob tool** among the server's 91 tools.
  The entire `git` toolset is one read-only tool.
- Even ignoring encoding: 10 MB → 13.4 MB base64 in a single tool-call argument
  would blow model context and gateway body limits.
- Bonus hazard: issue #2182 — `create_or_update_file` **silently truncates** larger
  content and still returns a valid commit SHA.

**The fix, and it's a better story anyway:** split the job across two backends.

| Backend | Job | Credential |
| --- | --- | --- |
| `publish-mcp` (yours, ~50 lines) | `publish_video(handle) → url`. Uploads the MP4 as a **release asset** or via blob+tree+commit, reading bytes **from local disk** so they never touch the model. | GitHub PAT, held by the gateway |
| **GitHub MCP** (official, remote) | `create_branch`, `create_or_update_file` (a *small text* gallery entry), `create_pull_request` | GitHub PAT, held by the gateway |

One gateway, two backends, two credential policies, and the agent sees neither
token. That's a stronger agentgateway demo than a single server would have been.

### 🔴 BLOCKER 2 — local Qwen tool calling is genuinely fragile

You can absolutely run the Director on Qwen, but not naked. The open Ollama issues
cluster **exactly** where a Director lives — multi-turn conversations with long tool
history:

- **#16383 (open)** — qwen3.6 rides the `qwen35` parser, intermittently emits stray
  close tags, parser throws **HTTP 500**. Verbatim from the issue: *"Single-shot
  requests rarely fail (20/20 succeeded). **Multi-turn conversations with extensive
  history trigger failures reliably.**"*
- **#17778 (open, one week old)** — qwen3.8 specifically: `no user query found in
  messages`, HTTP 500, **only after returning tool results in a loop**. The reporter
  says 3.5 and 3.6 work with identical code.
- **#17276** — parser error returns **HTTP 200** while discarding output. macOS
  Apple Silicon, "mid-to-late tool calls in sequences."
- **#14958** — tool calls **silently dropped** when the system prompt exceeds
  ~1600 tokens. Response comes back with empty content and no `tool_calls`, despite
  non-zero completion tokens.
- **goose #6883** — qwen3-coder via Ollama's OpenAI-compat API: **≤5 tools works;
  at 5–6 tools the model flips to emitting XML tool calls inside `content`**. Goose
  fixed it *client-side*.

Every fix in the wild is a client-side tolerant re-parse. **A gateway cannot repair
this** — agentgateway does translation and policy, not tool-payload recovery.

**Model choice: `qwen3.6:35b-a3b`.** MoE, 3B active, ~23 GB at Q4. 34–55 tok/s on
M4/M5 Max with sub-second TTFT — faster than the dense `27b` despite being bigger on
disk. Avoid `qwen3.8` (one week old, open multi-turn tool-loop 500) and the small
qwen3.5 sizes.

---

## 2. The mitigations, and how they become demo beats

Three of the four things you must do anyway are also good television.

### 2.1 Failover to Gemini — mandatory, and a great beat

```yaml
llm:
  models:
  - name: qwen-local
    visibility: internal
    provider: ollama
    params:
      model: qwen3.6:35b-a3b
      baseUrl: http://localhost:11434/v1
    health:
      eviction:
        consecutiveFailures: 1
        duration: 60s
  - name: gemini-backup
    visibility: internal
    provider: gemini
    params:
      model: gemini-2.5-pro
      apiKey: "$GEMINI_API_KEY"
    health:
      eviction:
        consecutiveFailures: 1
        duration: 60s

  virtualModels:
  - name: director
    routing:
      failover:
        targets:
        - model: qwen-local
          priority: 0
        - model: gemini-backup
          priority: 1
```

The Director points at model name `director` and never knows which served it.
*"Local first. If the local model stumbles, the gateway moves me to cloud, and my
agent code doesn't change."* That is a legitimately great line, and you get to say it
whether or not it fires.

> ⚠️ **Two traps.**
> **(a)** `routing.failover` alone does *not* fail over. `health.eviction` on the
> concrete models is required — the docs say so explicitly.
> **(b)** The nastiest Qwen failures return **HTTP 200** with empty content and no
> `tool_calls` (#17276, #14958). A default eviction condition keyed on 5xx **will not
> fire** — the Director just stalls. Write a custom `unhealthyExpression` that also
> treats empty-content-with-no-tool-call as unhealthy, **and test it**.
> **(c)** `duration: 60s` means once Qwen trips you're on Gemini for a full minute.
> That's probably what you want on stage; just know there's no snap-back.

### 2.2 Keep the Director's tool surface ≤5 — via the gateway

This is the constraint that most shapes the design, and the fix is the best idea in
this revision.

Full tool surface is ~11: `capture`, `transform_image`, `submit_video`, `poll_video`,
`announce`, `publish_video`, `create_branch`, `create_or_update_file`,
`create_pull_request`, plus two A2A agents. That's well past the 5-tool threshold
where the model starts emitting XML into `content`.

**Don't trim the system — trim what the gateway shows.** `mcpAuthorization` filters
unauthorized tools out of `tools/list`, so scope the Director's JWT per phase:

| Phase | Tools visible | Count |
| --- | --- | --- |
| Shoot | `capture`, `list_images`, `transform_image` | 3 |
| Direct | `submit_video`, `poll_video`, `announce` | 3 |
| Publish | `publish_video`, `create_branch`, `create_or_update_file`, `create_pull_request` | 4 |

The model never sees more than four tools at once, and you've turned a model
limitation into **genuine least-privilege, enforced at the gateway**. On stage:
*"the Director cannot open a pull request while it's still taking the photo — not
because I told it not to, because the gateway won't show it that tool."*

### 2.3 The other three mitigations (no beat, just do them)

- **System prompt under ~1600 tokens.** Above that, tool calls get silently dropped.
- **Compact tool history aggressively** between Director steps. Long history is the
  documented trigger.
- **Add a ~30-line XML fallback parser** in the Director: if `content` contains
  `<function=`, parse it as a tool call. Both Goose and ZeroClaw shipped exactly this.
  Highest-value insurance available, and independent of the gateway.
- **Pin the Ollama version** and rehearse on that exact build — #14745 was a
  0.17.5→0.17.7 regression. Latest ≠ safest.
- **Never send `tool_choice`.** Ollama's `/v1` doesn't support it, and it appeared in
  the silent-drop repro.

> Note: agentgateway's "native" Ollama provider talks to
> `http://localhost:11434/v1` — Ollama's **OpenAI-compat layer**. So every
> compat-layer caveat above applies on the gateway path.

---

## 3. Architecture

```
                      ┌──────────────────────────────────────────┐
   you type the   ───▶│   agentgateway  (single binary, laptop)   │
   prompt live       │                                          │
                     │  /mcp   virtual MCP · per-phase tool RBAC │
   Director ────①───▶│  /a2a   card rewrite · method logging     │
      │              │  /v1    qwen→gemini failover · cost       │
      ├─────②───────▶│                                          │
      ③              └──┬─────┬──────┬───────┬────────┬──────────┘
      │                 │     │      │       │        │
      ▼                 ▼     ▼      ▼       ▼        ▼
  Scout · DP        camera vision  stage  publish  GitHub MCP
   (A2A)            -mcp   -mcp    -mcp   -mcp     (remote, 91 tools)
                    stdio  HTTP    stdio  HTTP     PAT held by gateway
                            │
                            └──④──▶ back through gateway to Gemini
                                     :generateContent  /  Veo
```

① agent→MCP ② agent→LLM ③ agent→agent ④ **the double hop** — `vision-mcp` has no
API key either; its own Gemini egress is governed too.

**The thesis, unchanged and now stronger:** `GEMINI_API_KEY` and the GitHub PAT exist
in exactly one process. Not the agents. Not the MCP servers.
`env | grep -iE 'gemini|github'` on the agent host returns nothing, and it still
takes the photo, transforms it, makes the film, and opens the PR.

And with Qwen: **the Director's reasoning never leaves the laptop.** For a Japanese
audience that's a sovereignty story as much as a cost one.

### The crew — still three

| Agent | Job |
| --- | --- |
| **Director** | Qwen3.6 local. Owns the room, orchestrates, does the talking. |
| **Location Scout** | Your live prompt → a *specific* Japanese scene. "Make my audience do a Japanese dance" → which dance, which festival, what lighting, what clothing. Bon Odori at a summer matsuri reads very differently from a Nihon-buyō stage. |
| **Cinematographer** | The Veo shot: motion, lens, light, **and the music.** |

Three agents, two A2A hops. Do not add a fourth — the tool budget can't afford it.

---

## 4. The run of show

One photo. One transform. One video. No monitoring.

1. **Consent + capture.** Director announces what it's about to do, then `capture`.
2. **You type the prompt live** — *"make my audience do a Japanese dance."*
3. **Director → Scout (A2A).** Scout returns a vivid, specific scene prompt.
4. **`transform_image`.** ~10s. **The still lands in the MCP playground, rendered
   inline in agentgateway's own UI.** This is the first payoff and it's fast.
5. **Director → DP (A2A).** DP writes the motion + audio prompt. Music is
   non-negotiable — imagine's existing rule ("Veo generates native audio, never
   silently drop a music request") already covers this; keep it verbatim.
6. **`submit_video`** → returns a job id immediately. **You keep talking.** This is
   where you show the analytics dashboard, the tool-list filtering, the A2A log tail.
7. **~90s later**, `poll_video` completes and the Director calls
   `announce(ja, en)` — the room hears, in Japanese and then English, that the film
   is ready. You didn't check anything.
8. **Play it.** Music and all.
9. **`publish_video`** uploads the MP4. Then GitHub MCP: `create_branch` →
   `create_or_update_file` → `create_pull_request`.
10. **You merge on stage.** QR is already on the slide. It resolves.

### Why keep the still-image step when you only want one video

Because it buys you a visual payoff at ~10 seconds instead of ~100, and it's the beat
that puts the image inside agentgateway's UI. If you're cutting for time, going
straight from photo to Veo image-to-video works — you just lose the early payoff and
the inline-image beat. I'd keep it.

---

## 5. The voice announcement

New tiny MCP server, `stage-mcp`, one tool:

```
announce(ja: str, en: str) -> ok
```

**Use macOS `say`.** It's built in, works fully offline, and has a Japanese voice:

```bash
say -v Kyoko "映画ができました"
say -v Samantha "Your film is ready."
```

Verify what's installed on your machine before relying on it:

```bash
say -v '?' | grep -i ja_JP
```

If Kyoko isn't there, add it in System Settings → Accessibility → Spoken Content →
System Voice → Manage Voices. **Do this on the demo machine, not the day of.**

Cloud TTS would sound better and adds a network dependency to the one moment you
specifically said you don't want to babysit. `say` is the right call.

This is also a genuinely good demonstration of asynchronous agent work: the Director
is polling a long-running job in the background while holding a conversation, and it
interrupts *itself* to tell you when the job is done. Narrate that.

> `announce` is an MCP tool, so the call is metered and logged by the gateway like
> everything else — the voice you hear is a governed tool invocation.

---

## 6. Publishing — the PR flow

### The QR code

**Do not point it at `raw.githubusercontent.com`.** Measured: raw serves MP4 as
`content-type: application/octet-stream` with `x-content-type-options: nosniff` and
a `sandbox` CSP. A phone scanning that gets a download prompt, not a player.

**Point it at a GitHub Pages HTML wrapper** — Pages serves `.mp4` as `video/mp4`
with no nosniff, so it plays inline, and you control the presentation.

```
https://linsun.github.io/imagine/gallery/
```

That URL is **fully predictable before the file exists**, so generate the QR now and
put it on a slide. Have the Pages page already deployed and reading from a known
asset path, so the merge only adds an entry rather than triggering a first build.

### Timing and rulesets — check these this week

- **Pages publish latency after merge is ~20–60s and is not documented.** Budget
  1–2 minutes of stage time, or pre-deploy the wrapper so the merge just flips a link.
- **Don't let anyone scan the QR before the merge.** Raw/CDN negative caching is
  unpredictable; a pre-merge scan may poison it.
- **Check `main` for rulesets or required approvals.** You cannot approve your own
  PR. If required approvals ≥ 1, a self-merge is impossible without a second account.
- **`mergeable` is computed asynchronously** — poll until it's non-null before
  merging, or the merge can 405.
- **Use a different PAT for rehearsal than for stage.** GitHub's PR-create endpoint
  triggers notifications and has a secondary rate limit; dozens of back-to-back
  rehearsal PRs on one token can trip it.
- Fine-grained PAT needs **Contents: read/write** and **Pull requests: read/write**.

### The tool-filtering beat, on a real server

The GitHub MCP server exposes **91 tools**. You need three. Two independent filters,
which makes an even better visual than one:

1. `requestHeaderModifier` sending `X-MCP-Toolsets: repos,pull_requests` upstream →
   91 shrinks to ~30, server-side. **Guaranteed to shrink the listing.**
2. `mcpAuthorization` CEL rules → ~30 shrinks to 3, at the gateway.

```yaml
targets:
- name: github
  mcp:
    host: https://api.githubcopilot.com/mcp/
  policies:
    backendAuth:
      key:
        value: $GITHUB_PAT
        location:
          header:
            name: authorization
            prefix: "Bearer "
    requestHeaderModifier:
      set:
      - name: X-MCP-Toolsets
        value: "repos,pull_requests"
```

**91 → 30 → 3, on a real third-party authenticated server, with the credential held
by the gateway.** That's the best security beat in the talk. No Copilot subscription
is required for repo and PR tools.

> ⚠️ One source verified that `mcpAuthorization` filters unauthorized tools out of
> `tools/list`; another couldn't confirm it from the docs. The `X-MCP-Toolsets` path
> definitely shrinks the listing, so lead with that and treat authz-based hiding as
> the bonus. **Test both.**

---

## 7. Cost dashboard: make local vs cloud visible

A local Ollama model has no price, so agentgateway's cost catalog records it as
**`Missing`** — a hole in the chart, not a zero.

Add an explicit **zero-rate override** for `qwen3.6:35b-a3b` so it resolves as
`Exact`:

```yaml
config:
  modelCatalog:
  - file: ./costs/catalog.json      # agctl costs import --source models.dev
  - file: ./costs/overrides.json    # zero-rate entry for the local model
```

Then `/ui/llm/analytics` grouped by model shows the Director's tokens at **$0.00** next
to Gemini's real spend. Token *counts* are real for Ollama either way —
agentgateway parses actual usage, it doesn't estimate.

Combine with virtual keys per agent (Beat C in v1) and you get per-agent, per-model
FinOps: what each crew member cost, and which of them cost nothing.

---

## 8. Risk register (v2)

| | Risk | Mitigation |
| --- | --- | --- |
| 🔴 | **Qwen drops/mangles a tool call mid-chain** | Gemini failover with a *custom* `unhealthyExpression`; ≤5 tools per phase; short prompt; XML fallback parser; pinned Ollama |
| 🔴 | **MP4 can't go through GitHub MCP** | `publish-mcp` uploads from disk; GitHub MCP only does the text PR |
| 🔴 | `mcp.failureMode` defaults to **fail-closed** | `failureMode: failOpen`. One line. Do not skip it. |
| 🔴 | stdio targets dead in the official container | Native binary only |
| 🔴 | `npx`/`uvx` targets fetch at startup | Local venv, pinned versions, pre-warmed caches, rehearse offline |
| 🟠 | Tool-name prefixing breaks bare names in prompts | Write the prompt against prefixed names (`camera_capture`) |
| 🟠 | Pages publish latency after merge | Pre-deploy the wrapper; merge only adds an entry |
| 🟠 | Branch protection blocks self-merge | Check rulesets **this week** |
| 🟠 | UI rewrites your config file | Always `-f ./demo-config.yaml`; scripted clean reset |
| 🟠 | MCP capability stripping below spec rev `2026-07-28` | Don't rely on sampling/elicitation |
| 🟡 | Admin UI port 4000 vs 15000 | Run it once, note the real URL |
| 🟡 | Empty dashboards | Pre-seed the analytics DB |
| 🟡 | Japanese voice not installed | `say -v '?' \| grep ja_JP` on the demo machine now |

---

## 9. Three weeks

**Week 1 — kill the unknowns, in this order.** No features.
1. `qwen3.6:35b-a3b` in Ollama, driven **through agentgateway**, completing a
   5-step tool chain **20 times in a row**. If it can't, the Director is Gemini and
   Qwen becomes a "and it runs local too" aside. **Decide this in week 1, not week 3.**
2. Failover actually firing — including on a 200-with-no-tool-call.
3. `vision-mcp` calling Gemini `:generateContent` through the gateway with no local key.
4. `publish_video` + the three GitHub MCP calls, end to end, PR merged, Pages live.

**Week 2 — the show.**
Scout + DP over A2A. Per-phase JWT scoping. `stage-mcp` + Japanese voice.
`submit_video`/`poll_video`. Virtual keys + cost overrides. QR on the slide.

**Week 3 — rehearse. No new features after day one.**
- Full run **with wifi off** except model and GitHub calls.
- Failure drills: Qwen mangles a call, Veo times out, PR blocked, Pages slow.
- Pre-seed the analytics DB.
- **Record a complete backup video of the whole demo working.** Rehearse cutting to it.

**Cut order if you're behind:** the still-image step (go photo→video direct), then
the DP agent (Director writes the shot), then the PR (show the video, point at a
pre-published URL). **Never cut:** `failOpen`, the Gemini failover, the offline
rehearsal, the backup recording.

---

## 10. Still open

1. **Does Qwen survive week 1?** Everything else is downstream of that answer.
2. **Bon Odori, or something else?** "Japanese dance" is a wide net — worth deciding
   with someone Japanese which reading is charming rather than clumsy in front of a
   Tokyo audience.
3. **Does the Director speak Japanese throughout**, or only the announcement?
   Qwen3.6 handles 200+ languages; the announcement alone is the safe version.
4. **Consent language** — still needed, in both languages, before the camera opens.
   APPI treats facial images as personal data, and this one ends up in a public git
   repo permanently. That's a stronger commitment than a 24h gallery: **a binary in
   git is effectively forever.** Consider a release asset (deletable) over a
   committed file.
5. **Which repo?** Publishing audience photos into `linsun/imagine` makes them part
   of that project's history. A separate throwaway repo may be the kinder choice.
