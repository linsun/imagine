"""Shared google-genai client factory used by the image and video tools.

THE DOUBLE HOP
--------------
Set GEMINI_BASE_URL to agentgateway and this MCP server stops holding a real
model credential: its own Gemini egress is proxied, policied and metered by the
gateway, exactly like the agent's traffic is. That is what makes
"nothing here has an API key except the gateway" literally true.

    GEMINI_BASE_URL=http://localhost:3000   # image calls via the gateway
    VEO_BASE_URL=                           # video direct (see below)

Image generation uses Gemini's native :generateContent, which agentgateway
treats as a first-class route -- full policy, telemetry and token accounting.

Video is different. Veo's :predictLongRunning falls through to agentgateway's
Passthrough arm: it is proxied and logged, but with no token count, no cost and
no model attribution. It is governed one layer up instead, as an MCP tool call.
VEO_BASE_URL is left empty by default so the long-running video path stays as
simple as possible; set it if you want that hop proxied too.
"""

import os

from google import genai
from google.genai import types

_clients: dict[str, genai.Client] = {}


def _api_key(base_url: str = "") -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    if base_url:
        # The gateway injects the real credential. The SDK still insists on a
        # non-empty key, so hand it a placeholder. This is the good case: it
        # means this process genuinely has no model credential.
        return "agentgateway"
    raise RuntimeError(
        "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set, and no GEMINI_BASE_URL "
        "is configured to supply one. Get a key at https://aistudio.google.com/apikey"
    )


def _build(base_url: str) -> genai.Client:
    if base_url:
        return genai.Client(
            api_key=_api_key(base_url),
            http_options=types.HttpOptions(base_url=base_url),
        )
    return genai.Client(api_key=_api_key())


def get_client() -> genai.Client:
    """Client for image generation. Routed via GEMINI_BASE_URL when set."""
    base = os.environ.get("GEMINI_BASE_URL", "").rstrip("/")
    if "image" not in _clients:
        _clients["image"] = _build(base)
    return _clients["image"]


def get_video_client() -> genai.Client:
    """Client for Veo. Direct by default; set VEO_BASE_URL to proxy it too."""
    base = os.environ.get("VEO_BASE_URL", "").rstrip("/")
    if "video" not in _clients:
        _clients["video"] = _build(base)
    return _clients["video"]
