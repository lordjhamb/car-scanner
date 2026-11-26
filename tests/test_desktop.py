"""Personal (desktop) installs: launcher helpers, local sign-in, host guard, update check, quit."""
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from carscan import __version__, desktop
from carscan.adapter import AdapterManager
from carscan.api.system import LOCAL_EMAIL, parse_version
from carscan.app import create_app
from carscan.config import load_settings

H = {"X-Requested-With": "CarScanner"}


# ------------------------------------------------------------------ launcher helpers
@pytest.mark.parametrize("system,env,expected", [
    ("Darwin", {}, "/home/u/Library/Application Support/Car Scanner"),
    ("Windows", {"APPDATA": "C:/Users/u/AppData/Roaming"}, "C:/Users/u/AppData/Roaming/Car Scanner"),
    ("Linux", {}, "/home/u/.local/share/car-scanner"),
    ("Linux", {"XDG_DATA_HOME": "/data"}, "/data/car-scanner"),
])
def test_user_data_dir(system, env, expected):
    assert desktop.user_data_dir(system, Path("/home/u"), env) == Path(expected)


def test_choose_port_prefers_default_and_falls_back():
    port = desktop.free_port()
    assert desktop.choose_port(port) == (port, False)
    with socket.socket() as s:                      # something else holds the port
        s.bind(("127.0.0.1", port))
        s.listen()
        other, already = desktop.choose_port(port)
    assert other != port and already is False


def test_version_flag(capsys):
    assert desktop.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_parse_version():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("0.10.0") > parse_version("0.9.9")
    assert parse_version("1.0.0-beta") == (1, 0, 0)
    assert parse_version("") == (0,)


# ------------------------------------------------------------------ apps
def _app(tmp_path, local=True, fetcher=None, **kw):
    s = load_settings(env="test", data_dir=tmp_path / "d", secret_key="s" * 40, local_mode=local, **kw)
    return create_app(s, adapter=AdapterManager(), release_fetcher=fetcher)


def _local_client(app, client_host="127.0.0.1"):
    return TestClient(app, base_url="http://127.0.0.1:8077", client=(client_host, 50000))


def test_server_mode_has_no_local_sign_in(app, anon):
    info = anon.get("/api/system/info").json()
    assert info["local_mode"] is False and info["auto_login"] is False and info["can_quit"] is False
    assert anon.post("/api/system/local-login").status_code == 403


def test_local_sign_in_creates_one_owner_account(tmp_path):
    c = _local_client(_app(tmp_path))
    info = c.get("/api/system/info").json()
    assert info["local_mode"] and info["auto_login"] and info["repo"] == "lordjhamb/car-scanner"
    r = c.post("/api/system/local-login", headers=H)
    assert r.status_code == 200 and r.json()["user"]["email"] == LOCAL_EMAIL
    first_id = r.json()["user"]["id"]
    csrf = r.json()["csrf"]
    assert c.get("/api/cars").status_code == 200
    assert c.post("/api/cars", json={"vin": "1FTFW1ET9DFC10312"}, headers={**H, "X-CSRF-Token": csrf}).status_code == 200
    again = c.post("/api/system/local-login", headers=H).json()
    assert again["user"]["id"] == first_id                      # same garage every time


def test_local_account_cannot_be_used_with_a_password(tmp_path):
    c = _local_client(_app(tmp_path))
    c.post("/api/system/local-login", headers=H)
    r = c.post("/api/auth/login", json={"email": LOCAL_EMAIL, "password": "anything at all 1"}, headers=H)
    assert r.status_code == 401


def test_local_sign_in_needs_loopback_client_header_and_setting(tmp_path):
    app = _app(tmp_path)
    assert _local_client(app, "10.0.0.5").post("/api/system/local-login", headers=H).status_code == 403
    assert _local_client(app).post("/api/system/local-login").status_code == 403          # no client header
    off = _local_client(_app(tmp_path / "x", auto_login=False))
    assert off.post("/api/system/local-login", headers=H).status_code == 403


def test_host_guard_blocks_dns_rebinding(tmp_path):
    c = _local_client(_app(tmp_path))
    r = c.get("/api/system/info", headers={"Host": "attacker.example"})
    assert r.status_code == 403
    assert c.get("/api/system/info", headers={"Host": "localhost:8077"}).status_code == 200
    assert c.get("/", headers={"Host": "evil.example:8077"}).status_code == 403


def test_update_check(tmp_path):
    calls = []

    def newer(repo):
        calls.append(repo)
        return {"version": "99.0.0", "url": "https://github.com/lordjhamb/car-scanner/releases/tag/v99.0.0", "name": "v99"}

    c = _local_client(_app(tmp_path, fetcher=newer, update_check=True))
    u = c.get("/api/system/update").json()
    assert u["available"] is True and u["latest"] == "99.0.0" and u["url"].endswith("v99.0.0")
    c.get("/api/system/update")
    assert calls == ["lordjhamb/car-scanner"]                   # cached

    same = _local_client(_app(tmp_path / "b", fetcher=lambda r: {"version": __version__, "url": "u", "name": ""},
                              update_check=True))
    assert same.get("/api/system/update").json()["available"] is False
    offline = _local_client(_app(tmp_path / "c", fetcher=lambda r: None, update_check=True))
    assert offline.get("/api/system/update").json() == {"current": __version__, "available": False, "checked": False,
                                                        "latest": None, "url": None}
    disabled = _local_client(_app(tmp_path / "e", fetcher=newer))
    assert disabled.get("/api/system/update").json()["checked"] is False


def test_quit_stops_the_desktop_app(tmp_path):
    app = _app(tmp_path)
    stopped = []
    app.state.shutdown = lambda: stopped.append(True)
    c = _local_client(app)
    csrf = c.post("/api/system/local-login", headers=H).json()["csrf"]
    c.post("/api/adapter/connect", json={"simulate": True}, headers={**H, "X-CSRF-Token": csrf})
    assert c.get("/api/system/info").json()["can_quit"] is True
    assert c.post("/api/system/shutdown", headers={**H, "X-CSRF-Token": csrf}).json() == {"ok": True}
    assert stopped == [True] and app.state.adapter.elm is None


def test_quit_refused_outside_desktop(client):
    assert client.post("/api/system/shutdown").status_code == 403
