"""Assistant orchestration: settings, car context, app tools, chat, plans, photo reports."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError

from .. import db
from ..adapter import AdapterManager
from ..security import SecretBox, mask_key
from ..services import photos as photos_svc
from ..services import plans as plans_svc
from .base import AIError, Message, Provider, Tool
from .claude import ClaudeProvider
from .openai_provider import OpenAIProvider
from .prompts import PHOTOS, PLAN, SCOPE

DEFAULT_PERMS = {"read_data": "always", "web_search": "always", "start_tests": "ask",
                 "clear_codes": "ask", "raw_commands": "never"}
PERM_CHOICES = {"read_data": ("always", "never"), "web_search": ("always", "never"),
                "start_tests": ("ask", "never"), "clear_codes": ("ask", "never"), "raw_commands": ("never",)}
CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
HISTORY_LIMIT = 40
CONTEXT_CHAR_BUDGET = 400_000      # rough guide for the "conversation length" meter

ProviderFactory = Callable[[str, str, str], Provider]


def default_provider_factory(name: str, api_key: str, model: str) -> Provider:
    if name == "claude":
        return ClaudeProvider(api_key, model)
    if name == "openai":
        return OpenAIProvider(api_key, model)
    raise AIError(f"Unknown provider {name!r}")


# ------------------------------------------------------------------ settings
def load_settings(conn: sqlite3.Connection, box: SecretBox, user_id: int, defaults) -> dict:
    r = db.row(conn.execute("SELECT * FROM ai_settings WHERE user_id = ?", (user_id,)).fetchone())
    if not r:
        r = {"provider": "claude", "claude_model": "", "openai_model": "", "anthropic_key": None,
             "openai_key": None, "perms": "{}", "memory_enabled": 1, "hide_vin": 0, "monthly_limit_usd": None}
    perms = {**DEFAULT_PERMS, **db.loads(r["perms"], {})}
    return {
        "provider": r["provider"],
        "claude_model": r["claude_model"] or defaults.default_claude_model,
        "openai_model": r["openai_model"] or defaults.default_openai_model,
        "anthropic_key": box.decrypt(r["anthropic_key"]),
        "openai_key": box.decrypt(r["openai_key"]),
        "perms": perms,
        "memory_enabled": bool(r["memory_enabled"]),
        "hide_vin": bool(r["hide_vin"]),
        "monthly_limit_usd": r["monthly_limit_usd"],
    }


def public_settings(s: dict) -> dict:
    """What the browser may see: never the keys themselves."""
    return {k: v for k, v in s.items() if k not in ("anthropic_key", "openai_key")} | {
        "anthropic_key": mask_key(s["anthropic_key"]), "openai_key": mask_key(s["openai_key"]),
        "has_anthropic_key": bool(s["anthropic_key"]), "has_openai_key": bool(s["openai_key"]),
        "claude_models": CLAUDE_MODELS,
    }


def save_settings(conn, box: SecretBox, user_id: int, changes: dict) -> None:
    conn.execute("INSERT OR IGNORE INTO ai_settings (user_id) VALUES (?)", (user_id,))
    cur = db.row(conn.execute("SELECT * FROM ai_settings WHERE user_id = ?", (user_id,)).fetchone())
    fields = {}
    if "provider" in changes:
        if changes["provider"] not in ("claude", "openai"):
            raise ValueError("provider must be claude or openai")
        fields["provider"] = changes["provider"]
    for k in ("claude_model", "openai_model"):
        if k in changes:
            v = (changes[k] or "").strip()
            if len(v) > 80 or any(c.isspace() for c in v):
                raise ValueError(f"{k} looks wrong")
            fields[k] = v
    for k, col in (("anthropic_key", "anthropic_key"), ("openai_key", "openai_key")):
        if k in changes:
            v = (changes[k] or "").strip()
            if v and (len(v) < 20 or len(v) > 300 or any(c.isspace() for c in v)):
                raise ValueError("That doesn't look like an API key.")
            fields[col] = box.encrypt(v) if v else None
    if "perms" in changes:
        perms = {**DEFAULT_PERMS, **db.loads(cur["perms"], {})}
        for k, v in (changes["perms"] or {}).items():
            if k not in PERM_CHOICES or v not in PERM_CHOICES[k]:
                raise ValueError(f"Permission {k} can't be set to {v}.")
            perms[k] = v
        fields["perms"] = db.dumps(perms)
    for k in ("memory_enabled", "hide_vin"):
        if k in changes:
            fields[k] = 1 if changes[k] else 0
    if "monthly_limit_usd" in changes:
        v = changes["monthly_limit_usd"]
        fields["monthly_limit_usd"] = None if v in (None, "") else max(0.0, float(v))
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE ai_settings SET {sets} WHERE user_id = ?", (*fields.values(), user_id))
    conn.commit()


def make_provider(settings: dict, factory: ProviderFactory, override: str | None = None) -> Provider:
    name = override or settings["provider"]
    if name == "claude":
        if not settings["anthropic_key"]:
            raise AIError("Add your Anthropic API key in Settings to use Claude.")
        return factory("claude", settings["anthropic_key"], settings["claude_model"])
    if name == "openai":
        if not settings["openai_key"]:
            raise AIError("Add your OpenAI API key in Settings to use Codex.")
        return factory("openai", settings["openai_key"], settings["openai_model"])
    raise AIError("Unknown provider.")


# ------------------------------------------------------------------ context
class Context:
    def __init__(self, conn, user_id: int, car_id: int | None, adapter: AdapterManager, settings: dict):
        self.conn, self.user_id, self.car_id, self.adapter, self.settings = conn, user_id, car_id, adapter, settings
        self.plan_run_id: int | None = None
        self.plan: dict | None = None
        self.pending_ids: list[int] = []
        self.report: dict | None = None

    def car(self) -> dict | None:
        if not self.car_id:
            return None
        return db.row(self.conn.execute("SELECT * FROM cars WHERE id = ? AND user_id = ?",
                                        (self.car_id, self.user_id)).fetchone())

    def adapter_live(self) -> bool:
        return self.adapter.elm is not None and self.adapter.owner == self.user_id

    def memories(self) -> list[str]:
        if not self.car_id:
            return []
        return [r["fact"] for r in self.conn.execute(
            "SELECT fact FROM memories WHERE user_id = ? AND car_id = ? ORDER BY id", (self.user_id, self.car_id))]

    def summary_text(self) -> str:
        car = self.car()
        lines = []
        if car:
            vin = "hidden by the user" if self.settings["hide_vin"] else car["vin"]
            lines.append(f"Car: {car['year'] or ''} {car['make']} {car['model']} {car['engine']}".strip()
                         + f"; VIN {vin}.")
            latest = self.conn.execute("SELECT kind, odometer_km, created_at FROM scans WHERE car_id = ? "
                                       "ORDER BY created_at DESC LIMIT 1", (car["id"],)).fetchone()
            if latest:
                lines.append(f"Latest saved scan: {latest['kind']}, odometer {latest['odometer_km'] or 'unknown'} km.")
        else:
            lines.append("No car selected.")
        lines.append("Adapter: " + ("connected to this car." if self.adapter_live() else "not connected."))
        mem = self.memories()
        if mem and self.settings["memory_enabled"]:
            lines.append("Remembered about this car: " + " | ".join(mem))
        return "\n".join(lines)


def _require_perm(ctx: Context, key: str, allowed=("always", "ask")):
    if ctx.settings["perms"].get(key) not in allowed:
        raise PermissionError(f"The user has turned off '{key}' for the assistant in Settings.")


def _latest_codes(ctx: Context) -> dict:
    out = {"source": "saved scan", "obd": None, "modules": None}
    for r in ctx.conn.execute("SELECT kind, data, created_at FROM scans WHERE car_id = ? AND user_id = ? "
                              "ORDER BY created_at DESC LIMIT 10", (ctx.car_id, ctx.user_id)):
        data = db.loads(r["data"], {})
        if r["kind"] == "obd" and out["obd"] is None:
            out["obd"] = data
        if r["kind"] == "modules" and out["modules"] is None:
            out["modules"] = [{"name": m["name"], "codes": [
                {"code": d["full"], "state": d["state"], "description": d["description"]} for d in m["dtcs"]]}
                for m in data.get("modules", [])]
    return out


# ------------------------------------------------------------------ tools
STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"}, "phase": {"type": "string"}, "short": {"type": "string"},
        "title": {"type": "string"}, "why": {"type": "string"},
        "how": {"type": "array", "items": {"type": "string"}}, "safety": {"type": "string"},
        "kind": {"type": "string", "enum": ["auto", "confirm", "question"]},
        "choices": {"type": "array", "items": {"type": "string"}},
        "capture": {"type": "array", "items": {"type": "string"}, "description": "OBD mode 01 PIDs, 2 hex digits"},
        "target": {"type": "object", "properties": {
            "pid": {"type": "string"}, "min": {"type": "number"}, "max": {"type": "number"},
            "hold_s": {"type": "integer"}, "label": {"type": "string"}}, "required": ["pid", "min", "max"]},
    },
    "required": ["id", "phase", "short", "title", "kind", "how"],
}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"}, "summary": {"type": "string"},
        "causes": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "fit": {"type": "string", "enum": ["Most likely", "Possible", "Less likely"]},
            "why": {"type": "string"}}, "required": ["title", "fit", "why"]}},
        "needs": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": STEP_SCHEMA},
    },
    "required": ["title", "summary", "causes", "steps"],
}


def build_tools(ctx: Context, *, include_plan_tool=True, include_actions=True) -> list[Tool]:
    def read_codes(_args):
        _require_perm(ctx, "read_data", ("always",))
        if ctx.adapter_live():
            data = ctx.adapter.obd_codes(ctx.user_id)
            mods = [{"name": m["name"], "codes": [{"code": d["full"], "state": d["state"]} for d in m["dtcs"]]}
                    for m in ctx.adapter.modules]
            return {"source": "live from the car", "obd": data, "modules": mods or "run a module scan for these"}
        if not ctx.car_id:
            return {"error": "No car selected and the adapter isn't connected."}
        return _latest_codes(ctx)

    def read_live(args):
        _require_perm(ctx, "read_data", ("always",))
        if not ctx.adapter_live():
            return {"error": "The adapter isn't connected, so live data isn't available."}
        pids = []
        for p in (args.get("pids") or [])[:12]:
            try:
                pids.append(int(str(p), 16))
            except ValueError:
                continue
        return ctx.adapter.read_pids(ctx.user_id, pids or [0x0C, 0x0D, 0x05, 0x42])

    def history(_args):
        _require_perm(ctx, "read_data", ("always",))
        if not ctx.car_id:
            return {"scans": []}
        rows = ctx.conn.execute("SELECT kind, odometer_km, created_at, data FROM scans WHERE car_id = ? AND "
                                "user_id = ? ORDER BY created_at DESC LIMIT 15", (ctx.car_id, ctx.user_id))
        out = []
        for r in rows:
            d = db.loads(r["data"], {})
            codes = [c["code"] for c in d.get("stored", []) + d.get("pending", [])] if r["kind"] == "obd" else \
                [x["full"] for m in d.get("modules", []) for x in m.get("dtcs", [])]
            out.append({"kind": r["kind"], "odometer_km": r["odometer_km"], "at": r["created_at"], "codes": codes})
        return {"scans": out}

    def remember(args):
        if not ctx.settings["memory_enabled"]:
            return {"saved": False, "reason": "Memory is turned off in Settings."}
        fact = str(args.get("fact", "")).strip()[:300]
        if not fact or not ctx.car_id:
            return {"saved": False}
        ctx.conn.execute("INSERT INTO memories (user_id, car_id, fact, created_at) VALUES (?,?,?,?)",
                         (ctx.user_id, ctx.car_id, fact, db.now()))
        ctx.conn.commit()
        return {"saved": True}

    def create_plan(args):
        _require_perm(ctx, "start_tests", ("ask",))
        plan = _validate_plan(args.get("plan", args))
        ctx.plan = plan.model_dump()
        ctx.plan_run_id = _save_run(ctx, plan, status="draft")
        return {"saved": True, "plan_id": ctx.plan_run_id,
                "note": "The user sees a card to start this plan. Don't repeat the steps in full."}

    def request_clear(args):
        _require_perm(ctx, "clear_codes", ("ask",))
        target = str(args.get("target", "obd"))
        cur = ctx.conn.execute(
            "INSERT INTO pending_actions (user_id, car_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
            (ctx.user_id, ctx.car_id, "clear_codes", db.dumps({"target": target, "reason": args.get("reason", "")}),
             db.now()))
        ctx.conn.commit()
        ctx.pending_ids.append(cur.lastrowid)
        return {"status": "waiting_for_user", "note": "The user must approve this in the app. Nothing has been "
                                                       "cleared yet; don't say it has."}

    tools = [
        Tool("read_fault_codes", "Read this car's fault codes: live from the adapter if connected, otherwise "
             "from the latest saved scan.", {"type": "object", "properties": {}}, read_codes, "Read codes"),
        Tool("read_live_data", "Read live OBD-II mode 01 values (PIDs as two hex digits, e.g. 0C rpm, 0D speed, "
             "05 coolant, 06/07 fuel trims, 42 voltage). Needs the adapter connected.",
             {"type": "object", "properties": {"pids": {"type": "array", "items": {"type": "string"}}},
              "required": ["pids"]}, read_live, "Read live data"),
        Tool("get_scan_history", "List this car's saved scans: dates, odometer and codes.",
             {"type": "object", "properties": {}}, history, "Checked history"),
        Tool("remember_fact", "Save a durable fact about this car (repairs done, parts replaced, recurring "
             "symptoms) for future conversations.",
             {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
             remember, "Remembered"),
    ]
    if include_plan_tool and ctx.settings["perms"].get("start_tests") == "ask":
        tools.append(Tool("create_guided_plan", "Create a step-by-step guided check the user can start in the "
                          "app.", {"type": "object", "properties": {"plan": PLAN_SCHEMA}, "required": ["plan"]},
                          create_plan, "Built a guided plan"))
    if include_actions and ctx.settings["perms"].get("clear_codes") == "ask":
        tools.append(Tool("request_clear_codes", "Ask the user to approve clearing codes. target is 'obd' for "
                          "engine/emission codes or a module request id like '760'.",
                          {"type": "object", "properties": {"target": {"type": "string"},
                                                            "reason": {"type": "string"}},
                           "required": ["target", "reason"]}, request_clear, "Asked to clear codes"))
    return tools


def _validate_plan(raw: dict, goal: str = "fix") -> plans_svc.Plan:
    data = dict(raw or {})
    data.setdefault("goal", goal)
    data["source"] = "ai"
    for i, s in enumerate(data.get("steps", []) or []):
        if isinstance(s, dict):
            s.setdefault("id", f"s{i + 1}")
            s["short"] = (s.get("short") or s.get("title", "Step"))[:40]
    try:
        return plans_svc.Plan.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"The plan didn't validate: {e.errors()[:3]}") from e


def _save_run(ctx: Context, plan: plans_svc.Plan, status: str) -> int:
    cur = ctx.conn.execute(
        "INSERT INTO runs (user_id, car_id, goal, plan, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (ctx.user_id, ctx.car_id, plan.goal, db.dumps(plan.model_dump()), status, db.now(), db.now()))
    ctx.conn.commit()
    return cur.lastrowid


# ------------------------------------------------------------------ chat
def chat(ctx: Context, provider: Provider, conversation_id: int, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Type a message.")
    if len(text) > 8000:
        raise ValueError("That message is too long.")
    conn = ctx.conn
    conn.execute("INSERT INTO messages (conversation_id, role, text, created_at) VALUES (?,?,?,?)",
                 (conversation_id, "user", text, db.now()))
    conn.commit()
    rows = conn.execute("SELECT role, text FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                        (conversation_id, HISTORY_LIMIT)).fetchall()[::-1]
    history = [Message(r["role"], r["text"]) for r in rows if r["text"]]
    while history and history[0].role != "user":
        history.pop(0)
    system = SCOPE + "\n\n## This session\n" + ctx.summary_text()
    tools = build_tools(ctx)
    web = ctx.settings["perms"].get("web_search") == "always"
    result = provider.run(system, history, tools, web_search=web, effort="medium")
    meta = {"tool_log": result.tool_log, "sources": result.sources, "provider": provider.name,
            "model": result.model or provider.model, "plan_run_id": ctx.plan_run_id, "plan": ctx.plan,
            "pending_action_ids": ctx.pending_ids}
    reply = result.text or "I couldn't produce an answer. Try asking in a different way."
    cur = conn.execute("INSERT INTO messages (conversation_id, role, text, meta, created_at) VALUES (?,?,?,?,?)",
                       (conversation_id, "assistant", reply, db.dumps(meta), db.now()))
    title_row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if title_row and title_row["title"] == "New conversation":
        conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (text[:60], conversation_id))
    conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (db.now(), conversation_id))
    conn.commit()
    used = sum(len(r["text"]) for r in rows) + len(reply)
    return {"id": cur.lastrowid, "role": "assistant", "text": reply, "meta": meta,
            "context_used": min(1.0, used / CONTEXT_CHAR_BUDGET)}


# ------------------------------------------------------------------ plan for a described problem
def generate_plan(ctx: Context, provider: Provider, problem: str, when: list[str]) -> dict:
    problem = (problem or "").strip()
    if len(problem) < 5:
        raise ValueError("Describe the problem in a few words.")
    captured: dict = {}

    def submit(args):
        plan = _validate_plan(args.get("plan", args))
        captured["plan"] = plan
        return {"saved": True}

    tools = [t for t in build_tools(ctx, include_plan_tool=False, include_actions=False)
             if t.name in ("read_fault_codes", "get_scan_history", "read_live_data")]
    tools.append(Tool("submit_plan", "Submit the causes and guided plan. Call exactly once.",
                      {"type": "object", "properties": {"plan": PLAN_SCHEMA}, "required": ["plan"]}, submit,
                      "Built your plan"))
    when_txt = ", ".join(w for w in when if isinstance(w, str))[:300]
    user = f"Problem: {problem[:2000]}\nWhen it happens: {when_txt or 'not specified'}"
    system = PLAN + "\n\n" + SCOPE + "\n\n## This session\n" + ctx.summary_text()
    web = ctx.settings["perms"].get("web_search") == "always"
    result = provider.run(system, [Message("user", user)], tools, web_search=web, effort="high", max_turns=14)
    if "plan" not in captured:
        raise AIError("The assistant didn't return a plan. Try describing the problem differently.")
    plan = captured["plan"]
    plan = plan.model_copy(update={"sources": result.sources[:12]})
    run_id = _save_run(ctx, plan, status="draft")
    return {"run_id": run_id, "plan": plan.model_dump(), "tool_log": result.tool_log, "sources": result.sources,
            "note": result.text}


# ------------------------------------------------------------------ photo inspection report
class Area(BaseModel):
    name: str
    rating: int = Field(ge=1, le=5)
    note: str = ""


class Finding(BaseModel):
    title: str
    kind: str = Field(pattern="^(repair_sign|damage|rust|leak|wear|warning_light|ok)$")
    confidence: str = Field(pattern="^(High|Medium|Low)$")
    slot: str = ""
    what: str
    next: str = ""


class Wear(BaseModel):
    label: str
    low_km: float = Field(ge=0)
    high_km: float = Field(ge=0)


class PhotoReport(BaseModel):
    headline: str
    summary: str
    areas: list[Area] = Field(default_factory=list, max_length=10)
    findings: list[Finding] = Field(default_factory=list, max_length=30)
    wear_estimates: list[Wear] = Field(default_factory=list, max_length=6)
    retakes: list[dict] = Field(default_factory=list, max_length=20)
    odometer_seen_km: float | None = None


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"}, "summary": {"type": "string"},
        "areas": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "rating": {"type": "integer", "minimum": 1, "maximum": 5},
            "note": {"type": "string"}}, "required": ["name", "rating"]}},
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"},
            "kind": {"type": "string", "enum": ["repair_sign", "damage", "rust", "leak", "wear", "warning_light", "ok"]},
            "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
            "slot": {"type": "string"}, "what": {"type": "string"}, "next": {"type": "string"}},
            "required": ["title", "kind", "confidence", "what"]}},
        "wear_estimates": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "low_km": {"type": "number"}, "high_km": {"type": "number"}},
            "required": ["label", "low_km", "high_km"]}},
        "retakes": {"type": "array", "items": {"type": "object", "properties": {
            "slot": {"type": "string"}, "reason": {"type": "string"}}, "required": ["slot", "reason"]}},
        "odometer_seen_km": {"type": "number"},
    },
    "required": ["headline", "summary", "areas", "findings"],
}


def analyze_photos(ctx: Context, provider: Provider, photos: list[dict], upload_root, odometer_km=None) -> dict:
    if not photos:
        raise ValueError("Add some photos first.")
    photos = photos[:24]
    images, labels = [], []
    for p in photos:
        path = photos_svc.path_for(upload_root, ctx.user_id, ctx.car_id, p["filename"])
        images.append(photos_svc.for_ai(path))
        slot = photos_svc.SLOTS.get(p["slot"], {"name": "Unsorted photo"})["name"]
        labels.append(f"Image {len(labels) + 1}: {slot} (slot {p['slot']})")
    cov = photos_svc.coverage(photos)
    captured: dict = {}

    def submit(args):
        captured["report"] = PhotoReport.model_validate(args)
        return {"saved": True}

    tool = Tool("submit_photo_report", "Submit the condition report. Call exactly once.", REPORT_SCHEMA, submit,
                "Wrote the report")
    text = ("\n".join(labels) + f"\n\nOdometer reading given by the user: "
            f"{f'{odometer_km:,.0f} km' if odometer_km else 'not given'}.\n"
            f"Slots not photographed: {', '.join(m['name'] for m in cov['missing']) or 'none'}.")
    system = PHOTOS + "\n\n## This session\n" + ctx.summary_text()
    provider.run(system, [Message("user", text, images)], [tool], web_search=False, effort="high", max_turns=4)
    if "report" not in captured:
        raise AIError("The assistant didn't return a report. Try again.")
    report = captured["report"].model_dump()
    report["missing"] = [m["name"] for m in cov["missing"]]
    report["photo_count"] = len(photos)
    ctx.conn.execute("INSERT INTO reports (user_id, car_id, kind, data, created_at) VALUES (?,?,?,?,?)",
                     (ctx.user_id, ctx.car_id, "photos", db.dumps(report), db.now()))
    ctx.conn.commit()
    return report
