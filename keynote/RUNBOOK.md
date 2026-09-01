# Live run of show

Everything you type is in `you ›`. Nothing is automatic — the Director waits for
you at every step. You decide when the camera fires.

---

## T-60 min — bring it up, off-stage

```bash
cd ~/src/github.com/linsun/imagine     # repo root, on the agntcon-japan branch
./imagine preflight     # fix anything it flags NOW
./imagine up            # vision-mcp, scout, dp, then the gateway
./imagine verify        # all green before you walk anywhere
./imagine status        # what is running, and where
```

Then leave it running. **Do not `./imagine down` between rehearsal and the
talk** — bringing it up is the slowest part.

(`./imagine` at the repo root is a shim onto `keynote/imagine`, so the folder
name never has to appear on stage. `./imagine start`
does `up` and then drops you straight into the Director, which is the single
command for the day.)

Open two windows and size them for the back row:

| Window | What | Why |
| --- | --- | --- |
| **A — terminal** | `./imagine demo` | The agent. This is the star. |
| **B — browser** | the gateway UI (URL printed by `./imagine up`) | Tool list, LLM analytics, request log |
| **C — browser** | opens itself when you say *launch the camera* | The room, live, on the projector |

Have `logs/agentgateway.log` tailing in a third window if you want the A2A beat:

```bash
tail -f logs/agentgateway.log | grep --line-buffered a2a
```

## T-5 min — final checks

