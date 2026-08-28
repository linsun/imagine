# From web app to agent: a design for `imagine`

**Status:** proposal / design doc. No code changes yet.
**Goal:** turn `imagine` into a real agent you talk to, that still nails the live
KCD audience demo, and that can move into Kubernetes under kagent later without a
rewrite.
**Reference:** [kagent_vision](https://github.com/automateyournetwork/kagent_vision)
(Google ADK + MCP), which `imagine` was already inspired by.

---

## 1. The diagnosis: why today's `imagine` feels like an app, not an agent

The agent code in `backend/app/agent.py` is genuinely an agentic loop — Gemini
function calling, up to 5 tool rounds, the model picks the tool. That part is fine.
What makes it *feel* like a web app is everything around it:

| Symptom | Root cause |
| --- | --- |
| You can't use it without a browser | The camera is `getUserMedia()` in `frontend/index.html`. Capture is a **browser capability**, not a tool. |
| The agent can't do multi-step work | Every turn starts from scratch: `run(message, image_b64)` gets one image and no history. There is no session, so "now animate it" has no *it*. |
| The interesting verbs are buttons | `/api/transform` and `/api/animate` bypass the agent entirely. The agent is a fourth button labelled "Ask agent", not the front door. |
| The agent can't see its own work | `transform_image` returns base64 that the backend deliberately hides from the model. The agent is blind to whether the result is any good. |

So the fix is not "write an agent" — it's **move capture and display out of the
browser and into tools, give the agent a session, and make it the only front door.**
That is exactly the boundary kagent_vision got right, and it's the main thing worth
borrowing.

---

## 2. What's worth borrowing from kagent_vision

### 2.1 The camera is an MCP tool (the big one)

kagent_vision has `list_cameras`, `vision_start`, `vision_status`, `vision_capture`,
`vision_burst`, `vision_stop` — OpenCV against `avfoundation`/`v4l2`/`msmf`. Because
capture is a tool, the agent can run the whole pipeline from one sentence, from a
terminal, with no page open.

**Borrow it. This single change is what converts `imagine` from a UI into an agent.**

> *"Grab the room, make it Lima street art, then animate it."*
> → `vision_capture` → `transform_image` → `generate_video` — one utterance, four tools.

Today that sentence is impossible: the agent has no way to take a photo.

### 2.2 Working from files on disk, not base64 over the wire

kagent_vision tools return **file paths**; `veo_generate_video(image_path=...)` chains
directly off `banana_generate`'s output. `imagine` passes base64 blobs between backend
and MCP server.

To be fair, `imagine`'s base64 choice is *correct for its constraint* — it exists
specifically so no ReadWriteMany volume is needed across pods, which is called out in
the README. But it costs you: every hop re-encodes ~1–3 MB of image, and the design
note in `agent.py` ("the model only decides *what* to do") is a workaround for base64
being too big to show the model.

**Borrow the idea, keep your constraint:** introduce an **artifact handle** —
`img_a1b2c3` — with two implementations behind one interface. Local: a path under
`outputs/`. Cluster: the existing base64-over-MCP path plus backend storage. The agent
only ever passes handles, never bytes. That kills the base64 tax locally, keeps K8s
volume-free, and means the tool signatures don't change when you move to a cluster.

### 2.3 The system prompt should name the *pipeline*, not just the tools

kagent_vision's prompt spells out a numbered "Standard Photo Pipeline" and a separate
"ASL Conversation" flow. `imagine`'s prompt is a good single-turn router — it explains
each tool well — but it never says *photo → transform → animate* is a thing.

**Borrow it.** Naming the pipeline is what makes a chained request work reliably
instead of accidentally. Keep `imagine`'s prompt strengths, which are already better in
two places: the explicit "Veo generates native audio, never drop a music request" rule,
and telling the agent to ask for a photo when none is loaded. Those are good — keep
them verbatim.

### 2.4 `list_images` — the stage fallback

kagent_vision's `list_images(directory)` lets the agent work from an existing photo
instead of the webcam. That reads like a convenience; **for a live demo it's a safety
net.** Conference wifi, a locked-down laptop, a camera permission dialog on the wrong
display — any of these kills a webcam-only demo. Give the agent a folder of pre-shot
crowd photos and a `list_images` tool and you always have a fallback you can trigger by
saying "use the backup shot instead."

### 2.5 Consent as a prompt rule

kagent_vision's guidelines include *"Get consent before capturing people."* For a photo
of a live conference audience this is not boilerplate — it's the right behavior and it
plays well from stage. Have the agent say what it's about to capture before it captures.

### 2.6 (Optional) ASL as a second workflow

`asl_understand` over a burst of frames is a genuinely different pipeline, and having
two workflows is what proves the agent is *choosing* rather than following a script.
Out of scope for the KCD crowd demo, but it's the obvious "and it can also…" if you
ever want a second act.

---

## 3. What *not* to borrow

**One MCP server holding all ten tools.** kagent_vision ships k8s manifests, but the
camera tools cannot work in a pod — there is no `/dev/video0` in a container, which is
why the project needs an `MCP_LOCAL=1` subprocess escape hatch. Lumping hardware tools
and stateless API tools into one server means the server can only ever live in one
place. **Split by locality instead** (see §4). Your `vision-mcp` is already on the right
side of that line.

**The three-process launcher.** `run_local.py` (FastAPI) spawns the ADK `api_server`
subprocess *and* serves the UI on a third port. That's three things to explain and three
things to fail on stage. One process, one command.

**Blocking on Veo inline.** kagent_vision blocks; `imagine` blocks harder — a 960-second
SSE read timeout in `mcp_client.py`. Both mean the agent is frozen for 1–2 minutes. In a
room full of people that is an eternity. Make video a job (see §6).

---

## 4. Proposed architecture

Split the MCP servers by **where they must physically run**, not by topic:

```
                    ┌──────────────────────────────────┐
   you talk here →  │        Director agent            │
                    │  (session, orchestration, chat)  │
                    └───┬──────────┬──────────┬────────┘
                        │          │          │
        ┌───────────────┘          │          └────────────────┐
        ▼                          ▼                           ▼
┌────────────────┐        ┌─────────────────┐        ┌──────────────────┐
│  camera-mcp    │        │   vision-mcp    │        │  display-mcp     │
│  stdio, local  │        │  HTTP, anywhere │        │  stdio, local    │
│                │        │                 │        │                  │
│ list_cameras   │        │ transform_image │        │ show(handle)     │
│ capture        │        │ generate_image  │        │ show_video       │
│ burst          │        │ submit_video    │        │ clear            │
│ list_images    │        │ poll_video      │        └──────────────────┘
│ stop           │        │ list_styles     │
└────────────────┘        └─────────────────┘
  needs hardware            needs an API key         needs a screen
  never in a pod            already K8s-ready        never in a pod
```

`vision-mcp` is **the part you already built correctly** — stateless, streamable-HTTP,
volume-free, containerized. It doesn't change. Everything new goes in the two local
servers, which is also exactly the boundary that will matter when you move to kagent
(§7).

### Interfaces, in priority order

1. **Terminal REPL** — the primary interface. This is what makes it a real agent.
2. **A2A endpoint** — expose it from day one, even standalone. It costs almost nothing
   (ADK gives it; FastAPI is ~30 lines) and it's the *entire* on-ramp to kagent BYO.
3. **The projector page** — see §5. Not an app; a dumb viewer.

---

## 5. The display problem (don't skip this)

If there's no web UI, **where does the transformed crowd photo appear on the conference
projector?** This is the question that decides whether the demo works, and it's the one
thing neither repo solves cleanly.

Three options:

| Option | Verdict |
| --- | --- |
| `open`/`xdg-open` the file | Works, but you get an OS window manager on the big screen. Ugly, and you're alt-tabbing on stage. |
| Rebuild the UI | You're back where you started. |
| **A dumb full-screen viewer** | ✅ A single HTML page, no controls, that shows whatever the agent last handed it. |

Take the third. **The browser doesn't disappear — it gets demoted from *the app* to
*the projector*.** `display-mcp` exposes `show(handle)`, the page long-polls or holds a
websocket, and the agent decides what's on screen. You keep the visual payoff and you
still get to say "there is no UI — I'm just talking to it," which is true, because the
page has no buttons.

Nice side effect: you can reuse a stripped-down `frontend/index.html`, and `show()`
becomes a tool the agent can be *creative* with — before/after splits, a slow reveal,
putting the video up the moment it's ready.

---

## 6. Long-running video: make it a job

Veo takes 1–2 minutes. Blocking the agent for that long is the demo's biggest risk.

Split `generate_video` into two tools:

- `submit_video(image_handle, instruction, motion_preset) -> job_id` — returns instantly
- `poll_video(job_id) -> {status, progress, video_handle?}`

The agent stays live and can talk to the audience while Veo works, take *another* photo,
or run a second transform in parallel. In the prompt: *"after submitting a video, tell
the user it takes about 90 seconds, then keep the conversation going and check
`poll_video` before answering their next request."*

This also removes the 960-second SSE timeout hack in `mcp_client.py`, which is a
latent source of stage-time failure.

---

## 7. Agents, plural: what actually earns a second agent

You asked about "the agent(s)". A multi-agent diagram makes a better slide, but every
extra hop is latency and a new failure mode in front of a live audience. So: **one agent
by default, plus two that genuinely earn their keep.**

### Director — the one you talk to
Owns the session and the conversation, calls all the tools, decides the pipeline. This
is `backend/app/agent.py`'s loop, plus session state (`current_image`, `history`,
`pending_jobs`), which is the piece it's missing today.

