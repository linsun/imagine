"""Talk to the Director running in Kubernetes. Same stage feel as the local
chat loop -- `you ›`, one quiet line per step, the reply -- but every turn is
an A2A message/stream to the Director, through agentgateway.

When a turn puts something new on the stage -- the photo, or the film -- its
URL is opened in your browser, which shows it (and plays the film). When the Director opens the camera, the live
preview is opened too (in PREVIEW_BROWSER if set -- Safari, for the iPhone).

  DIRECTOR_A2A=http://localhost:3006/director python -m agent.client

With the Director on kagent (k8s/agents/), set DIRECTOR_A2A_PROTOCOL=v1: the
same turn as an A2A v1 SendStreamingMessage. kagent assigns the contextId (it
is the session, and so the actor); the client prints it, and DIRECTOR_CONTEXT
pins it on the next run.
"""

import json
import os
import re
import subprocess
import sys
import uuid

import httpx

from agent import auth

URL = os.environ.get("DIRECTOR_A2A", "http://localhost:3006/director").rstrip("/") + "/"
# "v1" for the Director on kagent (./imagine k8s demo sets it); 0.3 on the laptop.
PROTOCOL = os.environ.get("DIRECTOR_A2A_PROTOCOL") or "0.3"
STAGE = os.environ.get("STAGE_URL", "http://localhost:8888").rstrip("/")
HELP = """
  \033[2muse my latest photo            the newest photo sent from the phone
  take the photo, count down from 3
  <describe the film>            scene, still, film, credits -> opens in your browser
  publish                        publish the last film to GitHub
  quit\033[0m
"""


def turn(client: httpx.Client, ctx: str, text: str, steps: list) -> tuple[str, str]:
    """One turn; returns (reply, contextId)."""
    headers = {"Accept": "text/event-stream"}
    # Publishing needs a signed-in person (the gateway's MCP auth policy). Sign in
    # here, on the laptop -- the Director runs in a pod with no browser -- and
    # send the token with the request. Same flow as the laptop version.
    if re.search(r"\bpublish\b", text.lower()) and not auth.token():
        print("  \U0001F511  \033[2mopening your browser to sign in\033[0m", flush=True)
        res = auth.login()
        if not res.get("ok"):
            return f"! sign-in did not complete: {res.get('error')}", ctx
        print(f"  \033[2m\u2713 signed in as {res['user']}\033[0m", flush=True)
    if auth.token():
        headers["Authorization"] = f"Bearer {auth.token()}"
    if PROTOCOL == "v1":
        return _turn_v1(client, ctx, text, steps, headers)
    body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/stream",
            "params": {"message": {"role": "user", "kind": "message",
                                   "messageId": uuid.uuid4().hex, "contextId": ctx,
                                   "parts": [{"kind": "text", "text": text}]}}}
    reply = ""
    with client.stream("POST", URL, json=body, headers=headers) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            if "error" in ev:
                return f"! {ev['error'].get('message')}", ctx
            res = ev.get("result") or {}
            if res.get("kind") == "status-update" and not res.get("final"):
                msg = (res.get("status") or {}).get("message") or {}
                for p in msg.get("parts") or []:
                    steps.append(p.get("text", ""))
                    print(f"  \033[2m{p.get('text', '')}\033[0m", flush=True)
            elif res.get("kind") == "artifact-update":
                reply = "\n".join(p.get("text", "") for p in res["artifact"]["parts"])
    return reply, ctx


def _texts(parts) -> str:
    return "\n".join(p.get("text", "") for p in parts or [])


