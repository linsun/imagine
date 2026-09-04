"""The Director: the agent you talk to. No browser anywhere.

Everything it does crosses agentgateway:
  reasoning        -> /v1   (Gemini, failing over to OpenAI, transparently)
  tools            -> /mcp  (camera, stage, post, publish, vision -- federated)
  the other agents -> /a2a  (Scout, DP)

THREE COMMANDS, and only three:

    you › open the camera
    you › take the photo, count down from 3          -> shows the photo
    you › make people dance, japanese vibe           -> runs the whole film

The third one runs to completion without asking anything: Scout, still, DP,
Veo, credits, plays it out loud, opens the pull request. The Scout and DP are
still real A2A agents through the gateway -- you just never have to talk to
them. That is the point: you direct, the crew works.

  make demo
"""

import asyncio
import json
import os
import re
import sys
import threading
import time

import httpx
from openai import OpenAI

from agent import auth, store, tools, tracing

LLM = os.environ.get("AGW_LLM", "http://localhost:3000")
A2A = {
    "scout": os.environ.get("SCOUT_A2A", "http://localhost:3002"),
    "dp": os.environ.get("DP_A2A", "http://localhost:3003"),
}
MODEL = os.environ.get("DIRECTOR_MODEL", "director")

SYSTEM = """You are the Director of a film being made live, on stage, in front
of the audience who are in it.

## HOW YOU BEHAVE

NEVER ask the user a question. Not "shall I continue?", not "would you like me
to?", not "should I proceed?". They are on stage in front of hundreds of
people; every question you ask is dead air they have to fill. If something is
ambiguous, make the obvious choice and carry on. Say what you did in ONE short
sentence, then stop.

Call each tool AT MOST ONCE per request. If a tool fails, say so and stop --
do not retry it with different wording.

## THE THREE THINGS THE USER WILL SAY

**1. Something about opening or finding the camera.**
   camera_preview_url(). The preview opens in their browser automatically --
   do NOT call anything else, and do NOT capture. Say the preview is up, in one
   short sentence, and include the URL in case they need it.

**2a. A file path, or "use this photo".**
   camera_load_image(path=<what they said>). Paths like ~/Desktop/room.jpg work.
   If they name a folder, camera_list_images(directory=...) first and show the
   options. The photo is shown automatically. Then STOP.

**2. Something about taking the photo.**
   camera_capture(countdown=3), or the number they asked for. The photo is
   shown on screen automatically -- do NOT call stage_show yourself. Say what
   you got in one sentence. Then STOP. Do not start the film.
   If they don't like it and want ANOTHER photo, just call camera_capture
   again -- the newest photo is the one that gets used. Do not restart anything.
   If the camera fails: camera_list_images() then camera_load_image().
   If the user asks to stop/turn off the camera: camera_release(). To pick it
   back up: camera_resume(). Neither ends anything -- do not treat them as the
   end of the show.

**3. Anything describing what the film should be.**
   e.g. "make people dance, japanese vibe". This is the whole show. Run ALL of
   these, in order, WITHOUT asking anything and WITHOUT stopping in between:

     a. ask_scout(<the user's exact words>)  -> richer wording for THEIR idea
     b. vision_transform_image(image_handle=<the photo>,
                               instruction=<the scout's paragraph>)
     c. ask_dp(<the scout's paragraph>)      -> shot + music direction
     d. vision_generate_video(image_handle=<the still from step b>,
                              instruction=<the DP's paragraph>)
        This takes about 90 seconds. Just wait for it.
     e. post_add_credits(video_handle=<the film>)
        Pass NOTHING else. The heading, the cast and the closing QR are all
        fixed in config -- do not invent a title or retype any names.

   That is your last tool call. After it, the credited film plays out loud and
   you are asked whether to publish it -- do NOT call stage_show, and do NOT
   call any publish tool yourself. Publishing needs a signed-in person and is
   handled for you.

   Then say ONE short, warm sentence -- e.g. that the audience got their moment
   as the cast. Do NOT say the film is "now playing" (it has already played),
   do NOT claim it is published, and do NOT invent a download link. If it was
   published, the app prints the real link itself.

## RULES

NEVER add a place, culture, era or theme the user did not ask for. If they say
"add a japanese vibe", that is the theme; if they say nothing about a place,
do not invent one. The room and the people are fixed and must stay themselves.

Handles (img_x, vid_x) are opaque. Pass them along; never invent one.
Always use the NEWEST handle: the still from (b), then the credited film from (e).
One photo, one still, one film. Never generate a second video.
The audience are the cast. Say so once, at the end -- it is the point."""

