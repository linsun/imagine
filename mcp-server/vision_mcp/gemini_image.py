"""Gemini (Nano Banana) image generation + editing wrapper.

Uses the google-genai SDK. The same `generate_content` call performs both
text-to-image (generation) and image+text-to-image (editing); the only
difference is whether we include an input image in `contents`.
"""

import base64
import io
import os

from google.genai import types
from PIL import Image

from vision_mcp.genai_client import get_client

DEFAULT_MODEL = os.environ.get("IMAGE_MODEL", "gemini-2.5-flash-image")


def _extract_image(response) -> tuple[bytes, str] | None:
    """Pull the first inline image out of a generate_content response."""
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in content.parts or []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                mime = getattr(inline, "mime_type", "image/png") or "image/png"
                return inline.data, mime
    return None


def _extract_text(response) -> str:
    chunks: list[str] = []
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in content.parts or []:
            if getattr(part, "text", None):
                chunks.append(part.text)
    return "\n".join(chunks).strip()


def transform_image(image_b64: str, instruction: str, model: str | None = None) -> dict:
    """Edit an existing image according to a natural-language instruction.

    Args:
        image_b64: Source image encoded as base64 (no data URI prefix).
        instruction: What to do to the image.
        model: Optional model override.

    Returns a dict with `image_b64`, `mime`, and an optional `note`.
    """
    client = get_client()
    raw = base64.b64decode(image_b64)
    source = Image.open(io.BytesIO(raw))

    response = client.models.generate_content(
        model=model or DEFAULT_MODEL,
        contents=[instruction, source],
        config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
    )

    result = _extract_image(response)
    if result is None:
        note = _extract_text(response) or "Model returned no image."
        raise RuntimeError(f"No image returned from model. {note}")

    data, mime = result
    return {
        "image_b64": base64.b64encode(data).decode("ascii"),
        "mime": mime,
        "note": _extract_text(response),
    }


def generate_image(prompt: str, model: str | None = None) -> dict:
    """Generate a brand-new image from a text prompt."""
    client = get_client()
    response = client.models.generate_content(
        model=model or DEFAULT_MODEL,
        contents=[prompt],
        config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
    )
    result = _extract_image(response)
    if result is None:
        note = _extract_text(response) or "Model returned no image."
        raise RuntimeError(f"No image returned from model. {note}")
    data, mime = result
    return {
        "image_b64": base64.b64encode(data).decode("ascii"),
        "mime": mime,
        "note": _extract_text(response),
    }
