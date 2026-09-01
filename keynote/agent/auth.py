"""Browser login to Keycloak, so the Director carries a real user's identity.

The point of the beat: `vision_generate_video` is the expensive call, and
agentgateway refuses it unless the caller presents a token it can validate.
You log in as yourself, on stage, and the same command then works.

Authorization Code + PKCE, which is what a public client is supposed to use --
no client secret exists to leak. The token is cached in .token.json and read
fresh on every MCP request, so logging in DURING a session takes effect on the
next tool call without restarting anything.

    ./imagine login     open Keycloak, log in, cache the token
    ./imagine logout    forget it
    ./imagine auth on   turn the gateway policy on (checks Keycloak first)

Redirect URI: http://localhost:6274/oauth/callback. That is MCP Inspector's
callback, and it is ALREADY registered on the `mcp-client` in the my-realm
seed -- so this needs no Keycloak change at all. If Inspector happens to be
running it owns that port; stop it first.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import socket
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

KEYCLOAK = os.environ.get("KEYCLOAK_URL", "http://localhost:8080").rstrip("/")
REALM = os.environ.get("KEYCLOAK_REALM", "my-realm")
CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "mcp-client")
REDIRECT = os.environ.get("KEYCLOAK_REDIRECT", "http://localhost:6274/oauth/callback")
# `openid` for the ID token, `mcp:tools` because that client scope carries the
# audience mapper -- without it the token has no aud the gateway accepts.
SCOPES = os.environ.get("KEYCLOAK_SCOPES", "openid mcp:tools")

ISSUER = f"{KEYCLOAK}/realms/{REALM}"
AUTHZ = f"{ISSUER}/protocol/openid-connect/auth"
TOKEN = f"{ISSUER}/protocol/openid-connect/token"

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("TOKEN_CACHE", os.path.join(_HERE, ".token.json"))

_PAGE = """<!doctype html><meta charset=utf-8><title>Signed in</title>
<style>body{font:16px -apple-system,sans-serif;display:grid;place-items:center;
height:100vh;margin:0;background:#0b0c0e;color:#e8e8ea}
div{text-align:center}b{color:#f5b642}</style>
<div><p>Signed in as <b>%s</b>.</p><p>You can close this tab.</p></div>"""


# --- token cache -----------------------------------------------------------

_cached: dict = {}
_cached_mtime: float = -1.0


def load() -> dict:
    """Read the cached token, re-reading only when the file actually changed.

    Called on every MCP request, so it must be cheap; an mtime check is.
    """
    global _cached, _cached_mtime
    try:
        mtime = os.path.getmtime(CACHE)
    except OSError:
        _cached, _cached_mtime = {}, -1.0
        return {}
    if mtime != _cached_mtime:
        try:
            with open(CACHE, encoding="utf-8") as f:
                _cached = json.load(f)
            _cached_mtime = mtime
        except (OSError, ValueError):
            _cached, _cached_mtime = {}, -1.0
    return _cached


def token() -> str:
    """The access token, or "" if there is none or it has expired."""
    t = load()
    if not t.get("access_token"):
        return ""
    if t.get("expires_at", 0) <= time.time() + 5:
        return ""
    return t["access_token"]


def status() -> dict:
    t = load()
    if not t.get("access_token"):
        return {"logged_in": False}
    left = int(t.get("expires_at", 0) - time.time())
    return {"logged_in": left > 0, "user": t.get("user", "?"),
            "seconds_left": max(0, left), "issuer": t.get("issuer", ISSUER)}


def logout() -> bool:
    global _cached, _cached_mtime
    _cached, _cached_mtime = {}, -1.0
    try:
        os.remove(CACHE)
        return True
    except OSError:
        return False


# --- the httpx hook the MCP transport uses ---------------------------------

async def attach_bearer(request) -> None:
    """Put the cached token on every MCP request. No token: send nothing.

    Read per request, not per session, so `./imagine login` takes effect on the
    next tool call rather than needing a restart mid-demo.
    """
    if "authorization" in request.headers:
        return
    t = token()
    if t:
        request.headers["authorization"] = f"Bearer {t}"


# --- the flow --------------------------------------------------------------

def _claims(access_token: str) -> dict:
    """Decode the JWT payload. Display only -- the GATEWAY verifies it."""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


class _Handler(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Handler.result = {k: v[0] for k, v in q.items()}
        who = _Handler.result.get("_user", "")
        body = (_PAGE % (who or "you")).encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):  # silence the default stderr logging
        return


class _DualStack(http.server.HTTPServer):
    """Listen on BOTH 127.0.0.1 and ::1.

    The redirect URI is http://localhost:6274/... and on macOS `localhost`
    resolves to ::1 (IPv6) first. An IPv4-only listener never sees that
    callback -- which looks exactly like "no callback within 180s" after a
    successful browser login. Binding :: with V6ONLY off accepts both.
    """
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


def _print_token(access_token: str, claims: dict) -> None:
    """Print just the raw JWT, between rules so it is easy to select and copy.
    Set AGW_PRINT_TOKEN=0 to silence it."""
    bar = "-" * 68
    print(f"\n  \033[2m{bar}\033[0m", flush=True)
    print(access_token, flush=True)            # raw, unindented: easy to copy
    print(f"  \033[2m{bar}\033[0m\n", flush=True)


def _open_browser(url: str) -> None:
    """Open the URL without the child browser's stderr leaking into our
    terminal. Chrome/Firefox spawned by the default opener print gRPC noise
    like `I0901 ... FD from fork parent still in poll list` on the inherited
    stderr; sending the opener's output to /dev/null keeps the token output
    clean. Falls back to webbrowser if `open`/`xdg-open` is missing.
    """
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return
    except (OSError, ValueError):
        pass
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def login(timeout: float = 180.0) -> dict:
    """Open Keycloak in the browser and cache the resulting token.

    Returns {ok, user, expires_in} or {ok: False, error}.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)

    parsed = urllib.parse.urlparse(REDIRECT)
    port = parsed.port or 80
    try:
        server = _DualStack(("::", port), _Handler)   # dual-stack: v4 and v6
    except OSError as exc:
        # Fall back to IPv4-only if the host has no IPv6 at all.
        try:
            server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
        except OSError:
            return {"ok": False, "error": (
                f"cannot listen on :{port} ({exc}). That is the redirect URI "
                f"registered for `{CLIENT_ID}`; if MCP Inspector is running on "
                f"{port}, stop it.")}

    _Handler.result = {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fam = "dual-stack v4+v6" if server.address_family == socket.AF_INET6 else "v4 only"
    print(f"  \033[2mlistening for the callback on {REDIRECT} ({fam})\033[0m",
          flush=True)

    url = AUTHZ + "?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    print(f"  opening Keycloak — log in as your realm user\n  \033[2m{url[:80]}…\033[0m",
          flush=True)
    _open_browser(url)

    deadline = time.time() + timeout
    while not _Handler.result and time.time() < deadline:
        time.sleep(0.2)
    server.shutdown()

    got = _Handler.result
    if not got:
        return {"ok": False, "error": f"no callback within {int(timeout)}s"}
    if "error" in got:
        return {"ok": False, "error": f"{got['error']}: {got.get('error_description', '')}"}
    if got.get("state") != state:
        return {"ok": False, "error": "state mismatch — discarding this response"}
    if "code" not in got:
        return {"ok": False, "error": f"no code in callback: {got}"}

    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": got["code"],
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
    }).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(
                    TOKEN, data=data,
                    headers={"content-type": "application/x-www-form-urlencoded"}),
                timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"token endpoint {exc.code}: "
                                      f"{exc.read().decode('utf-8', 'replace')[:300]}"}
    except OSError as exc:
        return {"ok": False, "error": f"token endpoint unreachable: {exc}"}

    claims = _claims(tok.get("access_token", ""))
    record = {
        "access_token": tok.get("access_token", ""),
        "expires_at": time.time() + int(tok.get("expires_in", 300)),
        "user": claims.get("preferred_username") or claims.get("sub", "?"),
        "issuer": claims.get("iss", ISSUER),
        "aud": claims.get("aud"),
    }
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.chmod(CACHE, 0o600)

    if os.environ.get("AGW_PRINT_TOKEN", "1") not in ("", "0", "false", "no"):
        _print_token(tok.get("access_token", ""), claims)

    global _cached_mtime
    _cached_mtime = -1.0          # force the next load() to re-read

    return {"ok": True, "user": record["user"],
            "expires_in": int(tok.get("expires_in", 300)),
            "aud": record["aud"]}
