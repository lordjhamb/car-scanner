"""Accounts, sessions, CSRF, rate limiting and HTTP security headers."""
from carscan import db
from carscan.api.deps import SESSION_COOKIE

from .conftest import PASSWORD, Client


def test_register_sets_httponly_session_cookie(anon):
    r = anon.post("/api/auth/register", {"email": "New@Example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "new@example.com"
    cookie = r.headers["set-cookie"]
    assert SESSION_COOKIE in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()


def test_me_requires_session(anon, client):
    assert anon.get("/api/auth/me").status_code == 401
    me = client.get("/api/auth/me").json()
    assert me["user"]["email"] == "owner@example.com" and me["csrf"] == client.csrf


def test_duplicate_email_rejected(app, client):
    r = Client(app).post("/api/auth/register", {"email": "OWNER@example.com", "password": PASSWORD})
    assert r.status_code == 409


def test_registration_validation(anon):
    assert anon.post("/api/auth/register", {"email": "bad", "password": PASSWORD}).status_code == 422
    r = anon.post("/api/auth/register", {"email": "x@y.co", "password": "short"})
    assert r.status_code == 422 and "10 characters" in r.json()["detail"]


def test_registration_can_be_closed(app, anon):
    app.state.settings.allow_registration = False
    assert anon.post("/api/auth/register", {"email": "x@y.co", "password": PASSWORD}).status_code == 403


def test_login_logout_flow(app, client):
    c2 = Client(app)
    assert c2.login("owner@example.com").status_code == 200
    assert c2.get("/api/cars").status_code == 200
    assert c2.post("/api/auth/logout").status_code == 200
    assert c2.get("/api/cars").status_code == 401
    assert client.get("/api/cars").status_code == 200          # other session unaffected


def test_wrong_password_and_unknown_email_look_the_same(app, client):
    c = Client(app)
    a = c.login("owner@example.com", "wrong password 1")
    b = c.login("nobody@example.com", "wrong password 1")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_login_rate_limited(app, client):
    c = Client(app)
    codes = [c.login("owner@example.com", "wrong password 1").status_code for _ in range(6)]
    assert codes[-1] == 429
    assert c.login("owner@example.com").status_code == 429     # even the right password, until the window passes


def test_state_changing_requests_need_client_header(anon):
    r = anon.http.post("/api/auth/register", json={"email": "x@y.co", "password": PASSWORD})
    assert r.status_code == 403


def test_csrf_token_required_for_authenticated_writes(client):
    r = client.http.post("/api/cars", json={"vin": "1FTFW1ET9DFC10312"}, headers={"X-Requested-With": "CarScanner"})
    assert r.status_code == 403
    r = client.http.post("/api/cars", json={"vin": "1FTFW1ET9DFC10312"},
                         headers={"X-Requested-With": "CarScanner", "X-CSRF-Token": "forged"})
    assert r.status_code == 403
    assert client.post("/api/cars", {"vin": "1FTFW1ET9DFC10312"}).status_code == 200


def test_profile_update(client):
    r = client.patch("/api/auth/me", {"name": "Prachi", "unit_pref": "km"})
    assert r.json()["user"] == {**client.user, "name": "Prachi", "unit_pref": "km"}
    assert client.patch("/api/auth/me", {"unit_pref": "furlongs"}).status_code == 422


def test_password_change_signs_out_other_sessions(app, client):
    other_session = Client(app)
    other_session.login("owner@example.com")
    assert client.post("/api/auth/password", {"current": "wrong one 123", "new": "new pass 12345"}).status_code == 401
    assert client.post("/api/auth/password", {"current": PASSWORD, "new": "new pass 12345"}).status_code == 200
    assert other_session.get("/api/cars").status_code == 401
    assert client.get("/api/cars").status_code == 200
    assert Client(app).login("owner@example.com", "new pass 12345").status_code == 200


def test_expired_session_is_rejected(app, client, settings):
    conn = db.connect(settings.db_path)
    conn.execute("UPDATE sessions SET expires_at = 0")
    conn.commit()
    conn.close()
    assert client.get("/api/auth/me").status_code == 401


def test_delete_account_removes_everything(app, client, settings):
    client.add_car()
    assert client.delete("/api/auth/me", {"password": "wrong pass 123"}).status_code == 401
    assert client.delete("/api/auth/me", {"password": PASSWORD}).status_code == 200
    conn = db.connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM cars").fetchone()[0] == 0
    conn.close()
    assert Client(app).login("owner@example.com").status_code == 401


def test_security_headers_and_no_store(client):
    r = client.get("/api/auth/me")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_health_and_spa(anon):
    assert anon.get("/api/health").json()["ok"] is True
    for path in ("/", "/garage", "/some/deep/link"):
        r = anon.get(path)
        assert r.status_code == 200 and 'id="root"' in r.text
    assert anon.get("/static/js/app.js").status_code == 200
    assert anon.get("/static/css/app.css").status_code == 200
    r = anon.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["detail"]
