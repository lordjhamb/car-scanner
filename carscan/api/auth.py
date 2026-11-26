"""Accounts and sessions."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import db
from ..security import DUMMY_HASH, hash_password, new_token, password_problem, token_hash, valid_email, verify_password
from .deps import SESSION_COOKIE, current_user, get_db, require_client_header

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Register(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    name: str = Field(default="", max_length=80)


class Login(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class Profile(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    unit_pref: str | None = None


class PasswordChange(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


class Confirm(BaseModel):
    password: str = Field(max_length=256)


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "unit_pref": u["unit_pref"]}


def _start_session(request: Request, response: Response, conn, user_id: int) -> str:
    s = request.app.state.settings
    token, csrf = new_token(), new_token()
    conn.execute("INSERT INTO sessions (user_id, token_hash, csrf_token, created_at, expires_at, user_agent) "
                 "VALUES (?,?,?,?,?,?)", (user_id, token_hash(token), csrf, db.now(),
                                          db.now() + s.session_days * 86400,
                                          request.headers.get("user-agent", "")[:200]))
    conn.commit()
    response.set_cookie(SESSION_COOKIE, token, max_age=s.session_days * 86400, httponly=True,
                        secure=s.secure_cookies, samesite="lax", path="/")
    return csrf


def _client_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "?"
    return f"{host}|{email.lower()}"


@router.post("/register", dependencies=[Depends(require_client_header)])
def register(body: Register, request: Request, response: Response, conn=Depends(get_db)):
    if not request.app.state.settings.allow_registration:
        raise HTTPException(403, "Sign-ups are closed on this server.")
    email = body.email.strip().lower()
    if not valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(422, problem)
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    cur = conn.execute("INSERT INTO users (email, name, password_hash, created_at) VALUES (?,?,?,?)",
                       (email, body.name.strip(), hash_password(body.password), db.now()))
    conn.commit()
    csrf = _start_session(request, response, conn, cur.lastrowid)
    user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
    return {"user": public_user(user), "csrf": csrf}


@router.post("/login", dependencies=[Depends(require_client_header)])
def login(body: Login, request: Request, response: Response, conn=Depends(get_db)):
    email = body.email.strip().lower()
    limiter = request.app.state.login_limiter
    if not limiter.hit(_client_key(request, email)):
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    r = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not r:
        verify_password(body.password, DUMMY_HASH)     # same timing as a real check
        raise HTTPException(401, "Email or password is wrong.")
    if not verify_password(body.password, r["password_hash"]):
        raise HTTPException(401, "Email or password is wrong.")
    limiter.reset(_client_key(request, email))
    csrf = _start_session(request, response, conn, r["id"])
    return {"user": public_user(dict(r)), "csrf": csrf}


@router.post("/logout")
def logout(request: Request, response: Response, user=Depends(current_user), conn=Depends(get_db)):
    conn.execute("DELETE FROM sessions WHERE id = ?", (user["sid"],))
    conn.commit()
    adapter = request.app.state.adapter
    if adapter.owner == user["id"]:
        adapter.disconnect(user["id"])
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)):
    return {"user": public_user(user), "csrf": user["csrf_token"]}


@router.patch("/me")
def update_me(body: Profile, user=Depends(current_user), conn=Depends(get_db)):
    if body.unit_pref is not None and body.unit_pref not in ("mi", "km"):
        raise HTTPException(422, "Units must be mi or km.")
    if body.name is not None:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (body.name.strip(), user["id"]))
    if body.unit_pref is not None:
        conn.execute("UPDATE users SET unit_pref = ? WHERE id = ?", (body.unit_pref, user["id"]))
    conn.commit()
    return {"user": public_user(dict(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()))}


@router.post("/password")
def change_password(body: PasswordChange, user=Depends(current_user), conn=Depends(get_db)):
    r = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not verify_password(body.current, r["password_hash"]):
        raise HTTPException(401, "Current password is wrong.")
    problem = password_problem(body.new)
    if problem:
        raise HTTPException(422, problem)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(body.new), user["id"]))
    conn.execute("DELETE FROM sessions WHERE user_id = ? AND id != ?", (user["id"], user["sid"]))  # sign out others
    conn.commit()
    return {"ok": True}


@router.delete("/me")
def delete_account(body: Confirm, request: Request, response: Response, user=Depends(current_user),
                   conn=Depends(get_db)):
    r = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not verify_password(body.password, r["password_hash"]):
        raise HTTPException(401, "Password is wrong.")
    adapter = request.app.state.adapter
    if adapter.owner == user["id"]:
        adapter.disconnect(user["id"])
    conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    conn.commit()
    shutil.rmtree(request.app.state.settings.upload_dir / str(user["id"]), ignore_errors=True)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
