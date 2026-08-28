"""Publish MCP server (stdio): put the film somewhere the audience can get it.

Uses the GitHub REST API directly rather than the official GitHub MCP server,
because that server CANNOT carry the video: its create_or_update_file `content`
parameter is a JSON string and the server base64-encodes it for you, so raw
MP4 bytes cannot survive the wire. Verified in pkg/github/repositories.go.

Two tools:
  publish_video  -- uploads the MP4 as a RELEASE ASSET. Release assets are
                    deletable; a binary committed to git is effectively forever,
                    which matters when the file is a photo of a real audience.
  open_pr        -- opens a pull request adding a gallery entry that links to it.

The bytes never pass through the model: the agent hands over a handle and this
server reads the file from disk.
"""

import base64
import json
import os
import time

import requests
from fastmcp import FastMCP

mcp = FastMCP("publish")

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
REPO = os.environ.get("GITHUB_REPO", "")
TAG = os.environ.get("GITHUB_RELEASE_TAG", "keynote-demo")
PAGES_URL = os.environ.get("PAGES_URL", "")


def _token() -> str:
    # Strip quotes and whitespace: a token pasted into .env as "ghp_..." or with
    # a trailing space is the single most common cause of a 401 here.
    t = os.environ.get("GITHUB_TOKEN", "").strip().strip('"').strip("'")
    if not t:
        raise RuntimeError(
            "GITHUB_TOKEN is not set in the process agentgateway spawned. "
            "If you added it to .env after `make up`, restart: make down && make up."
        )
    return t


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _explain(r, what: str) -> None:
    """Turn GitHub's terse auth failures into the actual fix."""
    if r.status_code == 401:
        raise RuntimeError(
            f"{what}: 401 Unauthorized — GitHub rejected the token itself. This is "
            f"NOT a permissions problem (that would be 403). Causes, in order of "
            f"likelihood: the token is expired or revoked; it was pasted with "
            f"quotes/whitespace; it is a fine-grained token that was never granted "
            f"access to {_repo()}; or agentgateway was started before GITHUB_TOKEN "
            f"was in .env (fix: make down && make up). "
            f"Test it directly:  curl -H 'Authorization: Bearer $GITHUB_TOKEN' "
            f"https://api.github.com/user"
        )
    if r.status_code == 403:
        raise RuntimeError(
            f"{what}: 403 Forbidden — the token is valid but lacks permission on "
            f"{_repo()}. Fine-grained PAT needs: Contents = Read and write, "
            f"Pull requests = Read and write. Classic PAT needs the `repo` scope. "
            f"Detail: {r.text[:200]}"
        )
    if r.status_code == 404:
        raise RuntimeError(
            f"{what}: 404 — {_repo()} not found, or the token cannot see it. "
            f"Check GITHUB_REPO is owner/repo, and that a fine-grained token has "
            f"this repository selected under 'Repository access'."
        )
    r.raise_for_status()


def _repo() -> str:
    if not REPO or "/" not in REPO:
        raise RuntimeError("GITHUB_REPO must be set as owner/repo.")
    return REPO


def _ensure_release() -> dict:
    """Get the release for TAG, creating it if needed."""
    r = requests.get(f"{API}/repos/{_repo()}/releases/tags/{TAG}", headers=_headers(), timeout=30)
    if r.status_code == 200:
        return r.json()
    if r.status_code in (401, 403):
        _explain(r, "reading releases")
    r = requests.post(
        f"{API}/repos/{_repo()}/releases",
        headers=_headers(),
        json={"tag_name": TAG, "name": TAG, "body": "Keynote demo output.", "draft": False},
        timeout=30,
    )
    _explain(r, "creating the release")
    return r.json()


@mcp.tool
def publish_video(video_b64: str, name: str = "") -> dict:
    """Upload the finished film as a GitHub release asset and return its URL.

    Returns: { url, name, size }
    """
    data = base64.b64decode(video_b64)
    asset = name or f"film-{int(time.time())}.mp4"
    release = _ensure_release()
    upload = release["upload_url"].split("{")[0]

    # Remove a same-named asset if one exists -- uploads are not idempotent.
    for existing in release.get("assets", []):
        if existing.get("name") == asset:
            requests.delete(
                f"{API}/repos/{_repo()}/releases/assets/{existing['id']}",
                headers=_headers(), timeout=30,
            )

    h = _headers() | {"Content-Type": "video/mp4"}
    r = requests.post(f"{upload}?name={asset}", headers=h, data=data, timeout=300)
    r.raise_for_status()
    out = r.json()
    return {
        "url": out.get("browser_download_url", ""),
        "name": asset,
        "size": len(data),
    }