def _turn_v1(client: httpx.Client, ctx: str, text: str, steps: list,
             headers: dict) -> tuple[str, str]:
    message = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}]}
    if ctx:
        message["contextId"] = ctx
    body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "SendStreamingMessage",
            "params": {"message": message}}
    reply = ""
    with client.stream("POST", URL, json=body,
                       headers={**headers, "A2A-Version": "1.0"}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            if "error" in ev:
                return f"! {ev['error'].get('message')}", ctx
            res = ev.get("result") or {}
            # One of task / statusUpdate / artifactUpdate / message.
            item = next(iter(res.values()), {}) if res else {}
            ctx = item.get("contextId") or ctx
            if "statusUpdate" in res:
                status = res["statusUpdate"].get("status") or {}
                line_text = _texts((status.get("message") or {}).get("parts"))
                if status.get("state") == "TASK_STATE_WORKING" and line_text:
                    steps.append(line_text)
                    print(f"  \033[2m{line_text}\033[0m", flush=True)
                elif line_text:
                    reply = line_text
            elif "artifactUpdate" in res:
                reply = _texts(res["artifactUpdate"]["artifact"].get("parts"))
            elif "task" in res:
                # kagent ends the stream with the task as it saved it.
                task = res["task"]
                arts = [p for a in task.get("artifacts") or [] for p in a.get("parts") or []]
                reply = _texts(arts) or _texts(((task.get("status") or {}).get("message") or {})
                                               .get("parts")) or reply
            elif "message" in res:
                reply = _texts(res["message"].get("parts"))
    return reply, ctx


# ------------------------------------------------------------------ the film
def stage_state(client: httpx.Client) -> dict:
    """What is on the stage right now: {"seq": n, "kind": "image"|"video", ...}."""
    try:
        return client.get(f"{STAGE}/stage/state", timeout=5).json()
    except (httpx.HTTPError, ValueError):
        return {}


def main() -> None:
    # One conversation = one context in the Director. DIRECTOR_CONTEXT pins it,
    # so restarting this client mid-show picks up the same photo and film.
    # On kagent the context must be one kagent made, so start without one.
    ctx = os.environ.get("DIRECTOR_CONTEXT") or ("" if PROTOCOL == "v1" else uuid.uuid4().hex)
    print(f"\n\033[1m\U0001F3AC  Director ready\033[0m \033[2m(in Kubernetes, via {URL})\033[0m\n")
    with httpx.Client(timeout=httpx.Timeout(900, connect=10)) as client:
        while True:
            try:
                text = input("\033[1myou ›\033[0m ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not text:
                continue
            if text.lower() in ("quit", "exit", "q"):
                break
            if text in ("?", "help"):
                print(HELP)
                continue
            before = stage_state(client).get("seq")
            steps: list[str] = []
            try:
                reply, new_ctx = turn(client, ctx, text, steps)
                print(f"\n\033[1mdirector ›\033[0m {reply}\n")
                if new_ctx != ctx:
                    ctx = new_ctx
                    print(f"  \033[2m(session {ctx}: DIRECTOR_CONTEXT={ctx} picks it up again)\033[0m\n")
            except KeyboardInterrupt:
                print("\n  \033[33mstopped waiting -- the turn may still finish "
                      "in the pod\033[0m\n")
                continue
            except httpx.HTTPError as exc:
                print(f"\n\033[31m! {exc}\033[0m\n", file=sys.stderr)
                continue
            after = stage_state(client)
            if after.get("kind") in ("image", "video") and after.get("seq") != before:
                # New on the stage: the photo or the film. The ?seq= keeps the
                # browser from showing a cached older one.
                media = f"{STAGE}/stage/media?seq={after.get('seq')}"
                label = "film" if after["kind"] == "video" else "photo"
                icon = "\U0001F39E\uFE0F" if label == "film" else "\U0001F5BC\uFE0F"
                print(f"  {icon}  {label}: {media}\n")
                subprocess.run(["open", media], check=False)
            if any("finding the camera" in s for s in steps):
                # The Director found the camera; a pod cannot open a window here.
                browser = os.environ.get("PREVIEW_BROWSER", "")
                subprocess.run(["open"] + (["-a", browser] if browser else []) + [f"{STAGE}/"],
                               check=False)
                print(f"  \U0001F4F7  camera preview: {STAGE}/\n")


if __name__ == "__main__":
    main()
