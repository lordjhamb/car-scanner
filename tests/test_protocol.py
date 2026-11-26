"""ELM327 framing, ISO-TP reassembly, OBD-II services and UDS module scanning."""
import pytest

from carscan.obd.elm327 import assemble, is_can, split_frames
from carscan.obd.obd import OBD, decode_dtc, ecu_name
from carscan.obd.simulator import ROLLBACK_ODOMETER_KM, TRUE_ODOMETER_KM, SimulatedELM
from carscan.obd.uds import ModuleScanner, classify, snapshot_odometer


# ------------------------------------------------------------------ framing
def test_split_frames_11bit_can():
    frames = split_frames(["7E8 06 41 00 BE 3F A8 13 00", "SEARCHING...", "NO DATA"], 6)
    assert frames == [("7E8", [0x06, 0x41, 0x00, 0xBE, 0x3F, 0xA8, 0x13, 0x00])]


def test_split_frames_29bit_can():
    frames = split_frames(["18 DA F1 10 03 41 0D 2D"], 7)
    assert frames == [("18DAF110", [0x03, 0x41, 0x0D, 0x2D])]


def test_split_frames_legacy_protocol_drops_header_and_checksum():
    frames = split_frames(["48 6B 10 41 0C 1A F8 C5"], 3)
    assert frames == [("486B10", [0x41, 0x0C, 0x1A, 0xF8])]


def test_split_frames_ignores_garbage():
    assert split_frames(["BUS INIT: ...OK", "?", "7E8 ZZ"], 6) == []


def test_assemble_single_and_multi_frame():
    frames = [("7E8", [0x10, 0x0A, 0x49, 0x02, 0x01, 0x31, 0x46, 0x54]),
              ("7E9", [0x03, 0x41, 0x0D, 0x20]),
              ("7E8", [0x21, 0x46, 0x57, 0x31, 0x45, 0x00, 0x00, 0x00])]
    msgs = dict(assemble(frames, 6))
    assert msgs["7E9"] == bytes([0x41, 0x0D, 0x20])
    assert msgs["7E8"] == bytes([0x49, 0x02, 0x01, 0x31, 0x46, 0x54, 0x46, 0x57, 0x31, 0x45])


def test_assemble_returns_partial_message_when_frames_missing():
    msgs = assemble([("7E8", [0x10, 0x0A, 0x49, 0x02, 0x01, 0x31, 0x46, 0x54])], 6)
    assert msgs == [("7E8", bytes([0x49, 0x02, 0x01, 0x31, 0x46, 0x54]))]


def test_is_can():
    assert is_can(6) and is_can(9) and not is_can(3)


@pytest.mark.parametrize("b1,b2,code", [(0x03, 0x01, "P0301"), (0x44, 0x20, "C0420"), (0x80, 0x12, "B0012"),
                                        (0xC1, 0x00, "U0100"), (0x13, 0x42, "P1342"), (0xA0, 0x35, "B2035")])
def test_decode_dtc(b1, b2, code):
    assert decode_dtc(b1, b2) == code


def test_ecu_names():
    assert ecu_name("7E8").startswith("Engine")
    assert ecu_name("7E9").startswith("Transmission")
    assert ecu_name("18DAF110").startswith("Engine")
    assert ecu_name("7EC") == "ECU #5"


# ------------------------------------------------------------------ OBD-II against the simulator
@pytest.fixture
def obd():
    elm = SimulatedELM(seed=1)
    elm.connect()
    o = OBD(elm)
    o.discover_pids()
    return o


def test_supported_pids_discovered_per_ecu(obd):
    assert 0x0C in obd.supported["7E8"] and 0xA6 in obd.supported["7E8"]
    assert "7E9" in obd.supported


def test_read_pids_decode_with_units(obd):
    vals = {v["pid"]: v for v in obd.read_many([0x0C, 0x05, 0x42, 0xA6, 0x7F])}
    assert 700 < vals["0C"]["value"] < 2100 and vals["0C"]["unit"] == "rpm"
    assert vals["05"]["value"] == 88 or 87 <= vals["05"]["value"] <= 89
    assert 13.5 < vals["42"]["value"] < 14.5
    assert vals["A6"]["value"] == TRUE_ODOMETER_KM
    assert vals["7F"]["value"] == 2940


def test_unsupported_pid_is_skipped(obd):
    assert obd.read_many([0x5E]) == []


def test_vin(obd):
    assert obd.vin() == "1FTFW1ET9DFC10312"


