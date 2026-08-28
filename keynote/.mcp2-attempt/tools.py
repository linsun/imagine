"""MCP client (2026-07-28 spec) + the handle/bytes swap, all via agentgateway.

The Director talks to ONE MCP endpoint. Behind it agentgateway federates
camera, stage, post, publish and vision. Tool names arrive prefixed by target
(camera_capture, vision_transform_image, ...) -- the prompt uses those names.

MCP 2026-07-28 (mcp 2.x + fastmcp 4):
  * no `initialize` / `notifications/initialized` handshake -- `discover()`
    negotiates inline, and the gateway listener runs `statefulMode: stateless`
  * no Mcp-Session-Id, so nothing to tear down: `terminate_on_close=False`
  * protocol metadata AND the per-operation trace context ride in `params._meta`

That last point is why this file hands the transport its own httpx client: the
`_meta` traceparent is stamped in the CALLER's task, and a request hook copies
it into HTTP headers, which is the only place agentgateway looks.
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from agent import store, tracing

logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)

MCP_URL = os.environ.get("AGW_MCP", "http://localhost:3001/mcp")

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
    "publish_open_pr",
}


class _Conn:
    """ONE MCP session for the whole run.

    A session per call cost four requests each (initialize, initialized, the
    call, a DELETE) and scattered them across unrelated traces. Under the
    2026-07-28 flow there is no handshake at all, so this is one `discover()`
    at startup and a single request per tool call after it.
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
                http = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        timeout=httpx2.Timeout(30.0, read=900.0),
                        follow_redirects=True,
                        # Mirror the SDK's per-operation `_meta` trace context
                        # into headers at send time. A static default header
                        # would carry the same (wrong) span on every call.
                        event_hooks={"request": [tracing.mirror_mcp_trace_headers]},
                    )
                )
                read, write = await stack.enter_async_context(
                    streamable_http_client(
                        MCP_URL, http_client=http, terminate_on_close=False
                    )
                )
                sess = await stack.enter_async_context(ClientSession(read, write))
                # 2026-07-28: negotiate without an initialize handshake.
                await sess.discover()
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


def _flatten(exc: BaseException) -> BaseException:
    """Dig the real error out of the TaskGroup ExceptionGroup the SDK raises."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _unwrap(result: Any) -> Any:
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None)
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
        tools = (await sess.list_tools()).tools
    except BaseException as exc:
        await _conn.reset()
        raise RuntimeError(f"MCP {MCP_URL}: {_flatten(exc)}") from None

    out = []
    for t in tools:
        if t.name in HIDDEN:
            continue
        # mcp 2.x renamed inputSchema -> input_schema.
        schema = getattr(t, "input_schema", None) or {"type": "object", "properties": {}}
        props = dict(schema.get("properties") or {})
        required = list(schema.get("required") or [])
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
        res = await sess.call_tool(name, args)
        if getattr(res, "is_error", False) or getattr(res, "isError", False):
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

    slim = {k: v for k, v in payload.items() if k not in ("image_b64", "video_b64")}
    if payload.get("image_b64"):
        slim["image_handle"] = store.put(payload["image_b64"], payload.get("mime", "image/png"))
    if payload.get("video_b64"):
        slim["video_handle"] = store.put(payload["video_b64"], "video/mp4")
    return slim
