"""AI settings, assistant tools, approvals, AI-built plans, and both provider loops (no network)."""
from types import SimpleNamespace as NS

import anthropic
import httpx2
import openai
import pytest

from carscan import db
from carscan.ai.base import AIError, Message, Tool
from carscan.ai.claude import ClaudeProvider
from carscan.ai.openai_provider import OpenAIProvider
from carscan.ai.prompts import SCOPE

from .conftest import FakeProvider

KEY = "sk-ant-api03-testkey-0123456789abcd"
VALID_PLAN = {
    "title": "Find the front clunk", "summary": "Likely worn suspension parts.",
    "causes": [{"title": "Sway-bar end links", "fit": "Most likely", "why": "Classic clunk over bumps."}],
    "needs": ["Jack and axle stands"],
    "steps": [
        {"phase": "Read the car", "short": "Module codes", "title": "Read ABS and steering codes", "kind": "auto",
         "how": ["Ignition on"], "capture": ["0d"]},
        {"phase": "Parked", "short": "Bounce test", "title": "Push down on each corner", "kind": "question",
         "how": ["Push", "Let go"], "choices": ["Settled", "Bounced", "Clunked"]},
        {"phase": "Drive", "short": "Speed bump", "title": "Drive over a speed bump slowly", "kind": "auto",
         "how": ["Walking pace"], "safety": "Only where safe.",
         "target": {"pid": "0D", "min": 5, "max": 15, "hold_s": 3}},
    ],
}