@mcp.tool
def open_pr(video_url: str, title: str = "", caption: str = "") -> dict:
    """Open a pull request adding a gallery entry for the film.

    Text-only change, so it renders as a readable diff on stage.
    Returns: { pr_url, branch, number }
    """
    repo, hdr = _repo(), _headers()
    branch = f"film/{int(time.time())}"

    base = requests.get(f"{API}/repos/{repo}", headers=hdr, timeout=30).json()["default_branch"]
    sha = requests.get(
        f"{API}/repos/{repo}/git/ref/heads/{base}", headers=hdr, timeout=30
    ).json()["object"]["sha"]
    requests.post(
        f"{API}/repos/{repo}/git/refs", headers=hdr,
        json={"ref": f"refs/heads/{branch}", "sha": sha}, timeout=30,
    ).raise_for_status()

    path = "gallery/films.json"
    cur = requests.get(
        f"{API}/repos/{repo}/contents/{path}?ref={base}", headers=hdr, timeout=30
    )
    if cur.status_code == 200:
        body = cur.json()
        films = json.loads(base64.b64decode(body["content"]).decode())
        file_sha = body["sha"]
    else:
        films, file_sha = [], None

    films.insert(0, {
        "url": video_url,
        "caption": caption or title or "Keynote film",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    payload = {
        "message": title or "Add keynote film",
        "content": base64.b64encode(json.dumps(films, indent=2).encode()).decode(),
        "branch": branch,
    }
    if file_sha:
        payload["sha"] = file_sha
    requests.put(
        f"{API}/repos/{repo}/contents/{path}", headers=hdr, json=payload, timeout=60
    ).raise_for_status()

    pr = requests.post(
        f"{API}/repos/{repo}/pulls", headers=hdr,
        json={
            "title": title or "Add keynote film",
            "head": branch,
            "base": base,
            "body": f"Generated live on stage.\n\n{video_url}\n",
        }, timeout=30,
    )
    pr.raise_for_status()
    out = pr.json()
    return {"pr_url": out["html_url"], "branch": branch, "number": out["number"]}


@mcp.tool
def check_auth() -> dict:
    """Is the GitHub token usable, and can it write to this repo?

    Run this before the talk. It separates "token is bad" (401) from "token is
    fine but cannot touch this repo" (403), which need completely different fixes.
    """
    out: dict = {"repo": REPO, "tag": TAG}
    try:
        out["token_present"] = bool(_token())
        out["token_len"] = len(_token())
    except RuntimeError as exc:
        return {**out, "ok": False, "problem": str(exc)}

    u = requests.get(f"{API}/user", headers=_headers(), timeout=20)
    if u.status_code == 401:
        return {**out, "ok": False, "stage": "identity",
                "problem": "401 — the token itself is rejected: expired, revoked, "
                           "or mangled when pasted. Regenerate it."}
    if u.status_code != 200:
        return {**out, "ok": False, "stage": "identity", "problem": u.text[:200]}
    out["login"] = u.json().get("login")
    # Classic PATs report scopes in a header; fine-grained ones do not.
    out["scopes"] = u.headers.get("x-oauth-scopes", "(fine-grained token)")

    r = requests.get(f"{API}/repos/{_repo()}", headers=_headers(), timeout=20)
    if r.status_code != 200:
        return {**out, "ok": False, "stage": "repo",
                "problem": f"{r.status_code} on {REPO} — "
                           f"{'not visible to this token' if r.status_code == 404 else r.text[:150]}"}
    perms = r.json().get("permissions", {})
    out["permissions"] = perms
    out["ok"] = bool(perms.get("push"))
    if not out["ok"]:
        out["problem"] = ("token can read but not write. Fine-grained PAT needs "
                          "Contents = Read and write, Pull requests = Read and write.")
    return out


@mcp.tool
def gallery_url() -> dict:
    """The stable URL the QR code points at. Predictable before anything exists."""
    return {"url": PAGES_URL or f"https://github.com/{_repo()}/releases/tag/{TAG}"}


if __name__ == "__main__":
    mcp.run()
