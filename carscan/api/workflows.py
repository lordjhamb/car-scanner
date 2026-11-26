"""Guided runs, photo inspection and the AI assistant."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import db
from ..ai import assistant
from ..ai.assistant import Context
from ..services import photos as photos_svc
from ..services import plans as plans_svc
from .deps import current_user, get_db, owned_car

router = APIRouter(prefix="/api", tags=["workflows"])


def _ctx(request: Request, conn, user: dict, car_id: int | None) -> Context:
    s = assistant.load_settings(conn, request.app.state.box, user["id"], request.app.state.settings)
    return Context(conn, user["id"], car_id, request.app.state.adapter, s)


def _provider(request: Request, ctx: Context, override: str | None = None):
    return assistant.make_provider(ctx.settings, request.app.state.provider_factory, override)


# ------------------------------------------------------------------ runs
def _run(conn, user_id: int, run_id: int) -> dict:
    r = conn.execute("SELECT * FROM runs WHERE id = ? AND user_id = ?", (run_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "Run not found.")
    r = dict(r)
    plan = plans_svc.Plan.model_validate(db.loads(r["plan"], {}))
    results = db.loads(r["results"], {})
    return {**r, "plan": plan, "results": results}


def _run_out(run: dict) -> dict:
    return {"id": run["id"], "car_id": run["car_id"], "goal": run["goal"], "status": run["status"],
            "plan": run["plan"].model_dump(), "results": run["results"],
            "progress": plans_svc.summary(run["plan"], run["results"]), "created_at": run["created_at"]}


@router.get("/plans/templates")
def templates(user=Depends(current_user)):
    return {"templates": [{"goal": g, "title": p["title"], "summary": p["summary"], "steps": len(p["steps"]),
                           "needs": p["needs"]} for g, p in plans_svc.TEMPLATES.items()]}


class RunIn(BaseModel):
    goal: str = Field(pattern="^(inspection|health|emissions|delivery)$")
    car_id: int | None = None


@router.post("/runs")
def create_run(body: RunIn, user=Depends(current_user), conn=Depends(get_db)):
    if body.car_id:
        owned_car(conn, user["id"], body.car_id)
    plan = plans_svc.template(body.goal)
    cur = conn.execute("INSERT INTO runs (user_id, car_id, goal, plan, status, created_at, updated_at) VALUES "
                       "(?,?,?,?,?,?,?)", (user["id"], body.car_id, body.goal, db.dumps(plan.model_dump()),
                                           "running", db.now(), db.now()))
    conn.commit()
    return _run_out(_run(conn, user["id"], cur.lastrowid))


class ProblemIn(BaseModel):
    car_id: int | None = None
    problem: str = Field(min_length=5, max_length=2000)
    when: list[str] = Field(default_factory=list, max_length=12)
    provider: str | None = Field(default=None, pattern="^(claude|openai)$")


@router.post("/runs/ai")
def create_ai_run(body: ProblemIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    if body.car_id:
        owned_car(conn, user["id"], body.car_id)
    ctx = _ctx(request, conn, user, body.car_id)
    out = assistant.generate_plan(ctx, _provider(request, ctx, body.provider), body.problem, body.when)
    run = _run(conn, user["id"], out["run_id"])
    return {**_run_out(run), "tool_log": out["tool_log"], "sources": out["sources"], "note": out["note"]}


@router.get("/runs")
def list_runs(car_id: int | None = None, user=Depends(current_user), conn=Depends(get_db)):
    q, args = "SELECT id FROM runs WHERE user_id = ?", [user["id"]]
    if car_id:
        q += " AND car_id = ?"
        args.append(car_id)
    ids = [r["id"] for r in conn.execute(q + " ORDER BY created_at DESC LIMIT 50", args)]
    return {"runs": [_run_out(_run(conn, user["id"], i)) for i in ids]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, user=Depends(current_user), conn=Depends(get_db)):
    return _run_out(_run(conn, user["id"], run_id))


class PlanEdit(BaseModel):
    skip_steps: list[str] = Field(default_factory=list, max_length=40)


@router.post("/runs/{run_id}/start")
def start_run(run_id: int, body: PlanEdit, user=Depends(current_user), conn=Depends(get_db)):
    run = _run(conn, user["id"], run_id)
    plan = run["plan"]
    steps = [s for s in plan.steps if s.id not in set(body.skip_steps)]
    if not steps:
        raise HTTPException(422, "Keep at least one step.")
    plan = plan.model_copy(update={"steps": steps})
    conn.execute("UPDATE runs SET plan = ?, status = 'running', updated_at = ? WHERE id = ?",
                 (db.dumps(plan.model_dump()), db.now(), run_id))
    conn.commit()
    return _run_out(_run(conn, user["id"], run_id))


def _step(run: dict, step_id: str) -> plans_svc.Step:
    for s in run["plan"].steps:
        if s.id == step_id:
            return s
    raise HTTPException(404, "Step not found.")


@router.get("/runs/{run_id}/steps/{step_id}/sample")
def sample_step(run_id: int, step_id: str, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    run = _run(conn, user["id"], run_id)
    step = _step(run, step_id)
    a = request.app.state.adapter
    if a.elm is None or a.owner != user["id"]:
        return {"connected": False, "values": [], "in_target": None}
    pids = [int(p, 16) for p in step.capture]
    if step.target and int(step.target.pid, 16) not in pids:
        pids.append(int(step.target.pid, 16))
    data = a.read_pids(user["id"], pids) if pids else {"values": [], "voltage": a.elm.voltage()}
    return {"connected": True, **data, "in_target": plans_svc.in_target(step.target, data["values"])}


class StepResult(BaseModel):
    status: str = Field(pattern="^(done|skipped)$")
    answer: str | None = Field(default=None, max_length=200)


@router.post("/runs/{run_id}/steps/{step_id}")
def complete_step(run_id: int, step_id: str, body: StepResult, request: Request, user=Depends(current_user),
                  conn=Depends(get_db)):
    run = _run(conn, user["id"], run_id)
    step = _step(run, step_id)
    readings, extra = [], {}
    a = request.app.state.adapter
    live = a.elm is not None and a.owner == user["id"]
    if body.status == "done" and live:
        if step.capture:
            readings = a.read_pids(user["id"], [int(p, 16) for p in step.capture])["values"]
        if step.action == "read_codes":
            extra = a.obd_codes(user["id"])
        elif step.action == "read_vehicle":
            extra = {"voltage": a.elm.voltage()}
    try:
        results = plans_svc.record(run["results"], step, body.status, body.answer, readings)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if extra:
        results[step.id]["data"] = extra
    prog = plans_svc.summary(run["plan"], results)
    status = "finished" if prog["finished"] else "running"
    conn.execute("UPDATE runs SET results = ?, status = ?, updated_at = ? WHERE id = ?",
                 (db.dumps(results), status, db.now(), run_id))
    conn.commit()
    return _run_out(_run(conn, user["id"], run_id))


# ------------------------------------------------------------------ photos
@router.get("/photos/slots")
def slots(user=Depends(current_user)):
    return {"slots": list(photos_svc.SLOTS.values())}


def _photos(conn, car_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, slot, filename, size, width, height, created_at FROM photos "
                                          "WHERE car_id = ? ORDER BY id", (car_id,))]


def _photo_out(p: dict, car_id: int) -> dict:
    return {k: v for k, v in p.items() if k != "filename"} | {"url": f"/api/cars/{car_id}/photos/{p['id']}/file"}


@router.get("/cars/{car_id}/photos")
def list_photos(car_id: int, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    ph = _photos(conn, car_id)
    return {"photos": [_photo_out(p, car_id) for p in ph], "coverage": photos_svc.coverage(ph)}


@router.post("/cars/{car_id}/photos")
async def upload_photo(car_id: int, request: Request, slot: str = Form(...), file: UploadFile = File(...),
                       user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    if slot not in photos_svc.SLOTS and slot != photos_svc.UNSORTED:
        raise HTTPException(422, "Unknown photo slot.")
    s = request.app.state.settings
    raw = await file.read(s.max_upload_mb * 1024 * 1024 + 1)
    try:
        data, w, h = photos_svc.process_upload(raw, s.max_upload_mb * 1024 * 1024)
    except photos_svc.PhotoError as e:
        raise HTTPException(422, str(e)) from e
    if slot != photos_svc.UNSORTED:   # one photo per slot: replace the old one
        for old in conn.execute("SELECT id, filename FROM photos WHERE car_id = ? AND slot = ?", (car_id, slot)):
            photos_svc.path_for(s.upload_dir, user["id"], car_id, old["filename"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM photos WHERE car_id = ? AND slot = ?", (car_id, slot))
    name = photos_svc.save(s.upload_dir, user["id"], car_id, data)
    cur = conn.execute("INSERT INTO photos (user_id, car_id, slot, filename, content_type, size, width, height, "
                       "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (user["id"], car_id, slot, name, "image/jpeg", len(data), w, h, db.now()))
    conn.commit()
    p = dict(conn.execute("SELECT id, slot, filename, size, width, height, created_at FROM photos WHERE id = ?",
                          (cur.lastrowid,)).fetchone())
    return {"photo": _photo_out(p, car_id), "coverage": photos_svc.coverage(_photos(conn, car_id))}


@router.get("/cars/{car_id}/photos/{photo_id}/file")
def photo_file(car_id: int, photo_id: int, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    r = conn.execute("SELECT filename FROM photos WHERE id = ? AND car_id = ?", (photo_id, car_id)).fetchone()
    if not r:
        raise HTTPException(404, "Photo not found.")
    path = photos_svc.path_for(request.app.state.settings.upload_dir, user["id"], car_id, r["filename"])
    if not path.exists():
        raise HTTPException(404, "Photo file is missing.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/cars/{car_id}/photos/{photo_id}")
def delete_photo(car_id: int, photo_id: int, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    r = conn.execute("SELECT filename FROM photos WHERE id = ? AND car_id = ?", (photo_id, car_id)).fetchone()
    if not r:
        raise HTTPException(404, "Photo not found.")
    photos_svc.path_for(request.app.state.settings.upload_dir, user["id"], car_id, r["filename"]).unlink(
        missing_ok=True)
    conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    conn.commit()
    return {"ok": True}


class AnalyzeIn(BaseModel):
    odometer_km: float | None = Field(default=None, ge=0)
    provider: str | None = Field(default=None, pattern="^(claude|openai)$")


@router.post("/cars/{car_id}/photos/analyze")
def analyze(car_id: int, body: AnalyzeIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    ph = _photos(conn, car_id)
    ctx = _ctx(request, conn, user, car_id)
    return assistant.analyze_photos(ctx, _provider(request, ctx, body.provider), ph,
                                    request.app.state.settings.upload_dir, body.odometer_km)


@router.get("/cars/{car_id}/photos/report")
def photo_report(car_id: int, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    r = conn.execute("SELECT data, created_at FROM reports WHERE car_id = ? AND kind = 'photos' ORDER BY "
                     "created_at DESC LIMIT 1", (car_id,)).fetchone()
    return {"report": {**db.loads(r["data"], {}), "created_at": r["created_at"]} if r else None}


# ------------------------------------------------------------------ AI settings
class AISettingsIn(BaseModel):
    provider: str | None = None
    claude_model: str | None = None
    openai_model: str | None = None
    anthropic_key: str | None = None
    openai_key: str | None = None
    perms: dict | None = None
    memory_enabled: bool | None = None
    hide_vin: bool | None = None
    monthly_limit_usd: float | None = None


@router.get("/ai/settings")
def get_ai_settings(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    s = assistant.load_settings(conn, request.app.state.box, user["id"], request.app.state.settings)
    return assistant.public_settings(s)


@router.put("/ai/settings")
def put_ai_settings(body: AISettingsIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    try:
        assistant.save_settings(conn, request.app.state.box, user["id"], body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return get_ai_settings(request, user, conn)


class TestIn(BaseModel):
    provider: str = Field(pattern="^(claude|openai)$")


@router.post("/ai/test")
def test_provider(body: TestIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    ctx = _ctx(request, conn, user, None)
    provider = _provider(request, ctx, body.provider)
    res = provider.run("Reply with the single word OK.", [assistant.Message("user", "ping")], [],
                       web_search=False, effort="low", max_turns=1)
    return {"ok": True, "model": res.model or provider.model}


# ------------------------------------------------------------------ conversations
class ConvIn(BaseModel):
    car_id: int | None = None


class MsgIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    provider: str | None = Field(default=None, pattern="^(claude|openai)$")


def _conv(conn, user_id: int, conv_id: int) -> dict:
    r = conn.execute("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)).fetchone()
    if not r:
        raise HTTPException(404, "Conversation not found.")
    return dict(r)


@router.get("/conversations")
def list_conversations(car_id: int | None = None, user=Depends(current_user), conn=Depends(get_db)):
    q, args = "SELECT * FROM conversations WHERE user_id = ?", [user["id"]]
    if car_id:
        q += " AND car_id = ?"
        args.append(car_id)
    return {"conversations": [dict(r) for r in conn.execute(q + " ORDER BY updated_at DESC LIMIT 50", args)]}


@router.post("/conversations")
def new_conversation(body: ConvIn, user=Depends(current_user), conn=Depends(get_db)):
    if body.car_id:
        owned_car(conn, user["id"], body.car_id)
    cur = conn.execute("INSERT INTO conversations (user_id, car_id, created_at, updated_at) VALUES (?,?,?,?)",
                       (user["id"], body.car_id, db.now(), db.now()))
    conn.commit()
    return _conv(conn, user["id"], cur.lastrowid)


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: int, user=Depends(current_user), conn=Depends(get_db)):
    c = _conv(conn, user["id"], conv_id)
    msgs = [{**dict(r), "meta": db.loads(r["meta"], {})} for r in conn.execute(
        "SELECT id, role, text, meta, created_at FROM messages WHERE conversation_id = ? ORDER BY id", (conv_id,))]
    return {**c, "messages": msgs}


@router.post("/conversations/{conv_id}/messages")
def send_message(conv_id: int, body: MsgIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    c = _conv(conn, user["id"], conv_id)
    ctx = _ctx(request, conn, user, c["car_id"])
    provider = _provider(request, ctx, body.provider)
    return assistant.chat(ctx, provider, conv_id, body.text)


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int, user=Depends(current_user), conn=Depends(get_db)):
    _conv(conn, user["id"], conv_id)
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    return {"ok": True}


@router.get("/cars/{car_id}/memories")
def memories(car_id: int, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    return {"memories": [dict(r) for r in conn.execute(
        "SELECT id, fact, created_at FROM memories WHERE car_id = ? AND user_id = ? ORDER BY id",
        (car_id, user["id"]))]}


@router.delete("/memories/{mem_id}")
def delete_memory(mem_id: int, user=Depends(current_user), conn=Depends(get_db)):
    conn.execute("DELETE FROM memories WHERE id = ? AND user_id = ?", (mem_id, user["id"]))
    conn.commit()
    return {"ok": True}


@router.delete("/memories")
def delete_all_memories(user=Depends(current_user), conn=Depends(get_db)):
    conn.execute("DELETE FROM memories WHERE user_id = ?", (user["id"],))
    conn.commit()
    return {"ok": True}