def setup_ai(client, **extra):
    r = client.put("/api/ai/settings", {"anthropic_key": KEY, **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ settings
def test_settings_never_return_keys_and_encrypt_at_rest(client, settings):
    s = setup_ai(client, openai_key="sk-openai-test-key-0123456789", claude_model="claude-sonnet-5-5")
    assert s["anthropic_key"] == "…abcd" and s["has_anthropic_key"] and s["has_openai_key"]
    assert KEY not in str(s) and s["claude_model"] == "claude-sonnet-5-5"
    conn = db.connect(settings.db_path)
    stored = conn.execute("SELECT anthropic_key FROM ai_settings").fetchone()[0]
    conn.close()
    assert stored and KEY not in stored


def test_settings_defaults_and_validation(client):
    s = client.get("/api/ai/settings").json()
    assert s["provider"] == "claude" and s["claude_model"] == "claude-opus-5-5"
    assert s["perms"]["clear_codes"] == "ask" and s["has_anthropic_key"] is False
    assert client.put("/api/ai/settings", {"perms": {"clear_codes": "always"}}).status_code == 422
    assert client.put("/api/ai/settings", {"perms": {"raw_commands": "always"}}).status_code == 422
    assert client.put("/api/ai/settings", {"provider": "gemini"}).status_code == 422
    assert client.put("/api/ai/settings", {"anthropic_key": "short"}).status_code == 422
    assert client.put("/api/ai/settings", {"claude_model": "has spaces"}).status_code == 422


def test_removing_a_key(client):
    setup_ai(client)
    s = client.put("/api/ai/settings", {"anthropic_key": ""}).json()
    assert s["has_anthropic_key"] is False


def test_settings_are_per_user(client, other):
    setup_ai(client)
    assert other.get("/api/ai/settings").json()["has_anthropic_key"] is False


def test_key_is_used_to_build_the_provider(client, providers):
    setup_ai(client, claude_model="claude-haiku-5-5")
    assert client.post("/api/ai/test", {"provider": "claude"}).json()["ok"] is True
    assert providers.built[-1] == ("claude", KEY, "claude-haiku-5-5")
    r = client.post("/api/ai/test", {"provider": "openai"})
    assert r.status_code == 400 and "OpenAI API key" in r.json()["detail"]


# ------------------------------------------------------------------ chat
def _conv(client, car_id=None):
    return client.post("/api/conversations", {"car_id": car_id}).json()["id"]


def test_chat_reads_car_data_and_remembers(client, providers):
    setup_ai(client)
    client.connect_demo()
    car = client.add_car()
    providers.provider = FakeProvider(text="Cylinder 1 is misfiring.", script=[
        ("read_fault_codes", {}), ("read_live_data", {"pids": ["0C", "zz"]}),
        ("remember_fact", {"fact": "Spark plugs replaced at 140,000 km"})],
        sources=[{"title": "Forum", "url": "https://example.org/a"}])
    cid = _conv(client, car["id"])
    r = client.post(f"/api/conversations/{cid}/messages", {"text": "Why is the engine light on?"}).json()
    assert r["text"] == "Cylinder 1 is misfiring."
    assert [t["ok"] for t in r["meta"]["tool_log"]] == [True, True, True]
    assert r["meta"]["sources"][0]["url"] == "https://example.org/a"
    call = providers.provider.calls[0]
    assert SCOPE in call["system"] and "1FTFW1ET9DFC10312" in call["system"] and "connected" in call["system"]
    assert call["web_search"] is True
    mem = client.get(f"/api/cars/{car['id']}/memories").json()["memories"]
    assert mem[0]["fact"].startswith("Spark plugs")
    conv = client.get(f"/api/conversations/{cid}").json()
    assert conv["title"] == "Why is the engine light on?" and [m["role"] for m in conv["messages"]] == ["user", "assistant"]

    # the memory is part of the next conversation's context
    providers.provider = FakeProvider()
    client.post(f"/api/conversations/{_conv(client, car['id'])}/messages", {"text": "Anything else?"})
    assert "Spark plugs replaced" in providers.provider.calls[0]["system"]


def test_chat_history_is_sent_back(client, providers):
    setup_ai(client)
    cid = _conv(client)
    client.post(f"/api/conversations/{cid}/messages", {"text": "First"})
    client.post(f"/api/conversations/{cid}/messages", {"text": "Second"})
    msgs = providers.provider.calls[-1]["messages"]
    assert [(m.role, m.text) for m in msgs] == [("user", "First"), ("assistant", "Here is what I found."),
                                                 ("user", "Second")]


def test_hide_vin_and_memory_off(client, providers):
    setup_ai(client, hide_vin=True, memory_enabled=False)
    car = client.add_car()
    providers.provider = FakeProvider(script=[("remember_fact", {"fact": "secret"})])
    client.post(f"/api/conversations/{_conv(client, car['id'])}/messages", {"text": "hi"})
    assert "1FTFW1ET9DFC10312" not in providers.provider.calls[0]["system"]
    assert client.get(f"/api/cars/{car['id']}/memories").json()["memories"] == []


def test_assistant_can_only_request_clearing_and_user_must_approve(client, providers):
    setup_ai(client)
    client.connect_demo()
    car = client.add_car()
    providers.provider = FakeProvider(script=[("request_clear_codes", {"target": "obd", "reason": "Coil replaced"})])
    r = client.post(f"/api/conversations/{_conv(client, car['id'])}/messages", {"text": "Clear it"}).json()
    action_id = r["meta"]["pending_action_ids"][0]
    # nothing was cleared yet
    assert [c["code"] for c in client.post(f"/api/cars/{car['id']}/scans/obd", {}).json()["stored"]]
    actions = client.get("/api/actions").json()["actions"]
    assert actions[0]["payload"]["reason"] == "Coil replaced"
    assert client.post(f"/api/actions/{action_id}/approve", {"confirm": "yes"}).status_code == 422
    assert client.post(f"/api/actions/{action_id}/approve", {"confirm": "CLEAR"}).json()["result"]["ok"] is True
    assert client.post(f"/api/cars/{car['id']}/scans/obd", {}).json()["stored"] == []
    assert client.post(f"/api/actions/{action_id}/approve", {"confirm": "CLEAR"}).status_code == 404


def test_declining_and_isolation_of_actions(client, other, providers):
    setup_ai(client)
    providers.provider = FakeProvider(script=[("request_clear_codes", {"target": "obd", "reason": "x"})])
    car = client.add_car()
    aid = client.post(f"/api/conversations/{_conv(client, car['id'])}/messages", {"text": "x"}).json()["meta"][
        "pending_action_ids"][0]
    assert other.post(f"/api/actions/{aid}/approve", {"confirm": "CLEAR"}).status_code == 404
    assert other.post(f"/api/actions/{aid}/decline").status_code == 404
    assert client.post(f"/api/actions/{aid}/decline").status_code == 200
    assert client.get("/api/actions").json()["actions"] == []


def test_permissions_remove_tools(client, providers):
    setup_ai(client, perms={"clear_codes": "never", "start_tests": "never", "web_search": "never"})
    client.post(f"/api/conversations/{_conv(client)}/messages", {"text": "hi"})
    call = providers.provider.calls[0]
    assert "request_clear_codes" not in call["tools"] and "create_guided_plan" not in call["tools"]
    assert call["web_search"] is False


def test_read_data_permission_blocks_tool(client, providers):
    setup_ai(client, perms={"read_data": "never"})
    providers.provider = FakeProvider(script=[("read_fault_codes", {})])
    r = client.post(f"/api/conversations/{_conv(client)}/messages", {"text": "codes?"}).json()
    assert r["meta"]["tool_log"][0]["ok"] is False


def test_assistant_builds_a_guided_plan(client, providers):
    setup_ai(client)
    car = client.add_car()
    providers.provider = FakeProvider(script=[("create_guided_plan", {"plan": VALID_PLAN})])
    r = client.post(f"/api/conversations/{_conv(client, car['id'])}/messages", {"text": "Clunk"}).json()
    run = client.get(f"/api/runs/{r['meta']['plan_run_id']}").json()
    assert run["status"] == "draft" and run["plan"]["source"] == "ai"
    assert run["plan"]["steps"][0]["capture"] == ["0D"] and run["plan"]["steps"][0]["id"] == "s1"


def test_conversations_private_and_deletable(client, other):
    setup_ai(client)
    cid = _conv(client)
    assert other.get(f"/api/conversations/{cid}").status_code == 404
    assert other.post(f"/api/conversations/{cid}/messages", {"text": "hi"}).status_code == 404
    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert client.get(f"/api/conversations/{cid}").status_code == 404


def test_chat_without_key_explains(client):
    r = client.post(f"/api/conversations/{_conv(client)}/messages", {"text": "hi"})
    assert r.status_code == 400 and "Settings" in r.json()["detail"]


def test_delete_all_memories(client, providers):
    setup_ai(client)
    car = client.add_car()
    providers.provider = FakeProvider(script=[("remember_fact", {"fact": "a"})])
    client.post(f"/api/conversations/{_conv(client, car['id'])}/messages", {"text": "x"})
    assert client.delete("/api/memories").status_code == 200
    assert client.get(f"/api/cars/{car['id']}/memories").json()["memories"] == []


# ------------------------------------------------------------------ AI plan for a described problem
def test_ai_plan_from_description(client, providers):
    setup_ai(client)
    client.connect_demo()
    car = client.add_car()
    providers.provider = FakeProvider(script=[("read_fault_codes", {}), ("get_scan_history", {}),
                                              ("submit_plan", {"plan": VALID_PLAN})],
                                      sources=[{"title": "Guide", "url": "https://example.org/g"}])
    r = client.post("/api/runs/ai", {"car_id": car["id"], "problem": "Clunk from the front over bumps",
                                     "when": ["Over bumps"]})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "draft" and run["plan"]["causes"][0]["fit"] == "Most likely"
    assert run["plan"]["sources"][0]["url"] == "https://example.org/g"
    call = providers.provider.calls[0]
    assert call["effort"] == "high" and "submit_plan" in call["tools"]
    assert "Over bumps" in call["messages"][0].text
    started = client.post(f"/api/runs/{run['id']}/start", {"skip_steps": ["s2"]}).json()
    assert started["status"] == "running" and [s["id"] for s in started["plan"]["steps"]] == ["s1", "s3"]


def test_ai_plan_invalid_or_missing(client, providers):
    setup_ai(client)
    providers.provider = FakeProvider(script=[("submit_plan", {"plan": {"title": "x", "steps": []}})])
    r = client.post("/api/runs/ai", {"problem": "Strange smell when braking"})
    assert r.status_code == 400 and "didn't return a plan" in r.json()["detail"]
    assert client.post("/api/runs/ai", {"problem": "x"}).status_code == 422


# ------------------------------------------------------------------ Claude provider loop (fake SDK client)
class FakeAnthropic:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})   # snapshot: the loop keeps appending
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(stop, *blocks):
    return NS(stop_reason=stop, content=list(blocks), model="claude-opus-5-5")