- `cast` is the companies actually in the room (the file has no extension).
- Camera physically pointed at the audience, not at you.
- **Camera choice.** The camera lives in the **browser** (getUserMedia). Say
  *launch the camera* and the preview opens in **Safari** (`PREVIEW_BROWSER` in
  .env) -- because **Chrome cannot drive Continuity Camera / the iPhone** (a
  known Chrome bug), but Safari can. Allow camera access, then pick from the
  **Camera** dropdown; the iPhone appears by name and the phone shows a
  "Connected" banner. The agent-driven *take the photo* + 3-2-1 countdown is
  unchanged. Built-in FaceTime camera works in any browser and is the safe
  fallback -- pick it from the same dropdown.


  **Getting the iPhone to show up:** it is not a "connect" prompt — macOS
  exposes the phone as a camera once Continuity Camera is on. iPhone + Mac on
  the same Apple ID, Wi-Fi and Bluetooth on both; iPhone: Settings > General >
  AirPlay & Handoff > **Continuity Camera** on; phone nearby, held still
  (landscape). Then it appears in the picker by name (e.g. "Lin's iPhone
  Camera"). If it is not listed yet, click **Rescan** — no restart.
- Consent line ready, in both languages.
- Backup recording open in a tab you can reach in one click.

---

## On stage

### 1. Show that there's no key

```bash
env | grep -iE 'gemini|openai|github'
```

Nothing. *"Every credential in this demo lives in one process — the gateway.
Not in my agents, not in my MCP servers."*

### 2. Start the agent

```bash
./imagine demo
```

It prints the tools it got **through the gateway**. Read the list out — that's
five servers federated behind one endpoint, and the prefixes (`camera_`,
`vision_`, `post_`) show which is which.

### 3. Open the viewfinder

```
you › open the camera
```

The preview is already live at `http://localhost:8888/` — **put it on the
projector.** The room seeing itself does half your warm-up for you. Frame it.
Nothing has been captured.

### 4. The photo — you pick the moment

```
you › take the photo, count down from 3
```

A big 3-2-1 renders on the preview so people look up. The frame you were
watching is the frame you get, **and it appears on screen immediately.** Then
the agent stops and waits.

### 4b. Or skip the camera entirely

```
you › use the photo at ~/Desktop/room.jpg
```

Same from here on. This is how to rehearse without a room.

### 5. The film — one command, start to finish

```
you › make people dance, japanese vibe
```

That is the last thing you type. It now runs the entire show without asking
you anything:

| | | |
| --- | --- | --- |
| a | **Scout** (A2A) | your words → a specific Kyoto scene |
| b | Nano Banana | the still |
| c | **DP** (A2A) | shot direction + the music |
| d | Veo | ~90s, with an elapsed counter so it doesn't look hung |
| e | ffmpeg | end credits from `cast.txt` |
| | | **the film plays, fullscreen, out loud** |
| f | GitHub | uploads the film |
| g | GitHub | opens the pull request |

**Those ~90 seconds are your window.** Talk over them:

- **Window B → LLM analytics.** Tokens and spend, one meter point.
- **Window B → tool list.** Five servers, one endpoint.
- **Window C → the A2A log.** `a2a.method=message/send`, and the agent card
  whose `url` the gateway rewrote so the Director *cannot* route around it.
- **The double hop.** vision-mcp holds no key either — its own Gemini calls
  come back out through the gateway.

Then the room hears the announcement, the film plays with its credits, and you
read out the PR URL.

### 6. Turn the camera off

```
you › you can stop the camera now
```

The green light goes out and the preview reads CAMERA OFF. Nothing else stops.
You told the room you were photographing them — stopping when you said you
would is a small thing that lands well, and it costs you one sentence.

`./imagine camera off` does the same without the agent, and `./imagine camera on` picks
it back up.

### 7. Merge

Merge from your phone. The QR on the slide already points at the right place.

---

## When something breaks

| Symptom | Say this, do this |
| --- | --- |
| Camera won't open | *"Let's use the one I took earlier."* → `you › use a fallback photo instead` |
| Need the green light off | `you › stop the camera` · or `./imagine camera off` |
| Preview black / wrong camera | `you › stop the camera and reopen it on camera 1` |
| Shot direction wrong | `you › redo the film with exactly this shot: <your text>` |
| Still looks wrong | `you › try that again, make it more <X>` — one retry, then move on |
| Veo is slow | Keep talking. It is generating; the announcement will come. |
| Veo fails | `you › skip the film, just publish the still` |
| Credits fail | `you › skip the credits and publish` — ffmpeg, not the agent |
| Film doesn't play | needs `ffplay` (comes with ffmpeg). It is still in `outputs/`. |
| PR fails | The film already played. *"I'll merge this after."* Move on. |
| Director confused | Rephrase once, imperatively. Twice = cut to the recording. |
| Anything on fire | Cut to `backup/demo-recording.mp4`. **Rehearse this.** |

**The rule:** never debug on stage. One retry, then take the next branch down.
The audience cannot tell a fallback from a plan unless you tell them.

---

## After

```bash
./imagine down
```

`outputs/` has everything. Delete the audience photos when you're done with
them — you told the room you would.

---

## Optional beat — the budget that says no

agentgateway 1.5.0 charges an LLM budget against a **virtual key**, not against
a provider credential. The agents hold `AGW_VIRTUAL_KEY`; the config holds only
its SHA-256. The real Gemini and OpenAI keys are still only in the gateway.

Standing configuration: **$10 a day, then Block.** That is the real control and
it should never fire on stage.

The demo beat is a second, deliberately tiny budget. In `gateway/config.yaml`,
uncomment the `tripwire` block under `llm.policies.apiKey`:

```yaml
        - name: tripwire
          limit:
            unit: Tokens
            amount: 200
          window:
            rolling: 24h
          onBudgetExceeded: Block
```

Save. **No restart** — everything under `llm.policies` hot-reloads. The next
call comes back:

```json
{"error":{"message":"Budget exceeded","type":"rate_limit_error","code":"budget_exceeded"}}
```

with HTTP `429`. Raise `amount` to something large, save again, and the very
next request goes through.

Two things to know before you try it live:

- **The budget is charged after the response.** The request that blows through
  it still succeeds; the *next* one is refused. Burn it once off-stage so the
  failure lands on the call you mean it to.
- **Comment the tripwire back out before the real run.** `./imagine preflight`
  warns you if you left it live.

---

## The publish-login beat

**Before the talk** (Keycloak must be up before the gateway):

```bash
docker run -d --name keycloak -p 8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -v ~/mcp-auth-demo/auth-server/keycloak-seed2:/opt/keycloak/data/import:ro \
  quay.io/keycloak/keycloak:26.4.1 start-dev --import-realm
./imagine up            # refuses to start if Keycloak is down
./imagine verify        # 3b confirms publish is gated
```

**On stage:**

1. Take the photo, describe the film. The pipeline runs to the credits.
2. The film plays and holds on the last frame (the cast roll).
3. The Director asks: **Publish this film to GitHub? [y/N]**. Answer `y`.
4. Your browser opens on Keycloak. Log in as **linsun**.
5. It publishes and prints the download link as the closing line:
   `You can download the film at https://github.com/linsun/imagine/releases/tag/agntcon-mcpcon-japan-2026`
   — read it out or put it on a slide. (Answer `N` to skip; the film is on
   screen either way.)

Say it plainly: *publishing is the one thing that acts in the real world, so it
takes a person — the agent can make the film, but it can't ship it as me.*

**If Keycloak dies at the venue:** `./imagine auth off`, then `./imagine up`.
Publishing goes back to the gateway-held token with no login. `preflight` warns
you if auth is on and Keycloak is down.

Token lifetime in the seed realm is 300s — fine, because login happens live at
publish time, not before. If you rehearse publish and then wait, just log in
again; the agent will re-prompt if the token has expired.
