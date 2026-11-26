"""Adapter control, saved cars, scans, clearing codes, mileage verification."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db
from ..obd import list_ports
from ..services import mileage as mileage_svc
from ..services import vin as vin_svc
from .deps import current_user, get_db, owned_car

router = APIRouter(prefix="/api", tags=["garage"])


# ------------------------------------------------------------------ adapter
class Connect(BaseModel):
    port: str | None = Field(default=None, max_length=200)
    baud: int | None = None
    simulate: bool = False
    scenario: str | None = Field(default=None, pattern="^(genuine|rollback)$")


@router.get("/adapter/ports")
def ports(user=Depends(current_user)):
    return {"ports": list_ports()}


@router.get("/adapter/status")
def adapter_status(request: Request, user=Depends(current_user)):
    return request.app.state.adapter.status(user["id"])


@router.post("/adapter/connect")
def adapter_connect(body: Connect, request: Request, user=Depends(current_user)):
    info = request.app.state.adapter.connect(user["id"], body.port, body.baud, body.simulate, body.scenario)
    if info.get("vin"):
        info["vin_decoded"] = vin_svc.decode(info["vin"])
    return info


@router.post("/adapter/disconnect")
def adapter_disconnect(request: Request, user=Depends(current_user)):
    request.app.state.adapter.disconnect(user["id"])
    return {"ok": True}


@router.get("/adapter/live")
def adapter_live(request: Request, user=Depends(current_user)):
    return request.app.state.adapter.live(user["id"])


@router.get("/adapter/vehicle")
def adapter_vehicle(request: Request, user=Depends(current_user)):
    return request.app.state.adapter.vehicle(user["id"])


@router.get("/adapter/log")
def adapter_log(request: Request, user=Depends(current_user)):
    a = request.app.state.adapter
    a.check(user["id"])
    return {"lines": list(a.log)[-200:]}


@router.get("/adapter/scan")
def scan_status(request: Request, user=Depends(current_user)):
    return request.app.state.adapter.scan_result(user["id"])


@router.post("/adapter/scan/cancel")
def scan_cancel(request: Request, user=Depends(current_user)):
    request.app.state.adapter.cancel_scan(user["id"])
    return {"ok": True}


@router.get("/vin/{vin}")
def decode_vin(vin: str, user=Depends(current_user)):
    return vin_svc.decode(vin)


# ------------------------------------------------------------------ cars
class CarIn(BaseModel):
    vin: str = Field(min_length=11, max_length=20)
    make: str = Field(default="", max_length=60)
    model: str = Field(default="", max_length=60)
    year: int | None = Field(default=None, ge=1950, le=2100)
    engine: str = Field(default="", max_length=80)
    nickname: str = Field(default="", max_length=60)


class CarPatch(BaseModel):
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    year: int | None = Field(default=None, ge=1950, le=2100)
    engine: str | None = Field(default=None, max_length=80)
    nickname: str | None = Field(default=None, max_length=60)


def _car_summary(conn, car: dict) -> dict:
    last = conn.execute("SELECT created_at, odometer_km FROM scans WHERE car_id = ? ORDER BY created_at DESC "
                        "LIMIT 1", (car["id"],)).fetchone()
    n = conn.execute("SELECT COUNT(*) FROM scans WHERE car_id = ?", (car["id"],)).fetchone()[0]
    return {**car, "scan_count": n, "last_scan_at": last["created_at"] if last else None,
            "last_odometer_km": last["odometer_km"] if last else None}


@router.get("/cars")
def list_cars(user=Depends(current_user), conn=Depends(get_db)):
    cars = [dict(r) for r in conn.execute("SELECT * FROM cars WHERE user_id = ? ORDER BY updated_at DESC",
                                          (user["id"],))]
    return {"cars": [_car_summary(conn, c) for c in cars]}


@router.post("/cars")
def upsert_car(body: CarIn, user=Depends(current_user), conn=Depends(get_db)):
    vin = vin_svc.normalise(body.vin)
    if len(vin) == 17 and not vin_svc.is_valid_format(vin):
        raise HTTPException(422, "That VIN isn't valid.")
    decoded = vin_svc.decode(vin) if len(vin) == 17 else {}
    make = body.make or decoded.get("make", "")
    year = body.year or decoded.get("year")
    existing = conn.execute("SELECT id FROM cars WHERE user_id = ? AND vin = ?", (user["id"], vin)).fetchone()
    if existing:
        conn.execute("UPDATE cars SET make = COALESCE(NULLIF(?,''), make), model = COALESCE(NULLIF(?,''), model), "
                     "year = COALESCE(?, year), engine = COALESCE(NULLIF(?,''), engine), "
                     "nickname = COALESCE(NULLIF(?,''), nickname), updated_at = ? WHERE id = ?",
                     (make, body.model, year, body.engine, body.nickname, db.now(), existing["id"]))
        car_id = existing["id"]
    else:
        cur = conn.execute("INSERT INTO cars (user_id, vin, make, model, year, engine, nickname, created_at, "
                           "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (user["id"], vin, make, body.model, year, body.engine, body.nickname, db.now(), db.now()))
        car_id = cur.lastrowid
    conn.commit()
    return {"car": _car_summary(conn, owned_car(conn, user["id"], car_id))}


@router.get("/cars/{car_id}")
def get_car(car_id: int, user=Depends(current_user), conn=Depends(get_db)):
    car = owned_car(conn, user["id"], car_id)
    scans = [{**dict(r), "data": db.loads(r["data"], {})} for r in conn.execute(
        "SELECT id, kind, odometer_km, created_at, data FROM scans WHERE car_id = ? ORDER BY created_at DESC "
        "LIMIT 50", (car_id,))]
    reports = {}
    for kind in ("mileage", "photos"):
        r = conn.execute("SELECT data, created_at FROM reports WHERE car_id = ? AND kind = ? ORDER BY created_at "
                         "DESC LIMIT 1", (car_id, kind)).fetchone()
        reports[kind] = {**db.loads(r["data"], {}), "created_at": r["created_at"]} if r else None
    runs = [dict(r) for r in conn.execute("SELECT id, goal, status, created_at FROM runs WHERE car_id = ? "
                                          "ORDER BY created_at DESC LIMIT 20", (car_id,))]
    return {"car": _car_summary(conn, car), "vin": vin_svc.decode(car["vin"]), "scans": scans,
            "reports": reports, "runs": runs}


@router.patch("/cars/{car_id}")
def patch_car(car_id: int, body: CarPatch, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    changes = body.model_dump(exclude_none=True)
    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE cars SET {sets}, updated_at = ? WHERE id = ?", (*changes.values(), db.now(), car_id))
        conn.commit()
    return {"car": _car_summary(conn, owned_car(conn, user["id"], car_id))}


@router.delete("/cars/{car_id}")
def delete_car(car_id: int, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    conn.execute("DELETE FROM cars WHERE id = ?", (car_id,))
    conn.commit()
    import shutil
    shutil.rmtree(request.app.state.settings.upload_dir / str(user["id"]) / str(car_id), ignore_errors=True)
    return {"ok": True}


# ------------------------------------------------------------------ scans
class ObdScanIn(BaseModel):
    odometer_km: float | None = Field(default=None, ge=0, le=5_000_000)


@router.post("/cars/{car_id}/scans/obd")
def scan_obd(car_id: int, body: ObdScanIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    a = request.app.state.adapter
    codes = a.obd_codes(user["id"])
    vehicle = a.vehicle(user["id"])
    odo = body.odometer_km
    if odo is None:
        odo = next((s["value"] for s in vehicle["stats"] if s["pid"] == "A6"), None)
    data = {**codes, "vehicle": vehicle}
    cur = conn.execute("INSERT INTO scans (user_id, car_id, kind, odometer_km, data, created_at) VALUES "
                       "(?,?,?,?,?,?)", (user["id"], car_id, "obd", odo, db.dumps(data), db.now()))
    conn.execute("UPDATE cars SET updated_at = ? WHERE id = ?", (db.now(), car_id))
    conn.commit()
    return {"scan_id": cur.lastrowid, **data, "odometer_km": odo}


class ModuleScanIn(BaseModel):
    full: bool = True


@router.post("/cars/{car_id}/scans/modules")
def scan_modules(car_id: int, body: ModuleScanIn, request: Request, user=Depends(current_user),
                 conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    settings = request.app.state.settings
    uid = user["id"]

    def save(modules):
        c = db.connect(settings.db_path)
        try:
            c.execute("INSERT INTO scans (user_id, car_id, kind, odometer_km, data, created_at) VALUES "
                      "(?,?,?,?,?,?)", (uid, car_id, "modules", None, db.dumps({"modules": modules}), db.now()))
            c.commit()
        finally:
            c.close()
    return request.app.state.adapter.start_scan(uid, body.full, on_done=save)


@router.get("/cars/{car_id}/scans")
def list_scans(car_id: int, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    return {"scans": [{**dict(r), "data": db.loads(r["data"], {})} for r in conn.execute(
        "SELECT id, kind, odometer_km, created_at, data FROM scans WHERE car_id = ? ORDER BY created_at DESC",
        (car_id,))]}


# ------------------------------------------------------------------ clearing codes
class ClearIn(BaseModel):
    target: str = Field(default="obd", pattern=r"^(obd|[0-9A-Fa-f]{3}|18DA[0-9A-Fa-f]{2}F1)$")
    confirm: str = ""


def _do_clear(request: Request, user: dict, conn, car_id: int, target: str) -> dict:
    res = request.app.state.adapter.clear(user["id"], target)
    conn.execute("INSERT INTO scans (user_id, car_id, kind, odometer_km, data, created_at) VALUES (?,?,?,?,?,?)",
                 (user["id"], car_id, "clear", None, db.dumps({"target": target, "result": res}), db.now()))
    conn.commit()
    return res


@router.post("/cars/{car_id}/clear")
def clear_codes(car_id: int, body: ClearIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    if body.confirm != "CLEAR":
        raise HTTPException(422, "Type CLEAR to confirm.")
    return _do_clear(request, user, conn, car_id, body.target)


@router.get("/actions")
def list_actions(user=Depends(current_user), conn=Depends(get_db)):
    return {"actions": [{**dict(r), "payload": db.loads(r["payload"], {})} for r in conn.execute(
        "SELECT * FROM pending_actions WHERE user_id = ? AND status = 'pending' ORDER BY id DESC", (user["id"],))]}


class Decision(BaseModel):
    confirm: str = ""


@router.post("/actions/{action_id}/approve")
def approve_action(action_id: int, body: Decision, request: Request, user=Depends(current_user),
                   conn=Depends(get_db)):
    r = conn.execute("SELECT * FROM pending_actions WHERE id = ? AND user_id = ? AND status = 'pending'",
                     (action_id, user["id"])).fetchone()
    if not r:
        raise HTTPException(404, "No such pending action.")
    if body.confirm != "CLEAR":
        raise HTTPException(422, "Type CLEAR to confirm.")
    payload = db.loads(r["payload"], {})
    res = _do_clear(request, user, conn, r["car_id"], payload.get("target", "obd"))
    conn.execute("UPDATE pending_actions SET status = 'done' WHERE id = ?", (action_id,))
    conn.commit()
    return {"ok": True, "result": res}


@router.post("/actions/{action_id}/decline")
def decline_action(action_id: int, user=Depends(current_user), conn=Depends(get_db)):
    cur = conn.execute("UPDATE pending_actions SET status = 'declined' WHERE id = ? AND user_id = ? AND "
                       "status = 'pending'", (action_id, user["id"]))
    conn.commit()
    if not cur.rowcount:
        raise HTTPException(404, "No such pending action.")
    return {"ok": True}


# ------------------------------------------------------------------ service records
class ServiceIn(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    km: float = Field(ge=0, le=5_000_000)
    note: str = Field(default="", max_length=200)


@router.get("/cars/{car_id}/service-records")
def list_service(car_id: int, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    return {"records": [dict(r) for r in conn.execute(
        "SELECT id, date, km, note FROM service_records WHERE car_id = ? ORDER BY date", (car_id,))]}


@router.post("/cars/{car_id}/service-records")
def add_service(car_id: int, body: ServiceIn, user=Depends(current_user), conn=Depends(get_db)):
    owned_car(conn, user["id"], car_id)
    cur = conn.execute("INSERT INTO service_records (user_id, car_id, date, km, note, created_at) VALUES "
                       "(?,?,?,?,?,?)", (user["id"], car_id, body.date, body.km, body.note, db.now()))
    conn.commit()
    return {"id": cur.lastrowid}


# ------------------------------------------------------------------ mileage
class MileageIn(BaseModel):
    reading: float = Field(ge=0, le=3_000_000)
    unit: str = Field(default="mi", pattern="^(mi|km)$")
    use_car: bool = True


@router.post("/cars/{car_id}/mileage")
def check_mileage(car_id: int, body: MileageIn, request: Request, user=Depends(current_user),
                  conn=Depends(get_db)):
    car = owned_car(conn, user["id"], car_id)
    entered_km = body.reading * mileage_svc.KM_PER_MI if body.unit == "mi" else body.reading
    a = request.app.state.adapter
    live = {"module_readings": [], "fault_stamps": [], "engine_hours": None, "obd_odometer_km": None}
    if body.use_car and a.elm is not None and a.owner == user["id"]:
        live = a.mileage_evidence(user["id"])
    past = [{"date": dt.date.fromtimestamp(r["created_at"]).isoformat(), "km": r["odometer_km"]}
            for r in conn.execute("SELECT odometer_km, created_at FROM scans WHERE car_id = ? AND odometer_km IS "
                                  "NOT NULL AND created_at < ? ORDER BY created_at", (car_id, db.now() - 3600))]
    service = [dict(r) for r in conn.execute("SELECT date, km, note FROM service_records WHERE car_id = ?",
                                             (car_id,))]
    wear = []
    pr = conn.execute("SELECT data FROM reports WHERE car_id = ? AND kind = 'photos' ORDER BY created_at DESC "
                      "LIMIT 1", (car_id,)).fetchone()
    if pr:
        wear = db.loads(pr["data"], {}).get("wear_estimates", [])
    modules = [r for r in live["module_readings"] if "cluster" not in r["name"].lower()]
    result = mileage_svc.evaluate(
        entered_km, module_readings=modules, fault_stamps=live["fault_stamps"], engine_hours=live["engine_hours"],
        past_readings=past, service_records=service, wear_estimates=wear, vehicle_year=car["year"],
        current_year=dt.date.today().year, unit=body.unit)
    result.update({"entered": body.reading, "unit": body.unit, "entered_km": round(entered_km, 1),
                   "stamps": live["fault_stamps"], "module_readings": live["module_readings"],
                   "past_readings": past, "service_records": service,
                   "used_live_data": bool(live["module_readings"] or live["fault_stamps"] or live["engine_hours"])})
    conn.execute("INSERT INTO reports (user_id, car_id, kind, data, created_at) VALUES (?,?,?,?,?)",
                 (user["id"], car_id, "mileage", db.dumps(result), db.now()))
    conn.commit()
    return result
