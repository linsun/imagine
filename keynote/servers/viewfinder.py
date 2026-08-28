"""The viewfinder: a standalone, long-lived process that owns the camera.

WHY THIS IS NOT AN MCP TOOL
---------------------------
agentgateway spawns stdio MCP servers per session, and the Director opens a new
MCP session per tool call. So anything a tool starts -- a camera handle, a
background thread, an HTTP server -- dies the moment that call returns. A
viewfinder cannot live inside a tool call.

So the camera lives here instead, started by up.sh and running for the whole
demo. camera_mcp stays completely stateless and just talks to this over HTTP,
which means it does not care how often it gets respawned.

  GET /                       full-bleed viewfinder page (put this on the projector)
  GET /stream.mjpg            live MJPEG
  GET /frame.jpg?countdown=3  show 3-2-1 on the stream, then return that frame
  GET /release                let go of the camera (green light OFF), keep serving
  GET /resume                 pick it up again
  GET /healthz                {"ok":true,"has_frame":true,"paused":false,...}

  python -m servers.viewfinder
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2

PORT = int(os.environ.get("PREVIEW_PORT", "8888"))
INDEX = int(os.environ.get("CAMERA_INDEX", "0"))
WIDTH = int(os.environ.get("CAMERA_WIDTH", "1920"))
HEIGHT = int(os.environ.get("CAMERA_HEIGHT", "1080"))

_lock = threading.Lock()
_frame = None
_countdown_until = 0.0
_err = ""
# Released, not stopped: the process stays up so the page keeps working and
# you can pick the camera back up without restarting anything.
_paused = False


def _off_frame(width: int = 1280, height: int = 720):
    import numpy as np
    f = np.zeros((height, width, 3), dtype="uint8")
    txt = "CAMERA OFF"
    scale, thick = height / 400.0, max(2, height // 240)
    (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    cv2.putText(f, txt, ((width - tw) // 2, (height + th) // 2),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (70, 70, 70), thick, cv2.LINE_AA)
    return f

_PAGE = b"""<!doctype html><html><head><title>Viewfinder</title>
<style>html,body{margin:0;height:100%;background:#000;overflow:hidden}
img{width:100%;height:100%;object-fit:contain;display:block}</style></head>
<body><img src="/stream.mjpg"></body></html>"""


def _grab() -> None:
    """Hold the camera open forever, keeping the latest frame warm.

    Retries on failure so unplugging and replugging a webcam recovers instead of
    ending the demo.
    """
    global _frame, _err
    while True:
        if _paused:
            time.sleep(0.2)
            continue
        cap = cv2.VideoCapture(INDEX)
        if not cap.isOpened():
            _err = (f"cannot open camera {INDEX}. On macOS grant Camera permission "
                    f"to the terminal that started this, then `make down && make up`.")
            print(f"viewfinder: {_err}", flush=True)
            time.sleep(3)
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        _err = ""
        print(f"viewfinder: camera {INDEX} open, serving http://localhost:{PORT}/", flush=True)
        misses = 0
        while misses < 30 and not _paused:
            ok, f = cap.read()
            if ok:
                misses = 0
                with _lock:
                    _frame = f
            else:
                misses += 1
                time.sleep(0.05)
        cap.release()
        if _paused:
            with _lock:
                _frame = _off_frame(WIDTH, HEIGHT)
            print("viewfinder: camera released (green light off)", flush=True)
            continue
        print("viewfinder: lost the camera, reopening", flush=True)
        time.sleep(1)


def _annotate(frame):
    remaining = _countdown_until - time.time()
    if remaining <= 0:
        return frame
    f = frame.copy()
    n = str(int(remaining) + 1)
    h, w = f.shape[:2]
    scale, thick = h / 90.0, max(6, int(h / 90))
    (tw, th), _ = cv2.getTextSize(n, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    org = ((w - tw) // 2, (h + th) // 2)
    cv2.putText(f, n, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 8, cv2.LINE_AA)
    cv2.putText(f, n, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return f


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global _countdown_until
        u = urlparse(self.path)

        global _paused
        if u.path == "/release":
            _paused = True
            return self._json({"ok": True, "paused": True})

        if u.path == "/resume":
            _paused = False
            return self._json({"ok": True, "paused": False})

        if u.path == "/healthz":
            with _lock:
                has = _frame is not None
            return self._json({"ok": has and not _paused, "has_frame": has,
                               "paused": _paused, "error": _err,
                               "camera_index": INDEX, "port": PORT})

        if u.path == "/frame.jpg":
            q = parse_qs(u.query)
            cd = int((q.get("countdown") or ["0"])[0])
            if cd > 0:
                _countdown_until = time.time() + cd
                time.sleep(cd + 0.15)
                _countdown_until = 0.0
            with _lock:
                f = None if _frame is None else _frame.copy()
            if f is None:
                return self._json({"error": _err or "no frame yet"}, 503)
            ok, buf = cv2.imencode(".jpg", f, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if not ok:
                return self._json({"error": "encode failed"}, 500)
            data = buf.tobytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if u.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    with _lock:
                        f = None if _frame is None else _frame
                    if f is None:
                        time.sleep(0.05)
                        continue
                    ok, buf = cv2.imencode(".jpg", _annotate(f),
                                           [int(cv2.IMWRITE_JPEG_QUALITY), 72])
                    if ok:
                        b = buf.tobytes()
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: " + str(len(b)).encode()
                                         + b"\r\n\r\n" + b + b"\r\n")
                    time.sleep(1 / 25)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)


def main() -> None:
    threading.Thread(target=_grab, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    print(f"viewfinder: listening on http://localhost:{PORT}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