def _api_error(cls, status, mod):
    req = mod.Request("POST", "https://api.example.test/v1")
    return cls("boom", response=mod.Response(status, request=req), body=None)


def test_claude_loop_runs_tools_and_collects_sources():
    calls = []
    tool = Tool("read_fault_codes", "d", {"type": "object", "properties": {}}, lambda a: calls.append(a) or {"x": 1},
                "Read codes")
    fake = FakeAnthropic([
        _resp("tool_use", NS(type="server_tool_use", name="web_search", input={"query": "F-150 P0301"}),
              NS(type="web_search_tool_result", content=[NS(url="https://a.example", title="A")]),
              NS(type="tool_use", id="tu_1", name="read_fault_codes", input={})),
        _resp("pause_turn", NS(type="text", text="")),
        _resp("end_turn", NS(type="text", text="Coil on cylinder 1.")),
    ])
    out = ClaudeProvider(KEY, "claude-opus-5-5", client=fake).run("sys", [Message("user", "hi")], [tool])
    assert out.text == "Coil on cylinder 1." and calls == [{}]
    assert out.sources == [{"title": "A", "url": "https://a.example"}]
    assert [t["tool"] for t in out.tool_log] == ["web_search", "read_fault_codes"]
    first = fake.requests[0]
    assert first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive"}
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    assert {"type": "web_search_20260209", "name": "web_search", "max_uses": 5} in first["tools"]
    tool_result = fake.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_1"
    assert len(fake.requests) == 3