### Stylist — turns intent into a model-ready prompt
*"Make it Lima"* → a paragraph of Nano Banana prompt engineering with the right visual
vocabulary. This is a real LLM task and it is **not** the same task as orchestration:
different prompt, possibly a different (bigger) model, tuned independently, and it's
where the demo's quality actually comes from.

Your `presets.py` is this function *frozen into a dict* — the prompts in there are long
and vivid precisely because prompt-crafting matters. A Stylist agent is the live version
of that dict. Keep the presets as a fast path for known styles and a `list_styles`
answer; let the Stylist handle anything unlisted. That's a strictly better answer than
either repo has today.

### Critic — should this go on the big screen?
After a transform, look at the *result* (a vision call — the model actually sees the
image) and answer two questions: did it do what was asked, and is it OK to project in
front of several hundred people? If not, hand a corrected instruction back to the
Stylist and retry **once**.

This is the piece neither project has, and it's the most compelling thing on stage:
the agent notices its own bad output and fixes it, live. It's also a real guardrail —
you're generating images of a real audience on a real screen. Hard-cap it at one retry
so it can't loop while people watch.

Note this fixes the blindness in §1: today the base64 result is deliberately kept away
from the model. The Critic is where it comes back.

### Standalone vs. kagent — same design, two bindings

