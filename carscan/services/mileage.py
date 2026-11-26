"""Odometer verification.

Compares the reading the user typed in against every independent source of
mileage we can collect, and turns that into a 0-100 confidence score.

Rule of thumb that drives the strongest evidence: a module cannot record a fault
at a mileage the car has not reached yet. Any stored mileage *above* today's
reading is evidence that the reading was lowered.

All distances are kilometres internally.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

WEIGHT_POINTS = {"strong": (20, 45), "medium": (10, 20), "weak": (5, 8), "context": (0, 0)}
KM_PER_MI = 1.609344


@dataclass
class Evidence:
    key: str
    check: str
    source: str
    finding: str
    weight: str        # strong | medium | weak | context
    result: str        # agrees | conflicts | neutral | note

    def as_dict(self):
        return asdict(self)


def tolerance(km: float) -> float:
    """Allowed gap between modules that save their mileage at different times."""
    return max(80.0, km * 0.004)


def _fmt(km: float, unit: str) -> str:
    v = km / KM_PER_MI if unit == "mi" else km
    return f"{v:,.0f} {unit}"


def evaluate(entered_km: float, *, module_readings=(), fault_stamps=(), engine_hours=None,
             past_readings=(), service_records=(), wear_estimates=(), vehicle_year=None,
             current_year=None, codes_cleared_km=None, unit: str = "km") -> dict:
    """Score how likely ``entered_km`` is the car's true mileage.

    module_readings  [{"name", "km"}]            odometer stored in other modules
    fault_stamps     [{"code", "module_name", "km"}] mileage recorded with past faults
    engine_hours     float | None                total engine run time
    past_readings    [{"date", "km"}]            this app's earlier scans of the car
    service_records  [{"date", "km", "note"}]    stickers, invoices, inspections
    wear_estimates   [{"label", "low_km", "high_km"}] from photo analysis (tyres, interior)
    """
    if entered_km is None or entered_km < 0:
        raise ValueError("Enter the odometer reading.")
    ev: list[Evidence] = []
    tol = tolerance(entered_km)
    higher: list[float] = []

    # 1. other modules -------------------------------------------------------
    if module_readings:
        over = [r for r in module_readings if r["km"] > entered_km + tol]
        gap = max(abs(r["km"] - entered_km) for r in module_readings)
        names = ", ".join(sorted({r["name"] for r in module_readings}))
        if over:
            top = max(r["km"] for r in over)
            higher.append(top)
            ev.append(Evidence("modules", "Other modules", names,
                               f"{len(over)} module(s) remember up to {_fmt(top, unit)}, "
                               f"{_fmt(top - entered_km, unit)} more than the dashboard.", "strong", "conflicts"))
        else:
            ev.append(Evidence("modules", "Other modules", names,
                               f"Within {_fmt(gap, unit)} of the dashboard. Small gaps are normal; "
                               "modules save at different times.", "strong", "agrees"))

    # 2. mileage stamped on fault codes --------------------------------------
    if fault_stamps:
        over = [s for s in fault_stamps if s["km"] > entered_km + tol]
        lo, hi = min(s["km"] for s in fault_stamps), max(s["km"] for s in fault_stamps)
        if over:
            higher.append(max(s["km"] for s in over))
            ev.append(Evidence("stamps", "Fault-code stamps", f"{len(fault_stamps)} past codes",
                               f"{len(over)} code(s) were stamped above today's reading "
                               f"(up to {_fmt(max(s['km'] for s in over), unit)}). "
                               "A fault can't be recorded at a mileage the car hasn't reached.",
                               "strong", "conflicts"))
        else:
            ev.append(Evidence("stamps", "Fault-code stamps", f"{len(fault_stamps)} past codes",
                               f"All stamped {_fmt(lo, unit)}–{_fmt(hi, unit)}: below today's reading.",
                               "strong", "agrees"))

    # 3. engine hours --------------------------------------------------------
    if engine_hours and engine_hours > 50:
        avg = entered_km / engine_hours
        if avg < 18:
            ev.append(Evidence("hours", "Engine run time", "engine module",
                               f"{engine_hours:,.0f} h means an average of {avg:.0f} km/h, "
                               "unusually low: the engine has run longer than this mileage suggests.",
                               "medium", "conflicts"))
        elif avg > 110:
            ev.append(Evidence("hours", "Engine run time", "engine module",
                               f"{engine_hours:,.0f} h means an average of {avg:.0f} km/h, unusually high.",
                               "medium", "note"))
        else:
            ev.append(Evidence("hours", "Engine run time", "engine module",
                               f"{engine_hours:,.0f} h, an average of {avg:.0f} km/h. Normal mixed driving.",
                               "medium", "agrees"))

    # 4. this app's earlier scans and 5. service records ---------------------
    for key, label, items in (("history", "Earlier scans in this app", past_readings),
                              ("service", "Service records", service_records)):
        if not items:
            continue
        over = [r for r in items if r["km"] > entered_km + tol]
        latest = max(items, key=lambda r: (str(r.get("date", "")), r["km"]))
        if over:
            top = max(r["km"] for r in over)
            higher.append(top)
            ev.append(Evidence(key, label, f"{len(items)} record(s)",
                               f"A record from {max(over, key=lambda r: r['km']).get('date', 'earlier')} shows "
                               f"{_fmt(top, unit)}, higher than today.", "strong" if key == "history" else "medium",
                               "conflicts"))
        else:
            ev.append(Evidence(key, label, f"{len(items)} record(s)",
                               f"Latest: {_fmt(latest['km'], unit)} on {latest.get('date', 'unknown date')}. "
                               "Consistent with today.", "medium", "agrees"))

    # 6. wear seen in photos -------------------------------------------------
    for w in wear_estimates:
        if entered_km < w["low_km"] * 0.7:
            ev.append(Evidence("wear:" + w["label"], w["label"], "photos",
                               f"Wear looks like {_fmt(w['low_km'], unit)}–{_fmt(w['high_km'], unit)}, "
                               "more than the reading.", "weak", "conflicts"))
        elif entered_km > w["high_km"] * 1.5:
            ev.append(Evidence("wear:" + w["label"], w["label"], "photos",
                               "Less wear than this mileage would suggest.", "weak", "note"))
        else:
            ev.append(Evidence("wear:" + w["label"], w["label"], "photos",
                               f"Fits {_fmt(w['low_km'], unit)}–{_fmt(w['high_km'], unit)}.", "weak", "agrees"))

    # 7. context -------------------------------------------------------------
    if vehicle_year and current_year and current_year >= vehicle_year:
        age = max(1, current_year - vehicle_year)
        per_year = entered_km / age
        if per_year < 6000:
            ev.append(Evidence("age", "Mileage for its age", f"{age} years old",
                               f"About {_fmt(per_year, unit)} a year: low. Common, but worth noting.",
                               "context", "note"))
        else:
            ev.append(Evidence("age", "Mileage for its age", f"{age} years old",
                               f"About {_fmt(per_year, unit)} a year.", "context", "neutral"))
    if codes_cleared_km is not None:
        ev.append(Evidence("cleared", "Codes cleared", "readiness",
                           f"{_fmt(codes_cleared_km, unit)} ago. Clearing removes recent fault stamps.",
                           "context", "note"))

    return score(ev, entered_km, higher, unit)


def score(ev: list[Evidence], entered_km: float, higher: list[float], unit: str) -> dict:
    scored = [e for e in ev if e.weight != "context" and e.result in ("agrees", "conflicts")]
    if not scored:
        return {"score": None, "band": "Not enough evidence", "verdict": "unknown",
                "headline": "Not enough evidence to check the mileage",
                "evidence": [e.as_dict() for e in ev], "estimated_km": None, "missing_km": None}
    pts = 50
    for e in scored:
        plus, minus = WEIGHT_POINTS[e.weight]
        pts += plus if e.result == "agrees" else -minus
    strong_conflict = any(e.weight == "strong" and e.result == "conflicts" for e in scored)
    if strong_conflict:
        pts = min(pts, 25)
    pts = max(0, min(100, pts))
    band = "Low" if pts < 40 else "Medium" if pts < 70 else "High"
    estimated = max(higher) if higher else None
    if band == "High":
        verdict, headline = "genuine", "The mileage looks genuine"
    elif strong_conflict:
        verdict, headline = "rolled_back", "The mileage has probably been rolled back"
    else:
        verdict, headline = "uncertain", "The mileage can't be fully confirmed"
    return {
        "score": pts, "band": band, "verdict": verdict, "headline": headline,
        "evidence": [e.as_dict() for e in ev],
        "estimated_km": estimated,
        "missing_km": round(estimated - entered_km) if estimated else None,
        "missing_display": _fmt(estimated - entered_km, unit) if estimated else None,
    }
