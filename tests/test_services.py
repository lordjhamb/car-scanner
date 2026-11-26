"""VIN decoding, mileage scoring, guided plans, security primitives and photo handling."""
import io
import time

import pytest
from PIL import Image

from carscan.security import (
    RateLimiter,
    SecretBox,
    hash_password,
    mask_key,
    password_problem,
    valid_email,
    verify_password,
)
from carscan.services import mileage, photos, plans, vin


# ------------------------------------------------------------------ VIN
def test_vin_decode_demo_car():
    d = vin.decode("1FTFW1ET9DFC10312")
    assert d["valid"] and d["make"] == "Ford" and d["year"] == 2013 and d["check_digit_ok"] is True
    assert [p["label"] for p in d["parts"]] == ["Maker", "Model", "Check digit", "Year", "Plant", "Serial"]
    assert d["parts"][4]["meaning"] == "Dearborn, Michigan"


def test_vin_check_digit_mismatch_is_reported():
    assert vin.decode("1FTFW1ET5DFC10312")["check_digit_ok"] is False


def test_vin_rejects_bad_format():
    assert vin.decode("1FTFW1ET9DFC1031")["valid"] is False         # 16 chars
    assert vin.decode("1FTFW1ET9DFC1031O")["valid"] is False        # letter O
    assert vin.is_valid_format(" 1ftfw1et9dfc10312 ")                # normalised


def test_vin_year_cycles():
    assert vin.model_year("1G1JC5444R7252367") == 1994              # digit in position 7 -> 1980s cycle
    assert vin.model_year("5YJ3E1EA7KF317000") == 2019              # letter in position 7 -> 2010+ cycle


def test_vin_non_north_american_has_no_check_digit_rule():
    d = vin.decode("WVWZZZ1KZ6W612345")
    assert d["maker"].startswith("Volkswagen") and d["check_digit_ok"] is None


# ------------------------------------------------------------------ mileage
STAMPS = [{"code": "P0301", "module_name": "Engine", "km": 148_225}, {"code": "C0035", "module_name": "ABS", "km": 142_284}]


def test_mileage_genuine_scores_high():
    r = mileage.evaluate(148_280, module_readings=[{"name": "Engine", "km": 148_290}, {"name": "Body", "km": 148_226}],
                         fault_stamps=STAMPS, engine_hours=2940, unit="km")
    assert r["band"] == "High" and r["verdict"] == "genuine" and r["score"] >= 90
    assert {e["key"]: e["result"] for e in r["evidence"]} == {"modules": "agrees", "stamps": "agrees", "hours": "agrees"}


def test_mileage_rollback_scores_low_and_estimates_missing_distance():
    r = mileage.evaluate(100_260, module_readings=[{"name": "Engine", "km": 148_290}], fault_stamps=STAMPS,
                         engine_hours=2940, unit="km")
    assert r["band"] == "Low" and r["verdict"] == "rolled_back" and r["score"] <= 25
    assert r["estimated_km"] == 148_290 and r["missing_km"] == 48_030


def test_one_strong_conflict_caps_score_even_with_lots_of_agreement():
    r = mileage.evaluate(100_000, module_readings=[{"name": "Engine", "km": 100_010}],
                         fault_stamps=[{"code": "X", "module_name": "ABS", "km": 130_000}],
                         engine_hours=2000, past_readings=[{"date": "2025-01-01", "km": 90_000}],
                         service_records=[{"date": "2025-06-01", "km": 95_000}])
    assert r["score"] <= 25


def test_small_gaps_between_modules_are_tolerated():
    r = mileage.evaluate(150_000, module_readings=[{"name": "Engine", "km": 150_500}])   # 0.33 %
    assert r["evidence"][0]["result"] == "agrees"


def test_higher_past_reading_in_app_history_conflicts():
    r = mileage.evaluate(60_000, past_readings=[{"date": "2025-10-12", "km": 98_000}])
    ev = r["evidence"][0]
    assert ev["key"] == "history" and ev["result"] == "conflicts" and ev["weight"] == "strong"


def test_engine_hours_too_high_for_distance_conflicts():
    r = mileage.evaluate(30_000, engine_hours=3000)
    assert r["evidence"][0]["result"] == "conflicts"


def test_wear_from_photos_and_context_notes():
    r = mileage.evaluate(40_000, wear_estimates=[{"label": "Interior wear", "low_km": 120_000, "high_km": 180_000}],
                         vehicle_year=2013, current_year=2026, codes_cleared_km=1300)
    keys = {e["key"]: e for e in r["evidence"]}
    assert keys["wear:Interior wear"]["result"] == "conflicts"
    assert keys["age"]["result"] == "note" and keys["cleared"]["weight"] == "context"


def test_no_evidence_gives_no_score():
    r = mileage.evaluate(50_000)
    assert r["score"] is None and r["verdict"] == "unknown"


def test_display_units_in_findings():
    r = mileage.evaluate(160_934, module_readings=[{"name": "Engine", "km": 200_000}], unit="mi")
    assert " mi" in r["evidence"][0]["finding"] and r["missing_display"].endswith("mi")


def test_negative_reading_rejected():
    with pytest.raises(ValueError):
        mileage.evaluate(-1)