# Local tools that are really A2A calls to the crew.
LOCAL_TOOLS = [
    {"type": "function", "function": {
        "name": "ask_scout",
        "description": "Ask the Location Scout to turn the user's idea into a specific, vivid scene.",
        "parameters": {"type": "object", "required": ["instruction"],
                       "properties": {"instruction": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "ask_dp",
        "description": "Ask the Director of Photography for shot and music direction.",
        "parameters": {"type": "object", "required": ["scene"],
                       "properties": {"scene": {"type": "string"}}}}},
]

# The VIRTUAL key. Not a provider credential -- it identifies this agent to
# agentgateway, which is what the $10/day budget is charged against. The real
# Gemini and OpenAI keys still live only in the gateway. Falls back to the old
# placeholder so the demo runs unchanged with no apiKey policy configured.
VIRTUAL_KEY = os.environ.get("AGW_VIRTUAL_KEY", "agentgateway")

SLOW = {"vision_generate_video": 90, "post_add_credits": 15,
        "vision_transform_image": 12, "publish_publish_video": 20}

# What the ROOM sees. Deliberately says what is happening without exposing the
# pipeline -- the audience should be curious, not reading your architecture.
# Plain words only: no jargon, no tool names. If a label needs explaining, it
# is the wrong label. Edit freely -- this dict is the entire stage vocabulary.
# Set DIRECTOR_VERBOSE=1 to get the full prompts back for development.
VERBOSE = os.environ.get("DIRECTOR_VERBOSE", "") not in ("", "0", "false")

STEP = {
    "camera_preview_url":    ("\U0001F4F7", "finding the camera"),
    "camera_capture":        ("\U0001F4F8", "taking the photo"),
    "camera_load_image":     ("\U0001F5BC\uFE0F", "loading the photo"),
    "camera_list_images":    ("\U0001F5C2\uFE0F", "looking for photos"),
    "camera_release":        ("\U0001F4F4", "putting the camera down"),
    "camera_resume":         ("\U0001F4F7", "picking the camera up"),
    "ask_scout":             ("\u2728", "imagining the scene"),
    "vision_transform_image":("\U0001F3A8", "painting your room"),
    "ask_dp":                ("\U0001F3AC", "choosing the camera move and music"),
    "vision_generate_video": ("\u23F3", "generating the film"),
    "post_add_credits":      ("\U0001F39E\uFE0F", "rolling the credits"),
    "stage_show":            ("\u25B6\uFE0F", "on screen"),
    "stage_open_url":        ("\U0001F5A5\uFE0F", "putting it on screen"),
    "stage_announce":        ("\U0001F50A", ""),
    "publish_publish_video": ("\u2601\uFE0F", "publishing"),
    "publish_open_pr":       ("\U0001F500", "opening the pull request"),
    "publish_gallery_url":   ("\U0001F517", "finding the link"),
    "check_auth":            ("\U0001F511", "checking access"),
}

# Spoken when the film lands. Empty = say nothing; the film starting to
# play IS the announcement, and a chirp before it reads as odd.
READY_PHRASE = os.environ.get("READY_PHRASE", "")

# ---------------------------------------------------------------------------
# The preservation clause. Wrapped around whatever is asked for, IN CODE, so a
# model cannot drop it -- which is exactly what went wrong when the crew was
# free to write the whole instruction: it replaced the room with a Kyoto shrine
# and the audience stopped being the audience.
#
# The language is lifted from the presets that worked at KCD ("keep the main
# subject recognizable", "preserve the main subject while surrounding it").
#
# NOTE: deliberately contains NO location, culture or theme. All of that comes
# from what you type, so the same agent works at the next conference unchanged.
# ---------------------------------------------------------------------------
PRESERVE_IMAGE = (
    "Edit this photograph. It must remain recognisably the SAME photo of the "
    "SAME room and the SAME people: same faces, same number of people, same "
    "clothing, same positions, same seating and layout, same camera angle. "
    "Do NOT relocate them, do NOT replace the background with a different "
    "place, do NOT invent new people or remove anyone. Keep every face "
    "recognisable and dignified.\n\n"
    "Working within that, apply this: {idea}\n\n"
    "The person directing asked for exactly this, and every part of it must be "
    "honoured, including any greeting, gesture or action: \"{asked}\"\n\n"
    "No text, no captions, no watermarks."
)

PRESERVE_VIDEO = (
    "Animate this photograph. Keep the SAME people, same faces, same clothing, "
    "same room and layout. Do not relocate them or replace the setting. "
    "Motion should be natural and gentle -- the people move, the room does "
    "not.\n\n"
    "{idea}\n\n"
    # The user's request is deliberately NOT quoted back here. Veo treats quoted
    # text as a line to be SPOKEN -- which is why the film came out with someone
    # saying "make them dance and add some japanese vibe" out loud.
    "Audio is music and ambience only: no dialogue, no narration, no voice-over, "
    "no singing, and nobody speaks or reads anything aloud. Crowd laughter and "
    "clapping are welcome, but no intelligible words.\n\n"
    "No text, no captions, no subtitles, no watermarks."
)


def _echo(name: str, args: dict) -> None:
    """One quiet line per step. The prompts are for you, not for the room."""
    icon, label = STEP.get(name, ("\u2022", name))
    if label:
        print(f"  {icon}  \033[2m{label}\033[0m", flush=True)
    if not VERBOSE:
        return
    for k in ("instruction", "prompt", "scene", "title", "subtitle"):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            print(f"      \033[2m{k}:\033[0m \033[3m{v if len(v) <= 700 else v[:700] + ' …'}\033[0m",
                  flush=True)


class _Ticker:
    """A turning hourglass, so a 90-second wait reads as anticipation rather
    than a hang. No handles, no tool names -- the room is watching this."""

    GLASS = ["\u23F3", "\u231B"]

    def __init__(self, name: str, expect: int) -> None:
        icon, label = STEP.get(name, ("\u23F3", name))
        self.label, self.expect = label or name, expect
        self.stop = threading.Event()

    def __enter__(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        t0 = time.time()
        i = 0
        while not self.stop.wait(0.8):
            el = int(time.time() - t0)
            g = self.GLASS[i % 2]
            i += 1
            sys.stdout.write(f"\r  {g}  \033[2m{self.label} … {el // 60}:{el % 60:02d}\033[0m ")
            sys.stdout.flush()

    def __exit__(self, *_):
        self.stop.set()
        sys.stdout.write("\r" + " " * 72 + "\r")
        sys.stdout.flush()


class Director:
    def __init__(self) -> None:
        # The gateway holds the provider credential. What we send is the
        # VIRTUAL key -- an identity the gateway budgets, not something that
        # can talk to Gemini. If this ever has to be a real provider key, the
        # demo's whole thesis has broken.
        self.client = OpenAI(base_url=f"{LLM}/v1", api_key=VIRTUAL_KEY, timeout=300)
        self.messages = [{"role": "system", "content": SYSTEM}]
        self.tools: list[dict] = []
        # Kept verbatim so the Scout cannot quietly drop part of the request --
        # "greet my audience" was getting lost between enrichment and the model.
        self.user_idea = ""
        # The most recent photo handle (capture or uploaded file).
        # The transform ALWAYS uses this, so retaking a photo just
        # works -- the newest one wins, no matter what the model passes.
        self.last_photo = ""
        # The last credited film, so `publish` can retry without re-rendering.
        self._last_credited: dict = {}

    def _ask(self, who: str, text: str) -> str:
        r = httpx.post(
            f"{A2A[who]}/",
            json={"jsonrpc": "2.0", "id": 1, "method": "message/send",
                  "params": {"message": {"role": "user", "kind": "message",
                                         "messageId": "req",
                                         "parts": [{"kind": "text", "text": text}]}}},
            headers=tracing.headers(),
            timeout=180,
        )
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"{who}: {body['error'].get('message')}")
        return "\n".join(p.get("text", "") for p in body.get("result", {}).get("parts", []))

    async def _run_tool(self, name: str, args: dict) -> dict:
        with tracing.span(f"tool:{name}", **{"tool.name": name}):
            return await self._run_tool_inner(name, args)

    async def _run_tool_inner(self, name: str, args: dict) -> dict:
        # Wrap the preservation clause around the idea before it reaches the
        # model. Not left to the prompt: this is the difference between "your
        # audience, dancing" and "some other people, somewhere else".
        if name == "vision_transform_image":
            args = dict(args)
            # Always paint the NEWEST photo, whatever handle the model chose --
            # so "take another photo" then "make them dance" uses the retake.
            if self.last_photo:
                args["image_handle"] = self.last_photo
            if args.get("instruction"):
                args["instruction"] = PRESERVE_IMAGE.format(
                    idea=args["instruction"].strip(), asked=self.user_idea)
        elif name == "vision_generate_video" and args.get("instruction"):
            args = dict(args)
            args["instruction"] = PRESERVE_VIDEO.format(
                idea=args["instruction"].strip())

        if name == "ask_scout":
            return {"scene": self._ask("scout", args["instruction"])}
        if name == "ask_dp":
            return {"direction": self._ask("dp", args["scene"])}
        if name in SLOW:
            with _Ticker(name, SLOW[name]):
                return await tools.call(name, args)
        return await tools.call(name, args)

    async def _offer_publish(self, credited: dict) -> None:
        """After the film plays, ask whether to publish it -- which needs a
        signed-in person.

        Publishing is the one action that reaches the real world, so we ask,
        and the gateway only allows it for an authenticated user. We sign in
        FIRST and reconnect, then call publish on an authenticated session --
        so publish is never attempted without a token (which the gateway
        refuses, and which surfaces as an ugly cancellation).
        """
        handle = credited.get("video_handle")
        if not handle:
            return
        try:
            ans = (await asyncio.to_thread(
                input,
                "\n  \033[1mPublish this film to GitHub?\033[0m  "
                "you'll sign in with Keycloak  \033[2m[y/N]\033[0m \u203a "
            )).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(flush=True)
            return
        if ans not in ("y", "yes", "publish"):
            print("  \033[2mnot published — the film is still on screen\033[0m",
                  flush=True)
            return

        # Sign in only if there is no live token already.
        if not auth.token():
            print("  \U0001F511  \033[2mopening your browser to sign in\033[0m",
                  flush=True)
            try:
                res = await asyncio.to_thread(auth.login)
            except Exception as exc:  # noqa: BLE001
                print(f"  \033[33m! sign-in failed: {exc}\033[0m", flush=True)
                return
            if not res.get("ok"):
                print(f"  \033[33m! sign-in did not complete: "
                      f"{res.get('error')}\033[0m", flush=True)
                return
            print(f"  \033[2m✓ signed in as {res['user']}\033[0m", flush=True)
            # mcpAuthentication binds identity at connect, so the session
            # opened without a token must be reopened for the token to count.
            await tools.reset()

        try:
            with _Ticker("publish_publish_video",
                         SLOW.get("publish_publish_video", 20)):
                await tools.call("publish_publish_video", {"video_handle": handle})
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"  \033[33m! could not publish: {exc}\033[0m", flush=True)
            return
        print("  \u2601\uFE0F  \033[2mpublished\033[0m", flush=True)
        repo = os.environ.get("GITHUB_REPO", "").split("#")[0].strip()
        tag = os.environ.get("GITHUB_RELEASE_TAG", "").split("#")[0].strip()
        if repo and tag:
            self._closer = (f"You can download the film at "
                            f"https://github.com/{repo}/releases/tag/{tag}")
        else:
            self._closer = "The film is published."

    async def publish_again(self) -> None:
        """Retry publishing the last credited film -- e.g. after a sign-in that
        timed out -- without re-rendering. Same path as the automatic offer."""
        if not self._last_credited.get("video_handle"):
            print("  \033[2mno film to publish yet -- make one first\033[0m", flush=True)
            return
        await self._offer_publish(self._last_credited)

    async def _auto_show(self, out: dict, kind: str, caption: str) -> None:
        """Put things on screen without waiting to be asked.

        Done in code, not left to the prompt: the photo appearing and the film
        playing are the two moments the room is actually there for, and neither
        should depend on the model remembering.
        """
        handle = out.get(f"{kind}_handle")
        if not handle:
            return
        try:
            shown = await tools.call(
                "stage_show", {f"{kind}_handle": handle, "caption": caption})
            print(f"  \u25B6\uFE0F  \033[2m{caption.lower()}\033[0m", flush=True)
            if isinstance(shown, dict) and shown.get("holds"):
                # The window now parks on the QR card instead of closing.
                print("  \033[2m   (it holds on the QR code — press q to close)\033[0m",
                      flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  \033[33m! could not show it: {exc}\033[0m", flush=True)

    async def _auto_open(self, out: dict) -> None:
        """Open the live viewfinder in the browser, without being asked.

        Same reasoning as _auto_show: reading a localhost URL out of a terminal
        and pasting it into a browser is not a thing you should be doing in
        front of a room. If it fails the URL is still in the reply.
        """
        url = (out or {}).get("url", "")
        if not url:
            return
        # Chrome cannot drive Continuity Camera (the iPhone); Safari can. Open
        # the preview in PREVIEW_BROWSER (Safari by default) so the phone works.
        # Set PREVIEW_BROWSER="" to use the system default browser instead.
        app = os.environ.get("PREVIEW_BROWSER", "")  # empty = default browser
        args = {"url": url}
        if app:
            args["app"] = app
        try:
            await tools.call("stage_open_url", args)
            where = f"in {app}" if app else "in your browser"
            print(f"  \U0001F5A5\uFE0F  \033[2mpreview opened {where}\033[0m",
                  flush=True)
        except Exception:  # noqa: BLE001 -- the URL is in the reply either way
            pass

    async def turn(self, text: str) -> str:
        self.user_idea = text.strip()
        # If we publish this turn, the download link becomes the closing line,
        # in place of whatever the model says (which cannot know the URL and
        # tends to narrate stale state like "the film is now playing").
        self._closer = ""
        with tracing.span("director.turn", **{"user.request": text[:300]}):
            return await self._turn(text)

    async def _turn(self, text: str) -> str:
        self.messages.append({"role": "user", "content": text})
        called: set[str] = set()

        for _ in range(16):
            with tracing.span("director.think", **{"gen_ai.request.model": MODEL}):
                resp = self.client.chat.completions.create(
                    model=MODEL, messages=self.messages, tools=self.tools + LOCAL_TOOLS,
                    extra_headers=tracing.headers(),
                )
            msg = resp.choices[0].message
            self.messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                return self._closer or (msg.content or "").strip()

            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")

                # One call per tool per turn. Stops the "try three different
                # wordings of transform_image" behaviour dead.
                if name in called:
                    self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                          "content": json.dumps({
                                              "refused": f"You already called {name} this "
                                                         f"turn. Move on to the next step."})})
                    print(f"    \033[33m✋ {name} already called this turn\033[0m", flush=True)
                    continue
                called.add(name)

                _echo(name, args)
                try:
                    out = await self._run_tool(name, args)
                    if name == "camera_preview_url":
                        await self._auto_open(out)
                    elif name == "camera_capture" or name == "camera_load_image":
                        if isinstance(out, dict) and out.get("image_handle"):
                            self.last_photo = out["image_handle"]   # newest wins
                        await self._auto_show(out, "image", "The cast")
                    elif name == "post_add_credits":
                        await self._auto_show(out, "video", "The film")
                        try:
                            # Short on purpose -- a bilingual sentence is a long
                            # time to stand there. You just need to know it landed.
                            await tools.call("stage_announce", {"en": READY_PHRASE})
                        except Exception:  # noqa: BLE001
                            pass
                        # Ask whether to publish, then (if yes) sign in and
                        # publish -- deterministic, and off the model's plate.
                        self._last_credited = out      # so `publish` can retry
                        await self._offer_publish(out)
                except Exception as exc:  # noqa: BLE001
                    out = {"error": str(exc)}
                    print(f"    \033[31m! {exc}\033[0m", flush=True)

                self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                      "content": json.dumps(out)[:4000]})
        return "That took too many steps. Tell me which part to redo."