def test_dtc_modes(obd):
    assert [c["code"] for c in obd.stored_dtcs()] == ["P0301", "P0420", "P0700"]
    assert [c["code"] for c in obd.pending_dtcs()] == ["P0171"]
    assert [c["code"] for c in obd.permanent_dtcs()] == ["P0420"]
    assert obd.stored_dtcs()[0]["description"] == "Cylinder 1 misfire detected"


def test_freeze_frame(obd):
    ff = obd.freeze_frame()
    assert ff["dtc"] == "P0301"
    names = {v["name"]: v["value"] for v in ff["values"]}
    assert names["Engine RPM"] == 812 and names["Coolant temp"] == 32


def test_monitor_status(obd):
    eng = obd.monitor_status()[0]
    assert eng["mil_on"] is True and eng["dtc_count"] == 2
    assert any(not m["complete"] for m in eng["monitors"])


def test_clear_keeps_permanent_codes(obd):
    res = obd.clear_dtcs()
    assert res["ok"] and len(res["responders"]) == 2
    assert obd.stored_dtcs() == [] and obd.pending_dtcs() == []
    assert [c["code"] for c in obd.permanent_dtcs()] == ["P0420"]


# ------------------------------------------------------------------ UDS
@pytest.fixture
def elm():
    e = SimulatedELM(seed=2)
    e.connect()
    return e


def test_full_module_scan_finds_every_module(elm):
    progress = []
    mods = ModuleScanner(elm, lambda **kw: progress.append(kw)).scan(full=True)
    assert {m["request_id"] for m in mods} == {"7E0", "7E1", "760", "737", "726", "720", "7D0", "716"}
    assert progress[-1]["done"] == progress[-1]["total"]
    airbag = next(m for m in mods if m["request_id"] == "737")
    assert airbag["dtcs"][0]["full"] == "B0012-13" and airbag["dtcs"][0]["state"] == "ACTIVE"
    engine = next(m for m in mods if m["request_id"] == "7E0")
    assert {d["code"]: d["state"] for d in engine["dtcs"]} == {"P0301": "ACTIVE", "P0420": "HISTORY", "P0171": "PENDING"}
    assert engine["system_name"] == "ECM Engine"


def test_scan_restores_adapter_header(elm):
    ModuleScanner(elm).scan(full=False)
    assert elm.header == "7DF"


def test_scan_can_be_cancelled(elm):
    sc = ModuleScanner(elm)
    sc.cancel = True
    assert sc.scan(full=True) == []


def test_classify_status_byte():
    assert classify(0x09)[0] == "ACTIVE"
    assert classify(0x24)[0] == "PENDING"
    assert classify(0x08)[0] == "HISTORY"
    assert classify(0x00)[0] == "INFO"
    assert "warningLamp" in classify(0x80)[1]


def test_module_odometers_and_fault_stamps(elm):
    sc = ModuleScanner(elm)
    mods = sc.scan(full=False)
    readings = {r["module"]: r["km"] for r in sc.read_odometer(["7E0", "726", "720"])}
    assert readings["7E0"] == TRUE_ODOMETER_KM and readings["726"] == TRUE_ODOMETER_KM - 64
    stamps = sc.fault_mileage_stamps(mods)
    assert len(stamps) == 10
    assert max(s["km"] for s in stamps) == 148_275


def test_rollback_scenario_cluster_disagrees():
    e = SimulatedELM(scenario="rollback", seed=3)
    e.connect()
    readings = {r["module"]: r["km"] for r in ModuleScanner(e).read_odometer(["720", "7E0"])}
    assert readings["720"] == ROLLBACK_ODOMETER_KM and readings["7E0"] == TRUE_ODOMETER_KM


def test_snapshot_odometer_parser():
    assert snapshot_odometer([0x01, 0x02, 0xDD, 0x01, 0x02, 0x41, 0x9E, 0xF4, 0x0D, 0x2D]) == 147870
    assert snapshot_odometer([0x01, 0x01, 0xF4, 0xA6, 0x00, 0x16, 0x9F, 0x4C]) == 148257.2
    assert snapshot_odometer([0x01, 0x00]) is None


def test_clear_single_module(elm):
    sc = ModuleScanner(elm)
    assert sc.clear_module("760") == {"ok": True}
    abs_mod = next(m for m in sc.scan(full=False) if m["request_id"] == "760")
    assert abs_mod["dtcs"] == []


def test_unknown_module_scan_rejected_on_legacy_protocol():
    e = SimulatedELM()
    e.protocol = 3
    with pytest.raises(RuntimeError, match="CAN"):
        ModuleScanner(e).scan()
