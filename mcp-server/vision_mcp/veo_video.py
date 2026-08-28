"""Veo image-to-video generation wrapper.

Veo generation is a long-running operation: we submit the job and poll until it
completes, then return the resulting MP4 as base64. Uses the fast model by
default to keep latency to ~1-2 minutes.
"""

import base64
import logging
import os
import shutil
import subprocess
import tempfile
import time

from google.genai import types

from vision_mcp.genai_client import get_video_client

logger = logging.getLogger("vision_mcp.veo")
logging.basicConfig(level=logging.INFO)

DEFAULT_VIDEO_MODEL = os.environ.get("VIDEO_MODEL", "veo-3.1-lite-generate-preview")
# Shorter clips generate noticeably faster. Veo 3.1 supports 4/6/8 seconds.
VIDEO_DURATION_SECONDS = int(os.environ.get("VIDEO_DURATION", "6"))
# Hard cap on how long we wait before giving up (seconds).
MAX_WAIT_SECONDS = int(os.environ.get("VIDEO_MAX_WAIT", "900"))
POLL_INTERVAL_SECONDS = int(os.environ.get("VIDEO_POLL_INTERVAL", "10"))
# How many Veo clips to chain. 1 = a single clip (max 8s). 2 = the first clip is
# EXTENDED by Veo, which continues from where it ended and keeps the motion and
# the music running, rather than cutting to an unrelated second shot.
# Each extra clip costs another full generation, so 2 roughly doubles the wait.
VIDEO_CLIPS = int(os.environ.get("VIDEO_CLIPS", "1"))


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


def _await(client, operation, label: str):
    """Poll one Veo job to completion."""
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
        logger.info("Veo %s: %ss elapsed, done=%s", label, waited, operation.done)
    if getattr(operation, "error", None):
        raise RuntimeError(f"Veo error on {label}: {operation.error}")
    return operation


def animate_image(image_b64: str, prompt: str = "", model: str | None = None) -> dict:
    """Animate a still image into a short video clip.

    Args:
        image_b64: Source image as base64 (no data: prefix).
        prompt: Motion/scene description. Defaults to gentle natural motion.
        model: Optional Veo model override.

    Returns: { video_b64, mime }
    """
    client = get_video_client()
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

    operation = _await(client, operation, "clip 1")

    # --- chain more clips, so the film is not mostly credits -----------------
    # Veo's NATIVE extension (generate_videos(video=...)) is Vertex-only: on the
    # Gemini Developer API it fails with "video parameter is not supported in
    # Gemini API", exactly like generate_audio. So we chain manually: seed each
    # new clip with the LAST FRAME of the previous one, then cross-fade the
    # joins. Continuity is good because the model starts from where we stopped.
    clips = max(1, VIDEO_CLIPS)
    parts = [_extract_video_bytes(client, operation)]
    for n in range(2, clips + 1):
        logger.info("Generating clip %s of %s (seeded from the last frame)...", n, clips)
        try:
            seed = types.Image(image_bytes=_last_frame(parts[-1]), mime_type="image/png")
            nxt = client.models.generate_videos(
                model=video_model,
                prompt=_augment_audio(
                    f"Continue the same scene, unbroken. {motion_prompt}"),
                image=seed,
                config=types.GenerateVideosConfig(
                    number_of_videos=1,
                    duration_seconds=VIDEO_DURATION_SECONDS,
                ),
            )
            parts.append(_extract_video_bytes(client, _await(client, nxt, f"clip {n}")))
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


