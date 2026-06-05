"""Thin async MCP client for the vision MCP server.

Opens a short-lived streamable-HTTP session per call. This keeps the client
stateless and resilient to MCP server restarts inside Kubernetes.
"""

import json
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import MCP_URL

# Video generation can take several minutes, so allow a long read timeout
# (must exceed the MCP server's own VIDEO_MAX_WAIT cap).
_HTTP_TIMEOUT = timedelta(seconds=30)
_SSE_READ_TIMEOUT = timedelta(seconds=960)


def _parse_result(result: Any) -> Any:
    """Extract the structured/text payload from a tool call result."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        # FastMCP wraps non-dict returns under a "result" key.
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured

    contents = getattr(result, "content", None) or []
    for item in contents:
        text = getattr(item, "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    return None


def _error_text(result: Any) -> str:
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            return text
    return "Tool call failed."


async def call_tool(name: str, arguments: dict) -> Any:
    async with streamablehttp_client(
        MCP_URL, timeout=_HTTP_TIMEOUT, sse_read_timeout=_SSE_READ_TIMEOUT
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if getattr(result, "isError", False):
                raise RuntimeError(_error_text(result))
            return _parse_result(result)


async def list_styles() -> list[dict]:
    result = await call_tool("list_styles", {})
    return result if isinstance(result, list) else []


async def list_video_styles() -> list[dict]:
    result = await call_tool("list_video_styles", {})
    return result if isinstance(result, list) else []


async def transform_image(
    image_b64: str, instruction: str = "", style_preset: str = ""
) -> dict:
    return await call_tool(
        "transform_image",
        {
            "image_b64": image_b64,
            "instruction": instruction,
            "style_preset": style_preset,
        },
    )


async def generate_image(prompt: str) -> dict:
    return await call_tool("generate_image", {"prompt": prompt})


async def generate_video(
    image_b64: str, instruction: str = "", motion_preset: str = ""
) -> dict:
    return await call_tool(
        "generate_video",
        {
            "image_b64": image_b64,
            "instruction": instruction,
            "motion_preset": motion_preset,
        },
    )
