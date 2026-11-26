"""Guided test plans: schema, built-in templates and run-state helpers.

A plan is a list of steps. Each step is one of:
  auto      the app reads data by itself (``capture`` PIDs, optional live ``target``)
  confirm   the user does something and taps "Done"
  question  the user picks one of ``choices``
Plans come from templates (inspection, health, ...) or are written by the AI for a
user-described problem; both go through the same validation.
"""
from __future__ import annotations

import re
import time
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PID_RE = re.compile(r"^[0-9A-F]{2}$")


class Target(BaseModel):
    pid: str
    min: float
    max: float
    hold_s: int = Field(default=10, ge=1, le=120)
    label: str = ""

    @field_validator("pid")
    @classmethod
    def _pid(cls, v):
        v = v.upper()
        if not PID_RE.match(v):
            raise ValueError("pid must be two hex digits")
        return v


class Step(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    phase: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=3, max_length=160)
    short: str = Field(min_length=1, max_length=40)
    why: str = Field(default="", max_length=400)
    how: list[str] = Field(default_factory=list, max_length=8)
    safety: str = Field(default="", max_length=300)
    kind: Literal["auto", "confirm", "question"]
    choices: list[str] = Field(default_factory=list, max_length=8)
    capture: list[str] = Field(default_factory=list, max_length=8)
    target: Target | None = None
    action: Literal["read_codes", "read_vehicle"] | None = None

    @field_validator("capture")
    @classmethod
    def _capture(cls, v):
        out = [p.upper() for p in v]
        for p in out:
            if not PID_RE.match(p):
                raise ValueError(f"bad PID {p!r}")
        return out

    def model_post_init(self, _ctx):
        if self.kind == "question" and len(self.choices) < 2:
            raise ValueError(f"step {self.id}: a question needs at least 2 choices")


class Cause(BaseModel):
    title: str = Field(max_length=120)
    fit: Literal["Most likely", "Possible", "Less likely"]
    why: str = Field(max_length=400)


