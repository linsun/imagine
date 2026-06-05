"""Veo image-to-video generation wrapper.

Veo generation is a long-running operation: we submit the job and poll until it
completes, then return the resulting MP4 as base64. Uses the fast model by
default to keep latency to ~1-2 minutes.
"""

import base64
import logging
import os
import time

from google.genai import types

from vision_mcp.genai_client import get_client

logger = logging.getLogger("vision_mcp.veo")
logging.basicConfig(level=logging.INFO)

DEFAULT_VIDEO_MODEL = os.environ.get("VIDEO_MODEL", "veo-3.1-lite-generate-preview")
# Shorter clips generate noticeably faster. Veo 3.1 supports 4/6/8 seconds.
VIDEO_DURATION_SECONDS = int(os.environ.get("VIDEO_DURATION", "6"))
# Hard cap on how long we wait before giving up (seconds).
MAX_WAIT_SECONDS = int(os.environ.get("VIDEO_MAX_WAIT", "900"))
POLL_INTERVAL_SECONDS = int(os.environ.get("VIDEO_POLL_INTERVAL", "10"))


def _extract_video_bytes(client, operation) -> bytes:
    generated = (operation.response.generated_videos or [None])[0]
    if generated is None:
        raise RuntimeError("Veo returned no video.")
    video = generated.video
    data = getattr(video, "video_bytes", None)
    if not data:
        # Some responses return a file handle that must be downloaded first.
        client.files.download(file=video)
        data = getattr(video, "video_bytes", None)
    if not data:
        raise RuntimeError("Could not read video bytes from Veo response.")
    return data


# Words that signal the user wants background music in the clip.
_MUSIC_KEYWORDS = (
    "music",
    "song",
    "soundtrack",
    "melody",
    "tune",
    "score",
    "instrumental",
    "jingle",
    "background track",
)

# Veo defaults to generating spoken dialogue for scenes with people and often
# drops the music. When music is requested we append an explicit audio directive
# that keeps the natural conversation but layers clear background music
# underneath it for the whole clip.
_MUSIC_AUDIO_DIRECTIVE = (
    "Audio: keep the natural conversation and people talking, and at the same "
    "time layer pleasant instrumental background music playing continuously "
    "underneath the voices throughout the entire clip. The music should stay "
    "clearly audible the whole time, sitting just below the dialogue like a "
    "soundtrack."
)


def _augment_audio(prompt: str) -> str:
    """Append an explicit music audio directive when the user asks for music."""
    lowered = prompt.lower()
    if any(kw in lowered for kw in _MUSIC_KEYWORDS) and "audio:" not in lowered:
        return f"{prompt}\n\n{_MUSIC_AUDIO_DIRECTIVE}"
    return prompt


def animate_image(image_b64: str, prompt: str = "", model: str | None = None) -> dict:
    """Animate a still image into a short video clip.

    Args:
        image_b64: Source image as base64 (no data: prefix).
        prompt: Motion/scene description. Defaults to gentle natural motion.
        model: Optional Veo model override.

    Returns: { video_b64, mime }
    """
    client = get_client()
    raw = base64.b64decode(image_b64)
    image = types.Image(image_bytes=raw, mime_type="image/png")

    motion_prompt = prompt.strip() or "Bring this image to life with gentle, natural motion."
    motion_prompt = _augment_audio(motion_prompt)
    logger.info("Veo prompt: %s", motion_prompt)

    video_model = model or DEFAULT_VIDEO_MODEL
    logger.info(
        "Submitting Veo job (model=%s, duration=%ss)...",
        video_model,
        VIDEO_DURATION_SECONDS,
    )
    operation = client.models.generate_videos(
        model=video_model,
        prompt=motion_prompt,
        image=image,
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            duration_seconds=VIDEO_DURATION_SECONDS,
            # NOTE: Do NOT set `generate_audio` here. On the Gemini Developer API
            # (API key) that parameter is rejected ("generate_audio parameter is
            # not supported in Gemini API"). Veo 3.x generates native audio by
            # default on this API, so describe the desired sound/music in the prompt.
        ),
    )

    waited = 0
    while not operation.done:
        if waited >= MAX_WAIT_SECONDS:
            raise TimeoutError(
                f"Video generation exceeded {MAX_WAIT_SECONDS}s. Try the fast model "
                f"or a shorter clip."
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
        operation = client.operations.get(operation)
        logger.info("Veo polling... %ss elapsed, done=%s", waited, operation.done)

    if getattr(operation, "error", None):
        raise RuntimeError(f"Veo error: {operation.error}")

    data = _extract_video_bytes(client, operation)
    return {
        "video_b64": base64.b64encode(data).decode("ascii"),
        "mime": "video/mp4",
    }
