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
name never has to appear on stage. `make` inside `keynote/` still works and
runs exactly the same scripts, if that is in your fingers. `./imagine start`
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
