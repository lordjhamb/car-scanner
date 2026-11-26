"""Shared fixtures: an isolated app per test, the simulated car, and a scripted AI provider."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from carscan.adapter import AdapterManager
from carscan.ai.base import RunResult
from carscan.app import create_app
from carscan.config import load_settings

PASSWORD = "correct horse 42"


@dataclass
class FakeProvider:
    """Calls the given tools in order, then returns ``text``. Records every run."""
    name: str = "claude"
    model: str = "fake-model"
    script: list = field(default_factory=list)      # [(tool_name, args)]
    text: str = "Here is what I found."
    sources: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def run(self, system, messages, tools, *, web_search=True, effort="medium", max_turns=10):
        self.calls.append({"system": system, "messages": messages, "tools": [t.name for t in tools],
                           "web_search": web_search, "effort": effort})
        by_name = {t.name: t for t in tools}
        log = []
        for name, args in self.script:
            if name not in by_name:
                log.append({"tool": name, "label": name, "detail": "unavailable", "ok": False})
                continue
            try:
                by_name[name].call(args)
                log.append({"tool": name, "label": by_name[name].label, "detail": "", "ok": True})
            except Exception as e:  # mirror real providers: report, don't crash
                log.append({"tool": name, "label": name, "detail": str(e), "ok": False})
        return RunResult(text=self.text, tool_log=log, sources=self.sources, stop_reason="end_turn", model=self.model)


class ProviderBox:
    """Lets a test swap the fake provider the app will build."""

    def __init__(self):
        self.provider = FakeProvider()
        self.built: list = []

    def factory(self, name, api_key, model):
        self.built.append((name, api_key, model))
        self.provider.name = name
        return self.provider


@pytest.fixture
def settings(tmp_path):
    return load_settings(env="test", data_dir=tmp_path / "data", secret_key="test-secret-key-" + "x" * 32,
                         login_attempts_per_10min=5)


@pytest.fixture
def providers():
    return ProviderBox()


@pytest.fixture
def adapter():
    return AdapterManager()


@pytest.fixture
def app(settings, adapter, providers):
    return create_app(settings, adapter=adapter, provider_factory=providers.factory)


class Client:
    """TestClient wrapper that sends the client header and CSRF token like the browser does."""

    def __init__(self, app):
        self.http = TestClient(app)
        self.csrf = None
        self.user = None

    def headers(self, extra=None):
        h = {"X-Requested-With": "CarScanner"}
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        return {**h, **(extra or {})}

    def get(self, path, **kw):
        return self.http.get(path, **kw)

    def post(self, path, json=None, **kw):
        return self.http.post(path, json=json, headers=self.headers(kw.pop("headers", None)), **kw)

    def put(self, path, json=None, **kw):
        return self.http.put(path, json=json, headers=self.headers(kw.pop("headers", None)), **kw)

    def patch(self, path, json=None, **kw):
        return self.http.patch(path, json=json, headers=self.headers(kw.pop("headers", None)), **kw)

    def delete(self, path, json=None, **kw):
        return self.http.request("DELETE", path, json=json, headers=self.headers(kw.pop("headers", None)), **kw)

    def register(self, email="owner@example.com", password=PASSWORD, name="Owner"):
        r = self.post("/api/auth/register", {"email": email, "password": password, "name": name})
        assert r.status_code == 200, r.text
        self.csrf, self.user = r.json()["csrf"], r.json()["user"]
        return self

    def login(self, email, password=PASSWORD):
        r = self.post("/api/auth/login", {"email": email, "password": password})
        if r.status_code == 200:
            self.csrf, self.user = r.json()["csrf"], r.json()["user"]
        return r

    # convenience for the simulated car
    def connect_demo(self, scenario="genuine"):
        r = self.post("/api/adapter/connect", {"simulate": True, "scenario": scenario})
        assert r.status_code == 200, r.text
        return r.json()

    def add_car(self, vin="1FTFW1ET9DFC10312", **kw):
        r = self.post("/api/cars", {"vin": vin, "model": "F-150", "engine": "3.5 L V6", **kw})
        assert r.status_code == 200, r.text
        return r.json()["car"]

    def module_scan(self, car_id, full=False):
        r = self.post(f"/api/cars/{car_id}/scans/modules", {"full": full})
        assert r.status_code == 200, r.text
        for _ in range(200):
            s = self.get("/api/adapter/scan").json()
            if not s["running"]:
                return s
            time.sleep(0.02)
        raise AssertionError("module scan did not finish")


@pytest.fixture
def client(app):
    return Client(app).register()


@pytest.fixture
def other(app):
    return Client(app).register(email="someone.else@example.com", name="Other")


@pytest.fixture
def anon(app):
    return Client(app)