| | Standalone (now) | kagent (later) |
| --- | --- | --- |
| Director | ADK `Agent` / your own loop | `Agent`, `type: Declarative` |
| Stylist | `sub_agent` or a local function | `Agent` + A2A |
| Critic | `sub_agent` or a local function | `Agent` + A2A |
| vision-mcp | streamable HTTP, localhost | `MCPServer` / `RemoteMCPServer` |
| camera/display-mcp | stdio, on the laptop | stays on the laptop (see below) |

The point of picking these three: **the seams are in the same places in both columns.**
Nothing gets re-cut when you move.

---

## 8. Moving to kagent later — including the part that's genuinely hard

The easy parts:

- `vision-mcp` already speaks streamable-HTTP → it's a `RemoteMCPServer` (or an
  `MCPServer` with a `deployment` block) almost verbatim.
- Director becomes an `Agent` with `type: Declarative`, `modelConfig`, `systemMessage`,
  and a `tools:` list pointing at the MCP server. Your system prompt drops in as
  `systemMessage` unchanged — which is a nice thing to show on a slide: *the agent is
  YAML now.*
- Stylist and Critic become their own `Agent` resources; Director reaches them over A2A.
- If you'd rather keep the ADK code, kagent's **BYO** path takes an existing ADK /
  LangGraph / CrewAI agent deployed as an A2A server. That's why §4 says expose A2A from
  day one — it makes this step nearly free.

**The hard part, stated plainly:** an agent in a pod cannot see your laptop's webcam or
your projector. `camera-mcp` and `display-mcp` cannot follow the others into the
cluster. kagent_vision papers over this with `MCP_LOCAL=1`; don't repeat that.

The honest resolution is that **capture and display are edge concerns and stay at the
edge.** In the cluster future, a small local CLI snaps the photo and submits it, the
in-cluster Director's first tool is `fetch_submitted_photo(handle)`, and results come
back to the viewer page. Same three agents, same vision-mcp, same prompts — only the
first and last hop change. Worth designing for now, because it decides whether
`camera-mcp` returns a *handle* (portable) or a *local path* (not).

---

## 9. Suggested phasing

**Phase 1 — make it an agent.** `camera-mcp` (OpenCV, borrowed from kagent_vision's
`camera.py`), session state in the Director, terminal REPL, pipeline named in the
system prompt. Point it at the existing `vision-mcp` unchanged. *Milestone: run the
whole demo from a terminal with no browser open.*

**Phase 2 — make it survivable on stage.** `display-mcp` + the dumb viewer page, the
`submit_video`/`poll_video` job split, `list_images` fallback, consent rule.
*Milestone: nothing on screen but the output, and no 90-second dead air.*

**Phase 3 — make it interesting.** Stylist and Critic, one-retry self-correction.
*Milestone: the agent rejects its own bad output live.*

**Phase 4 — kagent.** A2A on the Director, three `Agent` CRDs, `RemoteMCPServer` for
vision-mcp, edge capture/display. *Milestone: `kubectl get agents`.*

Phases 1–2 alone get you the "real agent" demo. 3 is what makes it memorable.

---

## 10. Open questions

1. **Does the browser survive as a viewer, or do you want zero browser?** §5 assumes
   viewer. If you want zero, the fallback is an OS image previewer and the demo looks
   worse.
2. **Director model** — keep `gemini-2.5-flash` for speed (right call for a stage), or
   go bigger for better chaining? The Stylist is where a bigger model pays off, not the
   Director.
3. **How much does the Critic gate?** Advisory (mentions concerns, still shows) vs.
   blocking (won't project until it passes). Blocking is the better story and the bigger
   risk.
4. **Does the KCD demo need multi-user?** A single presenter laptop is a much simpler
   design than an audience-submits-photos flow.
5. **Keep the existing web UI in the repo?** Suggest yes, unchanged, as the
   "before" half of a before/after in the talk itself.
