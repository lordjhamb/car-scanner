"""Adapter ownership, saved cars, scans, clearing codes, service records and mileage checks."""
from carscan import db


def test_adapter_requires_login(anon):
    assert anon.post("/api/adapter/connect", {"simulate": True}).status_code == 401


def test_connect_demo_returns_vehicle_identity(client):
    info = client.connect_demo()
    assert info["vin"] == "1FTFW1ET9DFC10312" and info["simulated"] is True
    assert info["vin_decoded"]["year"] == 2013
    assert client.get("/api/adapter/status").json()["connected"] is True


def test_adapter_is_owned_by_one_account(client, other):
    client.connect_demo()
    st = other.get("/api/adapter/status").json()
    assert st == {"connected": False, "in_use_by_other": True, "info": {}, "scan": {"running": False}}
    assert other.post("/api/adapter/connect", {"simulate": True}).status_code == 409
    assert other.get("/api/adapter/live").status_code == 409
    assert other.post("/api/adapter/disconnect").status_code == 409
    client.post("/api/adapter/disconnect")
    assert other.post("/api/adapter/connect", {"simulate": True}).status_code == 200


def test_logout_releases_adapter(client, other):
    client.connect_demo()
    client.post("/api/auth/logout")
    assert other.get("/api/adapter/status").json()["in_use_by_other"] is False


def test_live_data_and_errors_without_adapter(client):
    assert client.get("/api/adapter/live").status_code == 400
    client.connect_demo()
    names = {v["name"] for v in client.get("/api/adapter/live").json()["values"]}
    assert {"Engine RPM", "Vehicle speed", "Coolant temp"} <= names


def test_connect_real_port_needs_a_port(client):
    r = client.post("/api/adapter/connect", {"simulate": False})
    assert r.status_code == 400 and "port" in r.json()["detail"].lower()


def test_car_upsert_list_patch_and_vin_validation(client):
    car = client.add_car()
    assert car["make"] == "Ford" and car["year"] == 2013 and car["model"] == "F-150"
    again = client.add_car(nickname="Work truck")
    assert again["id"] == car["id"] and again["nickname"] == "Work truck"
    assert len(client.get("/api/cars").json()["cars"]) == 1
    r = client.patch(f"/api/cars/{car['id']}", {"engine": "5.0 L V8"})
    assert r.json()["car"]["engine"] == "5.0 L V8"
    assert client.post("/api/cars", {"vin": "1FTFW1ET9DFC1031O"}).status_code == 422


def test_cars_are_private_to_their_owner(client, other):
    car = client.add_car()
    cid = car["id"]
    assert other.get(f"/api/cars/{cid}").status_code == 404
    assert other.patch(f"/api/cars/{cid}", {"nickname": "mine now"}).status_code == 404
    assert other.delete(f"/api/cars/{cid}").status_code == 404
    assert other.get(f"/api/cars/{cid}/scans").status_code == 404
    assert other.get("/api/cars").json()["cars"] == []
    # the same VIN in another garage is a separate car
    assert other.add_car()["id"] != cid


def test_obd_scan_is_saved_with_odometer(client):
    client.connect_demo()
    car = client.add_car()
    r = client.post(f"/api/cars/{car['id']}/scans/obd", {}).json()
    assert [c["code"] for c in r["stored"]] == ["P0301", "P0420", "P0700"]
    assert r["odometer_km"] == 148290.0 and r["freeze_frame"]["dtc"] == "P0301"
    detail = client.get(f"/api/cars/{car['id']}").json()
    assert detail["car"]["scan_count"] == 1 and detail["scans"][0]["kind"] == "obd"
    r = client.post(f"/api/cars/{car['id']}/scans/obd", {"odometer_km": 1000})
    assert r.json()["odometer_km"] == 1000


def test_module_scan_runs_in_background_and_is_saved(client):
    client.connect_demo()
    car = client.add_car()
    s = client.module_scan(car["id"])
    assert len(s["result"]) == 8 and "error" not in s
    scans = client.get(f"/api/cars/{car['id']}/scans").json()["scans"]
    assert scans[0]["kind"] == "modules" and len(scans[0]["data"]["modules"]) == 8


def test_scan_needs_connected_adapter(client):
    car = client.add_car()
    assert client.post(f"/api/cars/{car['id']}/scans/obd", {}).status_code == 400


