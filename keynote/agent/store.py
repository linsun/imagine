"""Handle store: the model never sees image or video bytes.

Tools return handles like img_a1b2c3; the tool layer swaps bytes in and out
around every MCP call. This is why the Director's context stays small enough
to reason well, and why a 10MB video never enters a prompt.
"""

import base64
import os
import uuid

OUT = os.environ.get("OUTPUT_DIR", "./outputs")
os.makedirs(OUT, exist_ok=True)

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4"}


def put(data_b64: str, mime: str = "image/png") -> str:
    kind = "vid" if mime.startswith("video") else "img"
    handle = f"{kind}_{uuid.uuid4().hex[:8]}"
    path = os.path.join(OUT, f"{handle}.{_EXT.get(mime, 'bin')}")
    with open(path, "wb") as f:
        f.write(base64.b64decode(data_b64))
    return handle


def path_of(handle: str) -> str:
    for name in os.listdir(OUT):
        if name.startswith(handle + "."):
            return os.path.join(OUT, name)
    raise KeyError(f"unknown handle {handle}")


def get(handle: str) -> str:
    with open(path_of(handle), "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")
