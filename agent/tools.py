"""MCP client + the handle/bytes swap, all traffic via agentgateway.

The Director talks to ONE MCP endpoint. Behind it agentgateway federates
camera, stage, publish and vision. Tool names arrive prefixed by target
(camera_capture, vision_transform_image, ...) -- the prompt uses those names.
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

# The SDK logs "Session termination failed: 202" on every clean close because
# agentgateway answers DELETE with 202. Harmless, and it drowns real output.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from agent import auth, store, tracing

MCP_URL = os.environ.get("AGW_MCP", "http://localhost:3001/mcp")


class _Conn:
    """ONE MCP session for the whole run, instead of one per tool call.

    Opening a session per call was the main reason Jaeger showed ~20 unrelated
    traces: every call cost an `initialize`, a `notifications/initialized`, the
    call itself and a DELETE -- four requests, each arriving at the gateway with
    no shared parent. One long-lived session means one handshake at startup and
    a single request per tool call thereafter.

    Reconnects once if the session has gone stale, so a dropped connection
    mid-demo costs a retry rather than the show.
    """

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()

    async def session(self) -> ClientSession:
        async with self._lock:
            if self._session is not None:
                return self._session
            stack = AsyncExitStack()
            try:
                r, w, _ = await stack.enter_async_context(
                    streamablehttp_client(
                        MCP_URL,
                        timeout=timedelta(seconds=30),
                        sse_read_timeout=timedelta(seconds=900),
                        httpx_client_factory=tracing.mcp_httpx_factory(
                            # Attach the person's Keycloak token per request,
                            # so a login mid-session is picked up on the next
                            # call without restarting anything.
                            extra_request_hooks=[auth.attach_bearer]),
                    )
                )
                sess = await stack.enter_async_context(ClientSession(r, w))
                await sess.initialize()
            except BaseException:
                await stack.aclose()
                raise
            self._stack, self._session = stack, sess
            return sess

    async def reset(self) -> None:
        async with self._lock:
            stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            try:
                await stack.aclose()
            except BaseException:  # noqa: BLE001 -- teardown must never raise
                pass


_conn = _Conn()


async def close() -> None:
    await _conn.reset()


async def reset() -> None:
    """Drop the MCP session and reconnect on the next call.

    Used after a browser login: mcpAuthentication binds identity at connect
    time, so a session opened without a token must be reopened for the token
    to take effect. Handles live in the Director's store, not the session, so
    nothing is lost.
    """
    await _conn.reset()

# Params that carry bytes. The model passes a handle; we substitute.
_BYTES_IN = {"image_b64": "image", "video_b64": "video"}

# Every tool you expose is a wrong turn the model can take on stage. These are
# real and useful, but not part of this show, so the Director never sees them.
HIDDEN = {
    "vision_generate_image",     # makes an unrelated picture from text
    "vision_list_styles",
    "vision_list_video_styles",
    "post_list_cast",
    # The release asset is public as soon as it uploads and the QR points
    # straight at it, so the PR added a step and a permission for nothing.
    # The tool still exists in publish_mcp if you want the beat back.
    "publish_open_pr",
    # Publishing is code-driven: _publish() in director.py runs it AFTER the
    # film is on screen, and wraps it in the browser-login-and-retry. The
    # model never calls it. (The gateway also filters it from an
    # unauthenticated tools/list, so it could not reliably see it anyway.)
    "publish_publish_video",
    "publish_check_auth",
    # Called from code the moment the preview URL comes back, so the model
    # never has to remember it -- and never gets to open anything else.
    "stage_open_url",
}


def _flatten(exc: BaseException) -> BaseException:
    """Dig the real error out of the TaskGroup ExceptionGroup the SDK raises.

    Without this every failure reads "unhandled errors in a TaskGroup
    (1 sub-exception)", which tells you nothing.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _unwrap(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    return None


async def list_tools() -> list[dict]:
    """Fetch the tool list from the gateway and convert to OpenAI tool schema."""
    try:
        sess = await _conn.session()
        tracing.set_pending(tracing.headers())
        tools = (await sess.list_tools()).tools
    except BaseException as exc:
        await _conn.reset()
        raise RuntimeError(f"MCP {MCP_URL}: {_flatten(exc)}") from None
    out = []
    for t in tools:
        if t.name in HIDDEN:
            continue
        schema = t.inputSchema or {"type": "object", "properties": {}}
        props = dict(schema.get("properties") or {})
        required = list(schema.get("required") or [])
        # Present byte params to the model as handles.
        for p, kind in _BYTES_IN.items():
            if p in props:
                props.pop(p)
                props[f"{kind}_handle"] = {
                    "type": "string",
                    "description": f"Handle of the {kind} to use, e.g. from a previous tool.",
                }
                if p in required:
                    required = [f"{kind}_handle" if x == p else x for x in required]
        out.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "").strip()[:900],
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        })
    return out


async def call(name: str, args: dict) -> dict:
    """Call a tool through the gateway, swapping handles for bytes both ways."""
    args = dict(args or {})
    for param, kind in _BYTES_IN.items():
        h = args.pop(f"{kind}_handle", None)
        if h:
            args[param] = store.get(h)

    async def _once() -> Any:
        sess = await _conn.session()
        # Captured HERE, in the caller's task, where the tool span is active.
        tracing.set_pending(tracing.headers())
        res = await sess.call_tool(name, args)
        if getattr(res, "isError", False):
            raise RuntimeError(f"{name}: {_unwrap(res) or 'tool call failed'}")
        return _unwrap(res)

    try:
        payload = await _once()
    except BaseException as exc:
        first = _flatten(exc)
        await _conn.reset()
        try:
            payload = await _once()          # one reconnect, then give up
        except BaseException:
            raise RuntimeError(f"{name}: {first}") from None

    if not isinstance(payload, dict):
        return {"result": payload}

    # Store any returned bytes and hand the model a handle instead.
    slim = {k: v for k, v in payload.items() if k not in ("image_b64", "video_b64")}
    if payload.get("image_b64"):
        slim["image_handle"] = store.put(payload["image_b64"], payload.get("mime", "image/png"))
    if payload.get("video_b64"):
        slim["video_handle"] = store.put(payload["video_b64"], "video/mp4")
    return slim