class Plan(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    goal: str = Field(max_length=40)
    source: Literal["template", "ai"] = "template"
    summary: str = Field(default="", max_length=800)
    causes: list[Cause] = Field(default_factory=list, max_length=8)
    needs: list[str] = Field(default_factory=list, max_length=8)
    sources: list[dict] = Field(default_factory=list, max_length=12)
    steps: list[Step] = Field(min_length=1, max_length=40)

    @field_validator("steps")
    @classmethod
    def _unique_ids(cls, steps):
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        return steps


# ----------------------------------------------------------------- templates
_DRIVE_SAFETY = "Only drive where it's legal and safe. Keep your eyes on the road; the app reads automatically."


def _s(id, phase, short, title, kind, why="", how=(), safety="", choices=(), capture=(), target=None, action=None):
    return {"id": id, "phase": phase, "short": short, "title": title, "kind": kind, "why": why, "how": list(how),
            "safety": safety, "choices": list(choices), "capture": list(capture), "target": target, "action": action}


INSPECTION = {
    "title": "Used-car inspection", "goal": "inspection", "source": "template",
    "summary": "Every module, the dash warning lights, idle, load, charging and a short test drive.",
    "needs": ["About 25 minutes", "A safe, quiet road for the drive"],
    "steps": [
        _s("ign", "Ignition on, engine off", "Ignition on", "Turn the ignition on. Don't start the engine.", "auto",
           "Lets every module wake up and gives a resting battery reading.",
           ["Press start without the brake, or turn the key to ON", "Leave the engine off and close the doors"],
           capture=["42"], action="read_vehicle"),
        _s("codes", "Ignition on, engine off", "Read codes", "Reading current and past codes", "auto",
           "Collects active, pending and stored codes plus emissions readiness.", ["Nothing to do"],
           action="read_codes"),
        _s("bulbs", "Ignition on, engine off", "Dash warning lights",
           "Did every warning light come on, then go out?", "question",
           "A warning light that never lights at all may have been disconnected to hide a fault.",
           ["Watch the dash right after switching the ignition on",
            "Check engine, airbag, ABS and battery lights should all show briefly"],
           choices=["All came on, then went out", "Some stayed on", "One never came on"]),
        _s("start", "Engine running", "Start engine", "Start the engine", "auto",
           "Measures charging right after start. A weak battery struggles to start the engine.",
           ["Foot on the brake, start the engine", "Keep it in Park with the handbrake on"], capture=["42", "0C"]),
        _s("idle", "Engine running", "Idle 60 s", "Let it idle for one minute", "auto",
           "Checks idle stability, fuel trims and temperature with no load.",
           ["Don't touch the pedals", "Turn off the A/C and lights for now"],
           capture=["0C", "06", "07", "05"], target={"pid": "0C", "min": 500, "max": 1100, "hold_s": 60,
                                                     "label": "Steady idle"}),
        _s("rev", "Engine running", "Hold 2,500 rpm", "Hold the engine at 2,500 rpm", "auto",
           "Checks charging voltage, fuel trims and misfires when the engine works harder.",
           ["Stay in Park with the handbrake on",
            "Press the accelerator gently until the needle sits in the green band", "Hold it there"],
           safety="Keep the car in Park. Don't rev a cold engine past 3,000 rpm.",
           capture=["0C", "42", "06"], target={"pid": "0C", "min": 2300, "max": 2700, "hold_s": 10,
                                               "label": "2,300–2,700 rpm"}),
        _s("load", "Engine running", "Electrical load", "Switch on headlights, A/C, rear demister and blower",
           "confirm", "The alternator should hold above 13.5 V with everything on.",
           ["Headlights on full beam", "A/C on, blower at maximum", "Rear window demister on", "Wait 20 seconds"],
           capture=["42", "0C"]),
        _s("steer", "Engine running", "Steering lock to lock", "Turn the steering fully left, then fully right",
           "question", "Listen for whining or knocking from the steering.",
           ["Car stationary, engine running", "Turn slowly to full lock each way, twice"],
           choices=["Quiet", "Whine", "Knock or clunk"]),
        _s("pull", "Test drive", "Pull away to 30 mph", "Drive gently up to 30 mph (50 km/h)", "auto",
           "Watches the first gear changes and whether the transmission slips.",
           ["Pull away normally on a quiet road", "Let the gearbox change up by itself"],
           safety=_DRIVE_SAFETY, capture=["0D", "0C"],
           target={"pid": "0D", "min": 45, "max": 60, "hold_s": 5, "label": "45–60 km/h"}),
        _s("accel", "Test drive", "Firm acceleration", "Accelerate firmly from 20 to 50 mph (30–80 km/h)", "confirm",
           "Misfires and turbo problems often only show under load.",
           ["On a clear straight road, press firmly (not flat out)", "Ease off at 50 mph"],
           safety=_DRIVE_SAFETY, capture=["0C", "0D", "04"]),
        _s("brake", "Test drive", "Firm braking", "Brake firmly from 30 mph (50 km/h)", "question",
           "Pulling to one side or pulsing in the pedal points to brake or ABS problems.",
           ["Check nobody is behind you", "Brake firmly to a stop"], safety="Only on an empty road.",
           choices=["Straight and smooth", "Pulls to one side", "Pedal pulses or grinds"]),
        _s("cruise", "Test drive", "Cruise 55 mph", "Cruise steadily at 55 mph (90 km/h) for a minute", "auto",
           "Checks steady-state fuel trims and temperature.", ["Hold a steady speed; cruise control is fine"],
           safety=_DRIVE_SAFETY, capture=["0D", "06", "07", "05"],
           target={"pid": "0D", "min": 80, "max": 100, "hold_s": 60, "label": "80–100 km/h"}),
        _s("reread", "Finish", "Re-read codes", "Park, idle 30 seconds, and re-read codes", "auto",
           "Anything new that appeared during the drive shows up here.", ["Park safely; leave the engine running"],
           action="read_codes"),
    ],
}

HEALTH = {
    "title": "Routine health check", "goal": "health", "source": "template",
    "summary": "Codes, battery and charging, idle quality.", "needs": ["About 5 minutes"],
    "steps": [INSPECTION["steps"][0], INSPECTION["steps"][1], INSPECTION["steps"][3], INSPECTION["steps"][4],
              INSPECTION["steps"][6]],
}

EMISSIONS = {
    "title": "Emissions readiness", "goal": "emissions", "source": "template",
    "summary": "Check-engine status and readiness monitors.", "needs": ["About 1 minute"],
    "steps": [INSPECTION["steps"][0],
              _s("ready", "Readiness", "Readiness monitors", "Reading readiness monitors", "auto",
                 "Monitors that haven't finished will fail most emissions tests.", ["Nothing to do"],
                 action="read_codes")],
}

DELIVERY = {
    "title": "New-car delivery check", "goal": "delivery", "source": "template",
    "summary": "No stored faults, every module answering, dash lights working.", "needs": ["About 5 minutes"],
    "steps": [INSPECTION["steps"][0], INSPECTION["steps"][1], INSPECTION["steps"][2], INSPECTION["steps"][3]],
}

TEMPLATES = {p["goal"]: p for p in (INSPECTION, HEALTH, EMISSIONS, DELIVERY)}


def template(goal: str) -> Plan:
    if goal not in TEMPLATES:
        raise KeyError(goal)
    return Plan.model_validate(TEMPLATES[goal])


# ----------------------------------------------------------------- run state
def record(results: dict, step: Step, status: str, answer: str | None = None, readings=None) -> dict:
    if status not in ("done", "skipped"):
        raise ValueError("status must be done or skipped")
    if step.kind == "question" and status == "done":
        if answer not in step.choices:
            raise ValueError("Pick one of the answers.")
    results = dict(results)
    results[step.id] = {"status": status, "answer": answer, "readings": readings or [], "at": time.time()}
    return results


def summary(plan: Plan, results: dict) -> dict:
    done = sum(1 for s in plan.steps if results.get(s.id, {}).get("status") == "done")
    skipped = sum(1 for s in plan.steps if results.get(s.id, {}).get("status") == "skipped")
    nxt = next((i for i, s in enumerate(plan.steps) if s.id not in results), None)
    return {"done": done, "skipped": skipped, "total": len(plan.steps), "next_index": nxt,
            "finished": nxt is None}


def in_target(target: Target | None, values: list[dict]) -> bool | None:
    if not target:
        return None
    for v in values:
        if v.get("pid") == target.pid and isinstance(v.get("value"), (int, float)):
            return target.min <= v["value"] <= target.max
    return None
