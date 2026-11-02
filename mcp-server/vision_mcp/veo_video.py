"""Image-to-video generation with Gemini Omni Flash.

Google retired the Veo models on the Gemini Developer API; the replacement,
gemini-omni-1.1-flash, is served by the Interactions API
(POST /v1beta/interactions), not generate_videos. The pinned google-genai SDK
predates `client.interactions`, so this calls the REST endpoint directly. The
call is synchronous: one request returns the MP4 as base64.
"""

import base64
import logging
import os
import shutil
import subprocess
import tempfile

import httpx

from vision_mcp.genai_client import _api_key, _virtual_key_headers

logger = logging.getLogger("vision_mcp.veo")
logging.basicConfig(level=logging.INFO)

DEFAULT_VIDEO_MODEL = os.environ.get("VIDEO_MODEL", "gemini-omni-1.1-flash")
# Omni has no duration parameter (it picks 3-10s), so this is a prompt hint.
VIDEO_DURATION_SECONDS = int(os.environ.get("VIDEO_DURATION", "6"))
# Hard cap on how long one generation request may take (seconds).
MAX_WAIT_SECONDS = int(os.environ.get("VIDEO_MAX_WAIT", "900"))
# "360p", "720p" (default), "1080p" or "4k" -- the last two are upscaled.
VIDEO_RESOLUTION = os.environ.get("VIDEO_RESOLUTION", "720p")
# Direct to Google by default; set VEO_BASE_URL to send it via the gateway.
_API_BASE = "https://generativelanguage.googleapis.com"
# How many clips to chain. 1 = a single clip. 2 = a second clip seeded from the
# last frame of the first, cross-faded, so the motion and the music keep
# running rather than cutting to an unrelated second shot.
# Each extra clip costs another full generation, so 2 roughly doubles the wait.
VIDEO_CLIPS = int(os.environ.get("VIDEO_CLIPS", "1"))


def _generate(model: str, image_png: bytes, prompt: str) -> bytes:
    """One image-to-video Interactions call; returns the MP4 bytes."""
    base = os.environ.get("VEO_BASE_URL", "").rstrip("/")
    headers = {"x-goog-api-key": _api_key(base), **(_virtual_key_headers() if base else {})}
    body = {
        "model": model,
        "input": [
            {"type": "image", "mime_type": "image/png",
             "data": base64.b64encode(image_png).decode("ascii")},
            {"type": "text", "text": prompt},
        ],
        "generation_config": {"video_config": {"task": "image_to_video"}},
        "response_format": {"type": "video", "aspect_ratio": "16:9", "resolution": VIDEO_RESOLUTION},
        # Synchronous unary generation: the fast path per Google's docs.
        "background": False,
        "store": False,
        "stream": False,
    }
    r = httpx.post(f"{base or _API_BASE}/v1beta/interactions", json=body,
                   headers=headers, timeout=MAX_WAIT_SECONDS)
    if r.status_code >= 400:
        raise RuntimeError(f"{model} {r.status_code}: {r.text[:500]}")
    out = r.json()
    for step in out.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for item in step.get("content") or []:
            if item.get("type") == "video" and item.get("data"):
                return base64.b64decode(item["data"])
    raise RuntimeError(f"{model} returned no video (status={out.get('status')}).")


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
# drops the music. The original directive asked to KEEP the conversation and
# layer music under it -- which told Veo to invent speech, and what it chose to
# speak was the prompt text itself ("make them dance and add some japanese
# vibe", read aloud). For a film shown to the room the people in it should not
# talk at all: music and ambience only.
_MUSIC_AUDIO_DIRECTIVE = (
    "Audio: instrumental music and natural ambience only, playing continuously "
    "for the whole clip. Absolutely NO speech: no dialogue, no narration, no "
    "voice-over, no singing, no lyrics, and nobody reads anything aloud. "
    "Crowd sounds such as laughter, clapping and cheering are welcome, but no "
    "intelligible words."
)


def _augment_audio(prompt: str) -> str:
    """Append an explicit music audio directive when the user asks for music."""
    lowered = prompt.lower()
    if any(kw in lowered for kw in _MUSIC_KEYWORDS) and "audio:" not in lowered:
        return f"{prompt}\n\n{_MUSIC_AUDIO_DIRECTIVE}"
    return prompt


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found (needed to join clips): brew install ffmpeg")
    return exe


