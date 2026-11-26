"""Guided runs and photo inspection."""
import io

from PIL import Image

from carscan import db

from .conftest import FakeProvider


def _jpeg(color=(90, 90, 90)):
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


# ------------------------------------------------------------------ runs
def test_templates_listed(client):
    goals = {t["goal"] for t in client.get("/api/plans/templates").json()["templates"]}
    assert goals == {"inspection", "health", "emissions", "delivery"}


def test_inspection_run_end_to_end(client):
    client.connect_demo()
    car = client.add_car()
    run = client.post("/api/runs", {"goal": "inspection", "car_id": car["id"]}).json()
    assert run["status"] == "running" and run["progress"]["next_index"] == 0
    steps = run["plan"]["steps"]

    # auto step with an action captures readings and codes
    r = client.post(f"/api/runs/{run['id']}/steps/ign", {"status": "done"}).json()
    assert r["results"]["ign"]["readings"][0]["pid"] == "42"
    r = client.post(f"/api/runs/{run['id']}/steps/codes", {"status": "done"}).json()
    assert [c["code"] for c in r["results"]["codes"]["data"]["stored"]] == ["P0301", "P0420", "P0700"]

    # question steps need a valid answer
    assert client.post(f"/api/runs/{run['id']}/steps/bulbs", {"status": "done"}).status_code == 422
    assert client.post(f"/api/runs/{run['id']}/steps/bulbs", {"status": "done", "answer": "Maybe"}).status_code == 422
    r = client.post(f"/api/runs/{run['id']}/steps/bulbs", {"status": "done", "answer": "Some stayed on"}).json()
    assert r["results"]["bulbs"]["answer"] == "Some stayed on"

    # live sample with a target
    s = client.get(f"/api/runs/{run['id']}/steps/rev/sample").json()
    assert s["connected"] is True and s["in_target"] in (True, False)
    assert any(v["pid"] == "0C" for v in s["values"])

    for st in steps[3:]:
        r = client.post(f"/api/runs/{run['id']}/steps/{st['id']}", {"status": "skipped"}).json()
    assert r["status"] == "finished" and r["progress"]["finished"] is True
    assert r["progress"]["done"] == 3 and r["progress"]["skipped"] == len(steps) - 3
    assert client.get(f"/api/cars/{car['id']}").json()["runs"][0]["status"] == "finished"


def test_run_without_adapter_records_without_readings(client):
    run = client.post("/api/runs", {"goal": "health"}).json()
    r = client.post(f"/api/runs/{run['id']}/steps/ign", {"status": "done"}).json()
    assert r["results"]["ign"]["readings"] == []
    assert client.get(f"/api/runs/{run['id']}/steps/ign/sample").json()["connected"] is False


def test_runs_are_private(client, other):
    run = client.post("/api/runs", {"goal": "health"}).json()
    assert other.get(f"/api/runs/{run['id']}").status_code == 404
    assert other.post(f"/api/runs/{run['id']}/steps/ign", {"status": "done"}).status_code == 404
    assert client.post(f"/api/runs/{run['id']}/steps/nope", {"status": "done"}).status_code == 404
    assert client.post("/api/runs", {"goal": "hack"}).status_code == 422
    assert client.post("/api/runs", {"goal": "health", "car_id": 999}).status_code == 404


def test_start_run_can_skip_steps(client):
    run = client.post("/api/runs", {"goal": "inspection"}).json()
    r = client.post(f"/api/runs/{run['id']}/start", {"skip_steps": ["steer", "brake"]}).json()
    assert {s["id"] for s in r["plan"]["steps"]}.isdisjoint({"steer", "brake"})
    all_ids = [s["id"] for s in r["plan"]["steps"]]
    assert client.post(f"/api/runs/{run['id']}/start", {"skip_steps": all_ids}).status_code == 422


# ------------------------------------------------------------------ photos
def test_photo_upload_replace_serve_and_delete(client, other, settings):
    car = client.add_car()
    base = f"/api/cars/{car['id']}/photos"
    r = client.http.post(base, data={"slot": "ext_front"}, files={"file": ("front.jpg", _jpeg(), "image/jpeg")},
                         headers=client.headers())
    assert r.status_code == 200
    p = r.json()["photo"]
    assert "filename" not in p and r.json()["coverage"]["done"] == 1
    # replacing a slot keeps one photo per slot
    r = client.http.post(base, data={"slot": "ext_front"}, files={"file": ("f2.jpg", _jpeg((10, 10, 10)), "image/jpeg")},
                         headers=client.headers())
    listing = client.get(base).json()
    assert len(listing["photos"]) == 1 and listing["coverage"]["done"] == 1
    url = listing["photos"][0]["url"]
    img = client.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert other.get(url).status_code == 404                    # not your car
    assert client.delete(url.rsplit("/", 1)[0]).status_code == 200
    assert client.get(base).json()["photos"] == []