def test_clear_codes_requires_typed_confirmation(client):
    client.connect_demo()
    car = client.add_car()
    assert client.post(f"/api/cars/{car['id']}/clear", {"target": "obd"}).status_code == 422
    assert client.post(f"/api/cars/{car['id']}/clear", {"target": "rm -rf", "confirm": "CLEAR"}).status_code == 422
    r = client.post(f"/api/cars/{car['id']}/clear", {"target": "obd", "confirm": "CLEAR"})
    assert r.json()["ok"] is True
    after = client.post(f"/api/cars/{car['id']}/scans/obd", {}).json()
    assert after["stored"] == [] and [c["code"] for c in after["permanent"]] == ["P0420"]
    kinds = [s["kind"] for s in client.get(f"/api/cars/{car['id']}/scans").json()["scans"]]
    assert "clear" in kinds


def test_clear_single_module(client):
    client.connect_demo()
    car = client.add_car()
    r = client.post(f"/api/cars/{car['id']}/clear", {"target": "760", "confirm": "CLEAR"})
    assert r.json() == {"ok": True}


def test_service_records(client, other):
    car = client.add_car()
    assert client.post(f"/api/cars/{car['id']}/service-records", {"date": "2026-03-01", "km": 144_000,
                                                                   "note": "Oil change"}).status_code == 200
    assert client.post(f"/api/cars/{car['id']}/service-records", {"date": "March", "km": 1}).status_code == 422
    assert client.get(f"/api/cars/{car['id']}/service-records").json()["records"][0]["note"] == "Oil change"
    assert other.get(f"/api/cars/{car['id']}/service-records").status_code == 404


def test_mileage_check_genuine_car(client):
    client.connect_demo("genuine")
    car = client.add_car()
    client.module_scan(car["id"])
    r = client.post(f"/api/cars/{car['id']}/mileage", {"reading": 92_140, "unit": "mi"}).json()
    assert r["band"] == "High" and r["verdict"] == "genuine" and r["used_live_data"] is True
    assert len(r["stamps"]) == 10
    # the instrument cluster itself is not used as evidence against its own reading
    assert {e["key"] for e in r["evidence"]} >= {"modules", "stamps", "hours"}
    saved = client.get(f"/api/cars/{car['id']}").json()["reports"]["mileage"]
    assert saved["score"] == r["score"]


def test_mileage_check_detects_rollback(client):
    info = client.connect_demo("rollback")
    car = client.add_car()
    client.module_scan(car["id"])
    r = client.post(f"/api/cars/{car['id']}/mileage", {"reading": 62_300, "unit": "mi"}).json()
    assert info["vin"] and r["band"] == "Low" and r["verdict"] == "rolled_back"
    assert 29_000 < r["missing_km"] / 1.609344 < 31_000
    assert any(e["key"] == "stamps" and e["result"] == "conflicts" for e in r["evidence"])


def test_mileage_check_offline_uses_saved_history(client, settings):
    car = client.add_car()
    conn = db.connect(settings.db_path)
    conn.execute("INSERT INTO scans (user_id, car_id, kind, odometer_km, data, created_at) VALUES (?,?,?,?,?,?)",
                 (client.user["id"], car["id"], "obd", 150_000, "{}", db.now() - 86400 * 40))
    conn.commit()
    conn.close()
    r = client.post(f"/api/cars/{car['id']}/mileage", {"reading": 80_000, "unit": "km"}).json()
    assert r["used_live_data"] is False and r["band"] == "Low"


def test_mileage_input_validation(client):
    car = client.add_car()
    assert client.post(f"/api/cars/{car['id']}/mileage", {"reading": -5}).status_code == 422
    assert client.post(f"/api/cars/{car['id']}/mileage", {"reading": 5, "unit": "leagues"}).status_code == 422


def test_delete_car_cascades(client, settings):
    client.connect_demo()
    car = client.add_car()
    client.post(f"/api/cars/{car['id']}/scans/obd", {})
    assert client.delete(f"/api/cars/{car['id']}").status_code == 200
    conn = db.connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    conn.close()


def test_vin_decode_endpoint(client):
    assert client.get("/api/vin/1FTFW1ET9DFC10312").json()["make"] == "Ford"