def _last_frame(mp4: bytes) -> bytes:
    """The final frame of a clip, as PNG -- the seed for the next one."""
    tmp = tempfile.mkdtemp(prefix="veo-")
    src, png = os.path.join(tmp, "c.mp4"), os.path.join(tmp, "last.png")
    with open(src, "wb") as f:
        f.write(mp4)
    subprocess.run([_ffmpeg(), "-y", "-sseof", "-0.2", "-i", src, "-frames:v", "1",
                    "-q:v", "2", png], check=True, capture_output=True)
    with open(png, "rb") as f:
        return f.read()


def _join(clips: list[bytes], fade: float = 0.4) -> bytes:
    """Concatenate clips with a short cross-fade in picture and sound.

    A hard cut between two independently generated clips is jarring -- the music
    restarts mid-film. A 0.4s cross-fade hides the seam well enough that most
    people read it as one continuous shot.
    """
    if len(clips) == 1:
        return clips[0]
    ff = _ffmpeg()
    tmp = tempfile.mkdtemp(prefix="veo-join-")
    paths = []
    for i, data in enumerate(clips):
        pth = os.path.join(tmp, f"c{i}.mp4")
        with open(pth, "wb") as f:
            f.write(data)
        paths.append(pth)

    probe = shutil.which("ffprobe") or "ffprobe"

    def _dims(path):
        out = subprocess.run(
            [probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True).stdout.strip().split(",")
        num, _, den = out[2].partition("/")
        return int(out[0]), int(out[1]), max(1, round(float(num) / float(den or 1)))

    def _dur(path):
        return float(subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of",
             "csv=p=0", path], capture_output=True, text=True, check=True).stdout.strip())

    w, h, fps = _dims(paths[0])
    cur = paths[0]
    for i, nxt in enumerate(paths[1:], start=1):
        out = os.path.join(tmp, f"j{i}.mp4")
        off = max(0.0, _dur(cur) - fade)
        subprocess.run([
            ff, "-y", "-i", cur, "-i", nxt, "-filter_complex",
            f"[0:v]scale={w}:{h},setsar=1,fps={fps}[v0];"
            f"[1:v]scale={w}:{h},setsar=1,fps={fps}[v1];"
            f"[v0][v1]xfade=transition=fade:duration={fade}:offset={off}[v];"
            f"[0:a][1:a]acrossfade=d={fade}:c1=tri:c2=tri[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", out],
            check=True, capture_output=True)
        cur = out
    with open(cur, "rb") as f:
        return f.read()


def animate_image(image_b64: str, prompt: str = "", model: str | None = None) -> dict:
    """Animate a still image into a short video clip.

    Args:
        image_b64: Source image as base64 (no data: prefix).
        prompt: Motion/scene description. Defaults to gentle natural motion.
        model: Optional video model override.

    Returns: { video_b64, mime }
    """
    raw = base64.b64decode(image_b64)

    motion_prompt = prompt.strip() or "Bring this image to life with gentle, natural motion."
    motion_prompt = _augment_audio(motion_prompt)
    motion_prompt += f"\n\nLength: about {VIDEO_DURATION_SECONDS} seconds."
    logger.info("Video prompt: %s", motion_prompt)

    video_model = model or DEFAULT_VIDEO_MODEL
    logger.info("Generating video (model=%s, ~%ss)...", video_model, VIDEO_DURATION_SECONDS)
    parts = [_generate(video_model, raw, motion_prompt)]

    # --- chain more clips, so the film is not mostly credits -----------------
    # Seed each new clip with the LAST FRAME of the previous one, then
    # cross-fade the joins. Continuity is good because the model starts from
    # where we stopped.
    clips = max(1, VIDEO_CLIPS)
    for n in range(2, clips + 1):
        logger.info("Generating clip %s of %s (seeded from the last frame)...", n, clips)
        try:
            parts.append(_generate(
                video_model, _last_frame(parts[-1]),
                _augment_audio(f"Continue the same scene, unbroken. {motion_prompt}")))
        except Exception as exc:  # noqa: BLE001
            # Never lose the footage we already have -- on stage a shorter film
            # beats no film.
            logger.warning("Clip %s failed (%s); keeping %s clip(s).", n, exc, len(parts))
            break

    data = _join(parts) if len(parts) > 1 else parts[0]
    logger.info("Film assembled from %s clip(s).", len(parts))
    return {
        "video_b64": base64.b64encode(data).decode("ascii"),
        "mime": "video/mp4",
        "clips": len(parts),
    }


