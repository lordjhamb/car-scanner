"""App info, update check, desktop shutdown and personal-install sign-in."""
from __future__ import annotations

import json
import re
import secrets
import time
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import __version__, db
from ..security import hash_password
from .auth import _start_session, public_user
from .deps import current_user, get_db, require_client_header

router = APIRouter(prefix="/api/system", tags=["system"])

LOCAL_EMAIL = "me@this-computer.local"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
UPDATE_TTL = 6 * 3600


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (v or "").split("-")[0])
    return tuple(int(n) for n in nums[:3]) or (0,)


def fetch_latest_release(repo: str) -> dict | None:
    """Latest GitHub release for the repo. Sends no user data; returns None on any failure."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "car-scanner-update-check"})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:  # noqa: S310 - fixed https URL
            data = json.loads(r.read().decode())
        return {"version": data.get("tag_name", "").lstrip("v"), "url": data.get("html_url", ""),
                "name": data.get("name") or data.get("tag_name", "")}
    except Exception:
        return None


def _is_local_request(request: Request) -> bool:
    return bool(request.client) and request.client.host in LOOPBACK


@router.get("/info")
def info(request: Request):
    s = request.app.state.settings
    return {"app": "carscan", "version": __version__, "local_mode": s.local_mode,
            "auto_login": s.local_mode and s.auto_login,
            "can_quit": s.local_mode and getattr(request.app.state, "shutdown", None) is not None,
            "repo": s.github_repo}


@router.get("/update")
def update(request: Request):
    s = request.app.state.settings
    if not s.update_check:
        return {"current": __version__, "available": False, "checked": False}
    cache = getattr(request.app.state, "update_cache", None)
    if not cache or time.time() - cache["at"] > UPDATE_TTL:
        latest = request.app.state.release_fetcher(s.github_repo)
        cache = {"at": time.time(), "latest": latest}
        request.app.state.update_cache = cache
    latest = cache["latest"]
    available = bool(latest and latest["version"] and parse_version(latest["version"]) > parse_version(__version__))
    return {"current": __version__, "available": available, "checked": latest is not None,
            "latest": latest["version"] if latest else None,
            "url": latest["url"] if available else None}


@router.post("/local-login", dependencies=[Depends(require_client_header)])
def local_login(request: Request, response: Response, conn=Depends(get_db)):
    """Personal installs: sign in as this computer's owner without a password."""
    s = request.app.state.settings
    if not (s.local_mode and s.auto_login) or not _is_local_request(request):
        raise HTTPException(403, "Automatic sign-in is only available on this computer.")
    r = conn.execute("SELECT * FROM users WHERE email = ?", (LOCAL_EMAIL,)).fetchone()
    if not r:
        # A random password nobody knows: this account is reachable only through local sign-in.
        conn.execute("INSERT INTO users (email, name, password_hash, created_at) VALUES (?,?,?,?)",
                     (LOCAL_EMAIL, "", hash_password(secrets.token_urlsafe(32)), db.now()))
        conn.commit()
        r = conn.execute("SELECT * FROM users WHERE email = ?", (LOCAL_EMAIL,)).fetchone()
    csrf = _start_session(request, response, conn, r["id"])
    return {"user": public_user(dict(r)), "csrf": csrf}


@router.post("/shutdown")
def shutdown(request: Request, user=Depends(current_user)):
    stop = getattr(request.app.state, "shutdown", None)
    if not request.app.state.settings.local_mode or stop is None or not _is_local_request(request):
        raise HTTPException(403, "Only the desktop app can be stopped from here.")
    adapter = request.app.state.adapter
    if adapter.owner == user["id"]:
        adapter.disconnect(user["id"])
    stop()
    return {"ok": True}