# ------------------------------------------------------------------ plans
def test_all_templates_validate():
    for goal in plans.TEMPLATES:
        p = plans.template(goal)
        assert p.steps and p.source == "template"


def test_template_unknown_goal():
    with pytest.raises(KeyError):
        plans.template("nope")


def _step(**kw):
    base = {"id": "s1", "phase": "P", "short": "S", "title": "Do a thing", "kind": "confirm"}
    return {**base, **kw}


def test_question_step_needs_choices():
    with pytest.raises(ValueError):
        plans.Step.model_validate(_step(kind="question", choices=["only one"]))


def test_bad_pid_rejected_and_pids_uppercased():
    with pytest.raises(ValueError):
        plans.Step.model_validate(_step(capture=["ZZ"]))
    assert plans.Step.model_validate(_step(capture=["0c"])).capture == ["0C"]


def test_duplicate_step_ids_rejected():
    with pytest.raises(ValueError):
        plans.Plan.model_validate({"title": "Plan", "goal": "fix", "steps": [_step(), _step()]})


def test_record_summary_and_targets():
    p = plans.template("inspection")
    q = next(s for s in p.steps if s.kind == "question")
    with pytest.raises(ValueError):
        plans.record({}, q, "done", "not a choice")
    res = plans.record({}, p.steps[0], "done", readings=[{"pid": "42", "value": 12.6}])
    res = plans.record(res, p.steps[1], "skipped")
    s = plans.summary(p, res)
    assert s["done"] == 1 and s["skipped"] == 1 and s["next_index"] == 2 and not s["finished"]
    rev = next(s for s in p.steps if s.id == "rev")
    assert plans.in_target(rev.target, [{"pid": "0C", "value": 2450}]) is True
    assert plans.in_target(rev.target, [{"pid": "0C", "value": 900}]) is False
    assert plans.in_target(None, []) is None
    with pytest.raises(ValueError):
        plans.record({}, p.steps[0], "maybe")


# ------------------------------------------------------------------ security
def test_password_hash_roundtrip():
    h = hash_password("hunter2 but longer")
    assert h.startswith("scrypt$") and verify_password("hunter2 but longer", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage") and not verify_password("x", "md5$1$2$3$4$5")
    assert hash_password("same") != hash_password("same")          # salted


@pytest.mark.parametrize("pw,ok", [("short1", False), ("onlyletterslong", False), ("1234567890123", False),
                                   ("letters and 123", True)])
def test_password_policy(pw, ok):
    assert (password_problem(pw) is None) == ok


def test_email_validation():
    assert valid_email("a.b@example.co") and not valid_email("nope") and not valid_email("a@b")


def test_secret_box():
    box = SecretBox("k" * 40)
    token = box.encrypt("sk-ant-secret")
    assert "sk-ant" not in token and box.decrypt(token) == "sk-ant-secret"
    assert SecretBox("other" * 10).decrypt(token) is None
    assert box.decrypt(None) is None
    assert mask_key("sk-ant-api03-abcdefgh1234") == "…1234" and mask_key(None) is None


def test_rate_limiter():
    rl = RateLimiter(2, window_s=0.2)
    assert rl.hit("a") and rl.hit("a") and not rl.hit("a")
    assert rl.hit("b")
    time.sleep(0.25)
    assert rl.hit("a")
    rl.reset("a")


# ------------------------------------------------------------------ photos
def _image(fmt="JPEG", size=(800, 600), exif=False):
    img = Image.new("RGB", size, (120, 80, 40))
    buf = io.BytesIO()
    kw = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "PhoneMaker"         # Make
        ex[0x8825] = {2: (51.0, 30.0, 0.0)}  # GPS IFD
        kw["exif"] = ex
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def test_process_upload_normalises_to_jpeg_and_strips_exif():
    data, w, h = photos.process_upload(_image(exif=True), 5 * 1024 * 1024)
    assert (w, h) == (800, 600)
    out = Image.open(io.BytesIO(data))
    assert out.format == "JPEG" and not out.getexif()


def test_process_upload_accepts_png_and_downscales():
    data, w, h = photos.process_upload(_image("PNG", (4000, 3000)), 20 * 1024 * 1024)
    assert max(w, h) == photos.STORE_MAX_PX


@pytest.mark.parametrize("raw,msg", [(b"", "empty"), (b"not an image at all", "readable"),
                                     (_image("GIF"), "JPEG, PNG or WebP")])
def test_process_upload_rejects(raw, msg):
    with pytest.raises(photos.PhotoError, match=msg):
        photos.process_upload(raw, 5 * 1024 * 1024)


def test_process_upload_size_limit():
    with pytest.raises(photos.PhotoError, match="under"):
        photos.process_upload(_image(), 100)


def test_path_traversal_blocked(tmp_path):
    for bad in ("../x.jpg", "a/b.jpg", ".hidden"):
        with pytest.raises(photos.PhotoError):
            photos.path_for(tmp_path, 1, 1, bad)


def test_coverage():
    cov = photos.coverage([{"slot": "ext_front"}, {"slot": "unsorted"}])
    assert cov["total"] == len(photos.SLOTS) and cov["done"] == 1 and cov["unsorted"] == 1
    assert all(m["slot"] != "ext_front" for m in cov["missing"])
