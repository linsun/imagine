"""The studio: the keynote's web page, served by an Agent Substrate actor.

The page only runs while someone is looking at it. The actor sits suspended
(a snapshot, no worker) until a browser opens :3007, which `kubectl ate proxy`
forwards through atenet-router; the router wakes it for that request, and
`./imagine k8s studio sleep` puts it back. Because the snapshot keeps process
memory, the server notices the gap in its own clock when it is resumed, and
the page says how long it slept -- the wake is the first thing the audience
sees.

The actor serves only the page. Everything else the page calls is on
agentgateway's :3006 (with CORS), so the actor needs no egress at all:

  :3007 /            this page            (the actor)
  :3007 /api/self    when it woke, how long it slept
  :3006 /director/   the Director, A2A v1 (kagent)
  :3006 /photo/...   the photo inbox      (viewfinder)
  :3006 /stage/...   what is on the projector

  python -m agent.studio            # PORT=80 in the actor; 8090 by default
"""

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8090"))
PAGE = (Path(__file__).parent / "studio.html").read_bytes()
# A gap this long in a 1-second tick means the process was not running:
# suspended (or, the first time, restored from the golden snapshot).
GAP_S = float(os.environ.get("STUDIO_GAP_S", "5"))

_started = time.time()
_wakes: list[dict] = []          # newest last: {"at": epoch, "slept_s": seconds}
_lock = threading.Lock()


def _watch_clock() -> None:
    last = time.time()
    while True:
        time.sleep(1)
        now = time.time()
        if now - last > GAP_S:
            with _lock:
                _wakes.append({"at": now, "slept_s": round(now - last)})
                del _wakes[:-20]
            print(f"studio: woke after {now - last:.0f}s asleep", flush=True)
        last = now


def _self() -> dict:
    now = time.time()
    with _lock:
        wakes = list(_wakes)
    return {"now": now, "started_at": _started, "host": socket.gethostname(),
            "wakes": len(wakes), "last_wake": wakes[-1] if wakes else None}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/self":
            return self._send(200, json.dumps(_self()).encode(), "application/json")
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        return self._send(404, b"not found", "text/plain")


def main() -> None:
    threading.Thread(target=_watch_clock, daemon=True).start()
    server = ThreadingHTTPServer(("", PORT), _Handler)
    print(f"studio: http://0.0.0.0:{PORT}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
