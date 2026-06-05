"""Lightweight Gemini agent that drives the vision MCP tools via function calling.

The active image (if any) is NOT passed through the model -- the model only
decides *what* to do and supplies an instruction/style. The backend injects the
actual image bytes when invoking the MCP `transform_image` tool. This keeps the
prompt small and avoids round-tripping large base64 blobs through the LLM.
"""

from google import genai
from google.genai import types

from app import config, mcp_client

SYSTEM_INSTRUCTION = (
    "You are a friendly vision agent. The user can capture a photo with their "
    "webcam or upload one, and ask you to transform it. When they describe a "
    "transformation of the current image, call `transform_image` with a clear, "
    "vivid instruction (and a style_preset key when it fits). When they ask to "
    "create a brand-new image from scratch, call `generate_image`. When they ask "
    "to animate the current image or turn it into a video/clip, call "
    "`generate_video` (this takes 1-2 minutes). Veo generates native audio, so "
    "whenever the user mentions sound, music, a song, voices, or ambience, you MUST "
    "carry that intent into the `generate_video` instruction as explicit audio cues "
    "alongside the motion (e.g. 'upbeat cheerful background music, people chatting "
    "and laughing'). Never silently drop a request for music or sound. Use "
    "`list_styles` if they ask what styles exist. After a tool runs, reply with a "
    "short, upbeat confirmation of what you did. If no image is loaded and the user "
    "asks to transform or animate one, ask them to capture or upload a photo first."
)

_TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="transform_image",
            description="Transform the currently loaded image using an instruction and/or a style preset.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "instruction": types.Schema(
                        type=types.Type.STRING,
                        description="Vivid description of the desired transformation.",
                    ),
                    "style_preset": types.Schema(
                        type=types.Type.STRING,
                        description="Optional preset key, e.g. nyc_vibe, japanese_culture, bridges.",
                    ),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="generate_image",
            description="Generate a brand-new image from a text prompt (no source image).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "prompt": types.Schema(
                        type=types.Type.STRING,
                        description="Description of the image to generate.",
                    ),
                },
                required=["prompt"],
            ),
        ),
        types.FunctionDeclaration(
            name="generate_video",
            description="Animate the currently loaded image into a short video clip (long-running, ~1-2 min).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "instruction": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Motion/scene description for the animation. Veo also "
                            "generates native audio, so include any requested audio "
                            "cues here too: background music and its mood/genre, "
                            "sound effects, ambience, voices, or dialogue. Always "
                            "preserve the user's music/sound intent."
                        ),
                    ),
                    "motion_preset": types.Schema(
                        type=types.Type.STRING,
                        description="Optional motion preset key, e.g. gentle_pan, dramatic_zoom.",
                    ),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="list_styles",
            description="List the available built-in style presets.",
            parameters=types.Schema(type=types.Type.OBJECT, properties={}),
        ),
    ]
)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set on the backend.")
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


async def _run_tool(name: str, args: dict, image_b64: str | None) -> tuple[dict, dict | None]:
    """Execute a tool. Returns (model_facing_summary, produced_image_or_None)."""
    if name == "list_styles":
        styles = await mcp_client.list_styles()
        return {"styles": styles}, None

    if name == "generate_image":
        result = await mcp_client.generate_image(args.get("prompt", ""))
        return {"status": "ok", "note": result.get("note", "")}, result

    if name == "generate_video":
        if not image_b64:
            return {"status": "error", "message": "No image is loaded."}, None
        result = await mcp_client.generate_video(
            image_b64,
            instruction=args.get("instruction", ""),
            motion_preset=args.get("motion_preset", ""),
        )
        return {"status": "ok", "note": "Video generated."}, result

    if name == "transform_image":
        if not image_b64:
            return {"status": "error", "message": "No image is loaded."}, None
        result = await mcp_client.transform_image(
            image_b64,
            instruction=args.get("instruction", ""),
            style_preset=args.get("style_preset", ""),
        )
        return {"status": "ok", "note": result.get("note", "")}, result

    return {"status": "error", "message": f"Unknown tool {name}"}, None


async def run(message: str, image_b64: str | None) -> dict:
    """Run one agent turn. Returns { text, image_b64?, mime? }."""
    client = _get_client()
    image_hint = "An image is currently loaded." if image_b64 else "No image is loaded yet."
    contents: list[types.Content] = [
        types.Content(
            role="user",
            parts=[types.Part(text=f"[context: {image_hint}]\n\n{message}")],
        )
    ]
    cfg = types.GenerateContentConfig(
        tools=[_TOOLS],
        system_instruction=SYSTEM_INSTRUCTION,
    )

    produced_image: dict | None = None
    produced_video: dict | None = None

    def _result(text: str) -> dict:
        return {
            "text": text,
            "image_b64": produced_image.get("image_b64") if produced_image else None,
            "mime": produced_image.get("mime") if produced_image else None,
            "video_b64": produced_video.get("video_b64") if produced_video else None,
        }

    for _ in range(5):  # cap tool-calling rounds
        response = client.models.generate_content(
            model=config.TEXT_MODEL, contents=contents, config=cfg
        )
        calls = response.function_calls or []
        if not calls:
            return _result((response.text or "Done.").strip())

        contents.append(response.candidates[0].content)
        for fc in calls:
            summary, result = await _run_tool(fc.name, dict(fc.args or {}), image_b64)
            if result and result.get("image_b64"):
                produced_image = result
            if result and result.get("video_b64"):
                produced_video = result
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=fc.name, response=summary)],
                )
            )

    return _result("I ran into a loop completing that. Try rephrasing your request.")