def test_photo_upload_validation(client):
    car = client.add_car()
    base = f"/api/cars/{car['id']}/photos"
    r = client.http.post(base, data={"slot": "bogus"}, files={"file": ("a.jpg", _jpeg(), "image/jpeg")},
                         headers=client.headers())
    assert r.status_code == 422
    r = client.http.post(base, data={"slot": "ext_front"}, files={"file": ("a.jpg", b"<script>", "image/jpeg")},
                         headers=client.headers())
    assert r.status_code == 422
    r = client.http.post(base, data={"slot": "unsorted"}, files={"file": ("a.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 403                                  # no CSRF token


def test_photo_analysis_report(client, providers):
    client.put("/api/ai/settings", {"anthropic_key": "sk-ant-test-key-0123456789"})
    car = client.add_car()
    for slot in ("ext_front", "int_dash"):
        client.http.post(f"/api/cars/{car['id']}/photos", data={"slot": slot},
                         files={"file": ("x.jpg", _jpeg(), "image/jpeg")}, headers=client.headers())
    providers.provider = FakeProvider(script=[("submit_photo_report", {
        "headline": "Good overall, one repaired corner", "summary": "Front-left repainted.",
        "areas": [{"name": "Body and paint", "rating": 3, "note": "Repaint"}],
        "findings": [{"title": "Airbag light on", "kind": "warning_light", "confidence": "High", "slot": "int_dash",
                      "what": "Light stays on.", "next": "See a dealer."}],
        "wear_estimates": [{"label": "Interior wear", "low_km": 120000, "high_km": 180000}]})])
    r = client.post(f"/api/cars/{car['id']}/photos/analyze", {"odometer_km": 148000})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["headline"].startswith("Good") and rep["photo_count"] == 2 and "Front" not in rep["missing"]
    call = providers.provider.calls[0]
    assert len(call["messages"][0].images) == 2 and call["web_search"] is False
    assert "Front" in call["messages"][0].text and "148,000 km" in call["messages"][0].text
    assert client.get(f"/api/cars/{car['id']}/photos/report").json()["report"]["findings"][0]["kind"] == "warning_light"
    # the wear estimate now feeds the mileage check
    m = client.post(f"/api/cars/{car['id']}/mileage", {"reading": 60000, "unit": "km"}).json()
    assert any(e["key"].startswith("wear:") and e["result"] == "conflicts" for e in m["evidence"])


def test_photo_analysis_rejects_invalid_report(client, providers):
    client.put("/api/ai/settings", {"anthropic_key": "sk-ant-test-key-0123456789"})
    car = client.add_car()
    client.http.post(f"/api/cars/{car['id']}/photos", data={"slot": "ext_front"},
                     files={"file": ("x.jpg", _jpeg(), "image/jpeg")}, headers=client.headers())
    providers.provider = FakeProvider(script=[("submit_photo_report", {"headline": "x", "summary": "y",
                                                                        "areas": [{"name": "a", "rating": 9}],
                                                                        "findings": []})])
    r = client.post(f"/api/cars/{car['id']}/photos/analyze", {})
    assert r.status_code == 400 and "didn't return a report" in r.json()["detail"]


def test_photo_analysis_needs_photos_and_key(client, settings):
    car = client.add_car()
    r = client.post(f"/api/cars/{car['id']}/photos/analyze", {})
    assert r.status_code == 400 and "API key" in r.json()["detail"]
    client.put("/api/ai/settings", {"anthropic_key": "sk-ant-test-key-0123456789"})
    assert client.post(f"/api/cars/{car['id']}/photos/analyze", {}).status_code == 422


def test_deleting_car_removes_photo_files(client, settings):
    car = client.add_car()
    client.http.post(f"/api/cars/{car['id']}/photos", data={"slot": "ext_front"},
                     files={"file": ("x.jpg", _jpeg(), "image/jpeg")}, headers=client.headers())
    folder = settings.upload_dir / str(client.user["id"]) / str(car["id"])
    assert any(folder.iterdir())
    client.delete(f"/api/cars/{car['id']}")
    assert not folder.exists()
    conn = db.connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0
    conn.close()
