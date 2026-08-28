"""Camera MCP server (stdio). Deliberately STATELESS.

agentgateway spawns stdio MCP servers per session, and the Director opens a new
session per tool call -- so this process may be created and destroyed around
every single call. Nothing here may hold state.

The camera therefore lives in servers/viewfinder.py, a long-lived process
started by up.sh. These tools are thin HTTP calls to it, which means they work
no matter how often this process is respawned.
"""

import base64
import glob
import os

import requests
from fastmcp import FastMCP

mcp = FastMCP("camera")

VIEWFINDER = f"http://localhost:{os.environ.get('PREVIEW_PORT', '8888')}"
FALLBACK_DIR = os.environ.get("FALLBACK_IMAGES", "./fallback")

_DOWN = (
    "The viewfinder is not running. It is a separate long-lived process, because "
    "a camera cannot survive inside an MCP tool call. Start everything with "
    "`make up`, or run `python -m servers.viewfinder` directly. "
    "Meanwhile list_images() and load_image() still work."
)


@mcp.tool
def preview_url() -> dict:
    """Where the live viewfinder is. Put this on the projector before shooting.

    Returns: { url, ready, error }
    """
    try:
        h = requests.get(f"{VIEWFINDER}/healthz", timeout=5).json()
    except Exception:  # noqa: BLE001
        raise RuntimeError(_DOWN)
    return {"url": f"{VIEWFINDER}/", "ready": bool(h.get("has_frame")),
            "error": h.get("error", ""), "camera_index": h.get("camera_index")}


@mcp.tool
def capture(countdown: int = 0) -> dict:
    """Take the photo. Returns exactly the frame showing in the viewfinder.

    Args:
        countdown: Seconds of 3-2-1 drawn on the live preview first. Use 3 for a
                   crowd, so people have time to look up.

    Returns: { image_b64, mime }
    """
    try:
        r = requests.get(f"{VIEWFINDER}/frame.jpg",
                         params={"countdown": max(0, countdown)},
                         timeout=15 + countdown)
    except Exception:  # noqa: BLE001
        raise RuntimeError(_DOWN)
    if r.status_code != 200:
        raise RuntimeError(f"viewfinder: {r.text[:200]}")
    return {"image_b64": base64.b64encode(r.content).decode("ascii"),
            "mime": "image/jpeg"}


@mcp.tool
def release() -> dict:
    """Let go of the camera. The green light goes out and the preview shows
    CAMERA OFF, but nothing is torn down -- resume() picks it straight back up.

    Worth doing once the photo is taken: you told the room you were
    photographing them, so stop when you said you would.
    """
    try:
        return requests.get(f"{VIEWFINDER}/release", timeout=10).json()
    except Exception:  # noqa: BLE001
        raise RuntimeError(_DOWN)


@mcp.tool
def resume() -> dict:
    """Pick the camera back up after release()."""
    try:
        return requests.get(f"{VIEWFINDER}/resume", timeout=10).json()
    except Exception:  # noqa: BLE001
        raise RuntimeError(_DOWN)


def _resolve(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path.strip())))


@mcp.tool
def list_images(directory: str = "") -> list[dict]:
    """List photos in a folder. Defaults to ./fallback (the stage safety net),
    but takes any path -- ~/Desktop, an SD card, wherever your test shots are.
    """
    d = _resolve(directory) if directory else FALLBACK_DIR
    out = []
    for ext in ("jpg", "jpeg", "png", "webp"):
        for p in sorted(glob.glob(os.path.join(d, f"*.{ext}"))):
            out.append({"name": os.path.basename(p), "path": p})
    return out


@mcp.tool
def load_image(path: str) -> dict:
    """Use an existing photo instead of the camera.

    Takes any path -- "~/Desktop/room.jpg", "./fallback/kcd.png", an absolute
    path. This is how you test with a proper picture without a room in front
    of you, and it is the fallback if the camera fails on stage.

    Returns: { image_b64, mime, path, width, height }
    """
    full = _resolve(path)
    if not os.path.exists(full):
        raise RuntimeError(
            f"No such file: {full}. Try list_images('<folder>') to see what is there.")
    if os.path.isdir(full):
        raise RuntimeError(f"{full} is a folder. Use list_images() on it first.")
    with open(full, "rb") as f:
        data = f.read()
    ext = os.path.splitext(full)[1].lower()
    mime = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    w = h = 0
    try:
        from PIL import Image
        with Image.open(full) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001
        pass
    return {"image_b64": base64.b64encode(data).decode("ascii"), "mime": mime,
            "path": full, "width": w, "height": h}


if __name__ == "__main__":
    mcp.run()