async def main() -> None:
    d = Director()
    d.tools = await tools.list_tools()
    print(f"\n\033[1m\U0001F3AC  Director ready.\033[0m\n")
    if VERBOSE:
        print(f"  \033[2m{len(d.tools)} tools via the gateway · outputs -> "
              f"{os.path.abspath(store.OUT)}\033[0m")
        print("  \033[2mctrl-c cancels the current step · ctrl-c at the prompt quits\033[0m\n")

    while True:
        # input() on a thread keeps the event loop responsive, so a ctrl-c
        # mid-turn cancels that turn instead of unwinding the whole program.
        try:
            text = (await asyncio.to_thread(input, "\033[1myou ›\033[0m ")).strip()
        except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit", "q"):
            break
        if text in ("?", "help"):
            print("""
  \033[2mopen the camera
  use the photo at ~/Desktop/room.jpg
  take the photo, count down from 3
  <describe the film>            runs scene, still, film, credits, publish
  publish                        publish the last film again (signs in if needed)
  stop the camera
  ctrl-c cancels the current step · ctrl-c at the prompt quits\033[0m
""")
            continue
        if re.search(r"\bpublish\b", text.lower()):
            # "publish" / "publish again" -> retry the last film through the
            # sign-in + publish path, instead of letting the model deflect.
            await d.publish_again()
            continue
        try:
            print(f"\n\033[1mdirector ›\033[0m {await d.turn(text)}\n")
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Abort this turn, keep the session. Mid-demo you want to redirect,
            # not restart -- restarting loses the photo and every handle with it.
            sys.stdout.write("\r" + " " * 72 + "\r")
            print("\n  \033[33mcancelled — the session is still alive, "
                  "your handles are intact\033[0m\n")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"\n\033[31m! {exc}\033[0m\n")

    await tools.close()
    print("\n\033[2mbye. the gateway and camera are still up — "
          "`./imagine down` stops them, `./imagine camera off` just releases the camera.\033[0m")


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Python 3.14's asyncio.run cancels the main task on SIGINT and then
        # re-raises, so without this you get a traceback on a clean quit.
        print()
    except EOFError:
        print()


if __name__ == "__main__":
    run()
