"""Stage MCP server (stdio): the projector and the voice.

Three jobs the browser used to do, now agent tools:
  show()     -- put something on the big screen
  open_url() -- put a page on the big screen (the live viewfinder)
  announce() -- say out loud that the film is ready

announce() uses macOS `say`, which is built in and works fully OFFLINE.
That matters: the announcement is the one moment you said you don't want
to babysit, so it must not depend on conference wifi.

Check your voices before the day:   say -v '?' | grep -i en_
Optional pre-recorded audio:        ANNOUNCE_SOUND=/path/to/fanfare.mp3
"""

import os
import shutil
import subprocess
import tempfile

from fastmcp import FastMCP

mcp = FastMCP("stage")

VOICE_EN = os.environ.get("VOICE_EN", "Samantha")
VOICE_JA = os.environ.get("VOICE_JA", "Kyoko")
ANNOUNCE_SOUND = os.environ.get("ANNOUNCE_SOUND", "")
OPEN_CMD = os.environ.get("OPEN_CMD", "open")
# The film ends on the QR card. Holding there means the code stays on the
# projector while the room gets their phones out, instead of the window
# vanishing the moment playback ends. Press q (or esc) to close it.
# SHOW_HOLD=0 goes back to closing itself.
SHOW_HOLD = os.environ.get("SHOW_HOLD", "1") not in ("", "0", "false", "no")


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


@mcp.tool
def announce(en: str, ja: str = "") -> dict:
    """Announce something out loud in the room. Call this when the video is ready.

    Args:
        en: What to say in English. Required.
        ja: Optional Japanese. Skipped silently if no Japanese voice is installed.

    Plays ANNOUNCE_SOUND first if set. Never raises -- a failed announcement
    must not break the demo.
    """
    played = []
    try:
        if ANNOUNCE_SOUND and os.path.exists(ANNOUNCE_SOUND) and _have("afplay"):
            subprocess.run(["afplay", ANNOUNCE_SOUND], timeout=20, check=False)
            played.append("sound")
        if not _have("say"):
            return {"ok": False, "note": "`say` not available on this host", "played": played}
        if ja:
            voices = subprocess.run(
                ["say", "-v", "?"], capture_output=True, text=True, check=False
            ).stdout
            if VOICE_JA in voices:
                subprocess.run(["say", "-v", VOICE_JA, ja], timeout=60, check=False)
                played.append("ja")
        subprocess.run(["say", "-v", VOICE_EN, en], timeout=60, check=False)
        played.append("en")
        return {"ok": True, "played": played}
    except Exception as exc:  # noqa: BLE001 -- never break the show
        return {"ok": False, "note": str(exc), "played": played}


@mcp.tool
def open_url(url: str, app: str = "") -> dict:
    """Open a URL on this machine's screen.

    Used for the live viewfinder. `app` names a specific browser (macOS), e.g.
    "Safari" -- which matters for the camera: Chrome cannot drive Continuity
    Camera (the iPhone), Safari can, so the preview opens there when asked.

    Never raises -- if the browser cannot be opened you still have the URL.
    """
    if not url.startswith(("http://", "https://")):
        raise RuntimeError("open_url() takes an http(s) URL")
    try:
        if app and _have(OPEN_CMD):
            subprocess.Popen([OPEN_CMD, "-a", app, url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "url": url, "opened_with": f"{OPEN_CMD} -a {app}"}
        if _have(OPEN_CMD):
            subprocess.Popen([OPEN_CMD, url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "url": url, "opened_with": OPEN_CMD}
        import webbrowser
        return {"ok": bool(webbrowser.open(url)), "url": url,
                "opened_with": "webbrowser"}
    except Exception as exc:  # noqa: BLE001 -- never break the show
        return {"ok": False, "url": url, "note": str(exc)}


@mcp.tool
def show(image_b64: str = "", video_b64: str = "", caption: str = "",
         fullscreen: bool = True) -> dict:
    """Put an image or a video on the screen. Video PLAYS, with sound.

    Video uses ffplay (ships with ffmpeg) so it starts immediately, fullscreen,
    with audio -- `open` would just park it in QuickTime waiting for a click,
    which is not what you want mid-keynote. Falls back to `open` if ffplay is
    missing. Returns as soon as playback starts; it does not block the agent.

    With SHOW_HOLD on (the default) the window stays on the last frame -- the
    QR card -- until you press q or esc, rather than closing on its own.
    """
    import base64 as _b64

    if not image_b64 and not video_b64:
        raise RuntimeError("show() needs image_b64 or video_b64")
    is_video = bool(video_b64)
    data = _b64.b64decode(video_b64 or image_b64)
    suffix = ".mp4" if is_video else ".png"
    fd, path = tempfile.mkstemp(suffix=suffix, dir=os.environ.get("SHOW_DIR", None))
    with os.fdopen(fd, "wb") as f:
        f.write(data)

    played_with = "none"
    if is_video and _have("ffplay"):
        # -autoexit closes the window at EOF, taking the QR code with it.
        # Without it ffplay parks on the final frame until a key is pressed.
        cmd = ["ffplay", "-loglevel", "quiet"]
        if not SHOW_HOLD:
            cmd.append("-autoexit")
        if fullscreen:
            cmd.append("-fs")
        cmd.append(path)
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        played_with = "ffplay"
    elif _have(OPEN_CMD):
        subprocess.run([OPEN_CMD, path], check=False)
        played_with = OPEN_CMD

    return {"ok": True, "path": path, "caption": caption,
            "kind": "video" if is_video else "image", "played_with": played_with,
            "holds": bool(is_video and played_with == "ffplay" and SHOW_HOLD)}


if __name__ == "__main__":
    mcp.run()