def test_claude_tool_errors_are_reported_to_the_model():
    boom = Tool("t", "d", {"type": "object"}, lambda a: 1 / 0)
    fake = FakeAnthropic([_resp("tool_use", NS(type="tool_use", id="x", name="t", input={})),
                          _resp("end_turn", NS(type="text", text="ok"))])
    out = ClaudeProvider(KEY, client=fake).run("s", [Message("user", "hi")], [boom], web_search=False)
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and out.tool_log[0]["ok"] is False
    assert not any(t.get("type") == "web_search_20260209" for t in fake.requests[0]["tools"])


def test_claude_images_and_no_fallback_for_haiku():
    fake = FakeAnthropic([_resp("end_turn", NS(type="text", text="ok"))])
    ClaudeProvider(KEY, "claude-haiku-5-5", client=fake).run("s", [Message("user", "look", ["QUJD"])], [])
    req = fake.requests[0]
    assert "fallbacks" not in req and "betas" not in req
    assert req["messages"][0]["content"][0]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}


@pytest.mark.parametrize("stop,msg", [("refusal", "declined"), ("max_tokens", "too long")])
def test_claude_refusal_and_truncation(stop, msg):
    fake = FakeAnthropic([_resp(stop)])
    with pytest.raises(AIError, match=msg):
        ClaudeProvider(KEY, client=fake).run("s", [Message("user", "x")], [])


def test_claude_api_errors_become_friendly_messages():
    for err, msg in ((_api_error(anthropic.AuthenticationError, 401, httpx2), "rejected the API key"),
                     (_api_error(anthropic.RateLimitError, 429, httpx2), "rate limit"),
                     (_api_error(anthropic.InternalServerError, 500, httpx2), "error \\(500\\)")):
        with pytest.raises(AIError, match=msg):
            ClaudeProvider(KEY, client=FakeAnthropic([err])).run("s", [Message("user", "x")], [])


def test_claude_requires_a_key_and_caps_turns():
    with pytest.raises(AIError, match="Anthropic API key"):
        ClaudeProvider("")
    loop = FakeAnthropic([_resp("pause_turn", NS(type="text", text=""))] * 3)
    with pytest.raises(AIError, match="too many steps"):
        ClaudeProvider(KEY, client=loop).run("s", [Message("user", "x")], [], max_turns=3)


# ------------------------------------------------------------------ OpenAI provider loop (fake SDK client)
class FakeOpenAI:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.responses_api = NS(create=self._create)

    @property
    def responses_(self):  # pragma: no cover - not used
        return self.responses_api

    def _create(self, **kw):
        self.requests.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _openai_client(responses):
    fake = FakeOpenAI(responses)
    return NS(responses=fake.responses_api), fake


def test_openai_loop_runs_function_calls_with_previous_response_id():
    got = []
    tool = Tool("get_scan_history", "d", {"type": "object", "properties": {}}, lambda a: got.append(a) or {"scans": []})
    client, fake = _openai_client([
        NS(id="r1", output=[NS(type="web_search_call", action=NS(query="P0301 F-150")),
                            NS(type="function_call", name="get_scan_history", arguments='{"x": 1}', call_id="c1")],
           output_text=""),
        NS(id="r2", output=[NS(type="message", content=[NS(annotations=[NS(url="https://b.example", title="B")])])],
           output_text="Check the coil."),
    ])
    out = OpenAIProvider("sk-openai-test-key-0123456789", "gpt-5", client=client).run(
        "sys", [Message("user", "hi"), Message("assistant", "hello"), Message("user", "img", ["QUJD"])], [tool])
    assert out.text == "Check the coil." and got == [{"x": 1}]
    assert out.sources == [{"title": "B", "url": "https://b.example"}]
    first, second = fake.requests
    assert first["instructions"] == "sys" and {"type": "web_search"} in first["tools"]
    assert first["input"][2]["content"][0] == {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"}
    assert second["previous_response_id"] == "r1"
    assert second["input"] == [{"type": "function_call_output", "call_id": "c1", "output": '{"scans": []}'}]


def test_openai_errors_and_key():
    with pytest.raises(AIError, match="OpenAI API key"):
        OpenAIProvider("")
    client, _ = _openai_client([_api_error(openai.AuthenticationError, 401, __import__("httpx"))])
    with pytest.raises(AIError, match="rejected the API key"):
        OpenAIProvider("k" * 30, client=client).run("s", [Message("user", "x")], [])
