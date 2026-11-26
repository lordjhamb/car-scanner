"""Request dependencies: DB connection, current user, CSRF, ownership checks."""
from __future__ import annotations

import sqlite3

from fastapi import Depends, HTTPException, Request

from .. import db
from ..security import token_hash

SESSION_COOKIE = "cs_session"
CSRF_HEADER = "X-CSRF-Token"
CLIENT_HEADER = "X-Requested-With"
CLIENT_VALUE = "CarScanner"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def get_db(request: Request):
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def require_client_header(request: Request) -> None:
    """Blocks cross-site form posts: browsers can't add custom headers cross-origin without CORS."""
    if request.method in UNSAFE and request.headers.get(CLIENT_HEADER) != CLIENT_VALUE:
        raise HTTPException(403, "Missing client header.")


def _session(request: Request, conn: sqlite3.Connection):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    r = conn.execute(
        "SELECT s.id AS sid, s.csrf_token, s.expires_at, u.id, u.email, u.name, u.unit_pref, u.created_at "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (token_hash(token),)).fetchone()
    if not r:
        return None
    if r["expires_at"] < db.now():
        conn.execute("DELETE FROM sessions WHERE id = ?", (r["sid"],))
        conn.commit()
        return None
    return dict(r)


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    s = _session(request, conn)
    if not s:
        raise HTTPException(401, "Please sign in.")
    if request.method in UNSAFE:
        require_client_header(request)
        if request.headers.get(CSRF_HEADER) != s["csrf_token"]:
            raise HTTPException(403, "Your session expired. Refresh the page.")
    return s


def owned_car(conn: sqlite3.Connection, user_id: int, car_id: int) -> dict:
    r = conn.execute("SELECT * FROM cars WHERE id = ? AND user_id = ?", (car_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "Car not found.")
    return dict(r)
