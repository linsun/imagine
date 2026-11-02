"""The Director as an A2A server -- how it runs in Kubernetes.

On the laptop the Director is a terminal chat loop. In a pod there is no
keyboard, so the same Director is wrapped in A2A (protocol 0.3, JSON-RPC):

  message/send     run one turn, reply when it is done
  message/stream   the same turn, streaming each step ("painting your room")
                   as it happens -- what ./imagine k8s demo uses
  tasks/get        look up a turn
  tasks/cancel     stop a running turn

This is the laptop's A2A 0.3 server. In the cluster the same Director runs
behind kagent instead (agent/kagent_runtime.py, A2A v1).
Every call the Director makes OUT (LLM, MCP, Scout, DP) goes through agentgateway.
One conversation (A2A contextId) = one Director, so the photo and the film
handles survive from "use my latest photo" to "make them dance" to "publish".

  python -m agent.a2a_server
"""

import asyncio
import json
import os
import re
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent import auth, tools, tracing
from agent.director import Director

PORT = int(os.environ.get("PORT", "8080"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", f"http://localhost:{PORT}/")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

CARD = {
    "name": "director",
    "description": "The Director: turns a photo of the room into a short film, "
                   "with a Location Scout and a Director of Photography as crew.",
    "url": PUBLIC_URL,
    "version": "1.0.0",
    "protocolVersion": "0.3.0",
    "capabilities": {"streaming": True},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "skills": [
        {"id": "photo", "name": "Get the photo",
         "description": "Use the latest photo sent from the phone, or take one "
                        "with the live camera.",
         "examples": ["use my latest photo", "take the photo, count down from 3"],
         "tags": ["camera"]},
        {"id": "film", "name": "Make the film",
         "description": "Describe the film; the crew makes it and puts it on the stage.",
         "examples": ["make them dance, festival vibe"], "tags": ["film"]},
        {"id": "publish", "name": "Publish",
         "description": "Publish the last film to GitHub.",
         "examples": ["publish"], "tags": ["publish"]},
    ],
}

_directors: dict[str, Director] = {}
_locks: dict[str, asyncio.Lock] = {}
_tasks: dict[str, dict] = {}
_running: dict[str, asyncio.Task] = {}
_tool_list: list[dict] | None = None


async def _director(ctx: str) -> Director:
    global _tool_list
    if _tool_list is None:
        _tool_list = await tools.list_tools()
    if ctx not in _directors:
        d = Director(interactive=False)
        d.tools = _tool_list
        _directors[ctx] = d
        _locks[ctx] = asyncio.Lock()
    return _directors[ctx]


def _text_of(message: dict) -> str:
    return "\n".join(p.get("text", "") for p in message.get("parts") or []
                     if p.get("kind", "text") == "text").strip()


def _msg(text: str, ctx: str, task_id: str) -> dict:
    return {"kind": "message", "role": "agent", "messageId": uuid.uuid4().hex,
            "contextId": ctx, "taskId": task_id,
            "parts": [{"kind": "text", "text": text}]}


def _status(task_id: str, ctx: str, state: str, text: str = "",
            final: bool = False) -> dict:
    st = {"state": state}
    if text:
        st["message"] = _msg(text, ctx, task_id)
    return {"kind": "status-update", "taskId": task_id, "contextId": ctx,
            "status": st, "final": final}


async def _run_turn(ctx: str, text: str, emit, trace_headers: dict,
                    user_token: str = "") -> str:
    d = await _director(ctx)
    async with _locks[ctx]:
        # The signed-in person, if any (Authorization from the terminal client).
        # The gateway binds identity when the MCP session connects, so a new
        # identity means reconnecting -- as the laptop does after a login.
        if auth.use_forwarded(user_token):
            await tools.reset()
        d.emit = lambda line: emit(_ANSI.sub("", line).strip())
        try:
            # Continue the caller's trace (gateway -> router -> resume), so the turn
            # shows up under the request that woke this actor, not as its own trace.
            with tracing.continue_from(trace_headers):
                if re.search(r"\bpublish\b", text.lower()):
                    return await d.publish_again()
                return await d.turn(text)
        finally:
            # Spans are batched; send this turn's now, so the trace is in
            # Jaeger even if the actor is suspended right after.
            await asyncio.to_thread(tracing.flush)


def _start(params: dict) -> tuple[str, str, str, dict]:
    message = params.get("message") or {}
    ctx = message.get("contextId") or uuid.uuid4().hex
    task_id = uuid.uuid4().hex
    task = {"kind": "task", "id": task_id, "contextId": ctx,
            "status": {"state": "submitted"}, "history": [message]}
    _tasks[task_id] = task
    return ctx, task_id, _text_of(message), task


def _finish(task: dict, reply: str, state: str = "completed") -> None:
    task["status"] = {"state": state,
                      "message": _msg(reply, task["contextId"], task["id"])}
    task["artifacts"] = [{"artifactId": uuid.uuid4().hex, "name": "reply",
                          "parts": [{"kind": "text", "text": reply}]}]


app = FastAPI()


@app.get("/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
async def agent_card():
    return CARD


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/debug/stacks")
async def debug_stacks():
    """Where every thread and asyncio task is right now -- for a turn that
    hangs. Served outside the per-conversation lock, so it answers even then."""
    import sys
    import traceback
    from fastapi.responses import PlainTextResponse
    out = []
    for tid, frame in sys._current_frames().items():
        out.append(f"--- thread {tid}\n" + "".join(traceback.format_stack(frame)[-8:]))
    for task in asyncio.all_tasks():
        stack = task.get_stack(limit=8)
        out.append(f"--- task {task.get_name()}\n" + "".join(
            traceback.format_list(traceback.extract_stack(stack[-1])[-8:]) if stack else ["(no frames)\n"]))
    return PlainTextResponse("\n".join(out))


@app.post("/")
async def rpc(request: Request):
    body = await request.json()
    rid, method, params = body.get("id"), body.get("method"), body.get("params") or {}
    trace_headers = {k: v for k, v in request.headers.items()
                     if k.lower() in ("traceparent", "tracestate")}
    authz = request.headers.get("authorization", "")
    user_token = authz[7:] if authz.lower().startswith("bearer ") else ""

    def ok(result):
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})

    def err(code, text):
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
                             "error": {"code": code, "message": text}})

    if method == "message/send":
        ctx, task_id, text, task = _start(params)
        try:
            reply = await _run_turn(ctx, text, lambda _line: None, trace_headers, user_token)
            _finish(task, reply)
        except Exception as exc:  # noqa: BLE001
            _finish(task, f"! {exc}", "failed")
        return ok(task)

    if method == "message/stream":
        ctx, task_id, text, task = _start(params)
        queue: asyncio.Queue = asyncio.Queue()

        async def work():
            try:
                reply = await _run_turn(ctx, text, lambda line: line and queue.put_nowait(
                    _status(task_id, ctx, "working", line)), trace_headers, user_token)
                _finish(task, reply)
            except asyncio.CancelledError:
                _finish(task, "cancelled", "canceled")
            except Exception as exc:  # noqa: BLE001
                _finish(task, f"! {exc}", "failed")
            queue.put_nowait(None)

        _running[task_id] = asyncio.create_task(work())

        async def events():
            def sse(result):
                return f"data: {json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result})}\n\n"
            yield sse({**task, "status": {"state": "working"}})
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"      # Veo takes ~90s of silence
                    continue
                if ev is None:
                    break
                yield sse(ev)
            art = task["artifacts"][0]
            yield sse({"kind": "artifact-update", "taskId": task_id, "contextId": ctx,
                       "artifact": art, "lastChunk": True})
            yield sse({"kind": "status-update", "taskId": task_id, "contextId": ctx,
                       "status": task["status"], "final": True})
            _running.pop(task_id, None)

        return StreamingResponse(events(), media_type="text/event-stream")

    if method == "tasks/get":
        t = _tasks.get(params.get("id", ""))
        return ok(t) if t else err(-32001, "task not found")

    if method == "tasks/cancel":
        t = _tasks.get(params.get("id", ""))
        if not t:
            return err(-32001, "task not found")
        if params.get("id") in _running:
            _running[params["id"]].cancel()
        return ok(t)

    return err(-32601, f"method not found: {method}")


if __name__ == "__main__":
    # atenet-router pools connections to this actor. uvicorn's default idle
    # keep-alive (5s) is shorter than the router's, so the router would reuse a
    # connection uvicorn had just closed: a 503 ("connection closed before
    # message completed"), or a hang when the close is lost in the sandbox's
    # tunnel. Keep idle connections longer than any proxy in front of us does.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning",
                timeout_keep_alive=3600)
