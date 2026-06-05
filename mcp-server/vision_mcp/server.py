"""Vision MCP server.

Exposes instruction-driven image transformation tools backed by Gemini's
Nano Banana image model over the MCP streamable-HTTP transport so it can be
consumed by the backend agent (or any other MCP client) inside Kubernetes.

Tools return image bytes as base64 so no shared volume is required between
pods -- the caller owns storage and serving.
"""

import os

from fastmcp import FastMCP

from vision_mcp import gemini_image, veo_video
from vision_mcp.presets import (
    get_preset_prompt,
    get_video_preset_prompt,
    list_presets,
    list_video_presets,
)

mcp = FastMCP("vision-mcp")


@mcp.tool
def list_styles() -> list[dict]:
    """List the available built-in image style presets (key + human label)."""
    return list_presets()


@mcp.tool
def list_video_styles() -> list[dict]:
    """List the available built-in video motion presets (key + human label)."""
    return list_video_presets()


@mcp.tool
def transform_image(
    image_b64: str,
    instruction: str = "",
    style_preset: str = "",
) -> dict:
    """Transform/edit an existing image using a natural-language instruction.

    Args:
        image_b64: The source image as base64 (no data: prefix).
        instruction: Free-form description of the desired transformation.
        style_preset: Optional preset key (see list_styles) to merge in.

    Returns: { image_b64, mime, note }
    """
    parts: list[str] = []
    preset_prompt = get_preset_prompt(style_preset) if style_preset else None
    if preset_prompt:
        parts.append(preset_prompt)
    if instruction:
        parts.append(instruction)
    if not parts:
        parts.append("Enhance this image with a tasteful, artistic transformation.")
    return gemini_image.transform_image(image_b64, "\n\n".join(parts))


@mcp.tool
def generate_image(prompt: str) -> dict:
    """Generate a brand-new image from a text prompt.

    Returns: { image_b64, mime, note }
    """
    return gemini_image.generate_image(prompt)


@mcp.tool
def generate_video(
    image_b64: str,
    instruction: str = "",
    motion_preset: str = "",
) -> dict:
    """Animate an existing image into a short video clip with Veo.

    This is a long-running operation (typically ~1-2 minutes).

    Args:
        image_b64: The source image as base64 (no data: prefix).
        instruction: Free-form motion/scene description.
        motion_preset: Optional preset key (see list_video_styles).

    Returns: { video_b64, mime }
    """
    parts: list[str] = []
    preset_prompt = get_video_preset_prompt(motion_preset) if motion_preset else None
    if preset_prompt:
        parts.append(preset_prompt)
    if instruction:
        parts.append(instruction)
    return veo_video.animate_image(image_b64, "\n\n".join(parts))


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    mcp.run(transport="streamable-http", host=host, port=port, path="/mcp")


if __name__ == "__main__":
    main()
