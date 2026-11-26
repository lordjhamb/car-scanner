"""All-module scan using UDS (ISO 14229) and KWP2000-on-CAN (ISO 14230-3).

Generic OBD-II only exposes engine/transmission emission codes. Body, ABS,
airbag, cluster, gateway etc. are reached by addressing every possible
diagnostic CAN ID and asking 'read DTC information'. This works on most
CAN cars (~2008+) without any brand-specific database, though module *names*
and some data (e.g. odometer DIDs) are brand-specific.
"""
from __future__ import annotations

import time

from .dtc_db import describe
from .obd import decode_dtc

# Commonly used request IDs (11-bit). Full scan covers 0x700-0x7F7 anyway.
KNOWN_11BIT = {
    0x7E0: "Engine (ECM/PCM)", 0x7E1: "Transmission (TCM)", 0x7E2: "Hybrid/EV ECU",
    0x7E3: "ECU 7E3", 0x7E4: "Battery / BMS", 0x7E5: "ECU 7E5", 0x7E6: "ECU 7E6", 0x7E7: "ECU 7E7",
    0x760: "ABS / Brakes", 0x737: "Airbag (SRS)", 0x726: "Body (BCM)", 0x720: "Instrument cluster",
    0x710: "Gateway", 0x716: "Gateway", 0x730: "Power steering", 0x740: "Door / Body",
    0x750: "Body", 0x770: "Climate (HVAC)", 0x7A0: "Climate / Body", 0x7B0: "Parking aid",
    0x7C0: "Steering column", 0x7D0: "Infotainment", 0x706: "Camera / ADAS",
    0x714: "Instrument cluster (VAG)", 0x713: "Brakes (VAG)", 0x715: "Airbag (VAG)",
    0x712: "Steering (VAG)", 0x744: "ECU 744", 0x746: "Climate (VAG)", 0x752: "ECU 752",
    0x765: "ECU 765", 0x773: "Radio (VAG)", 0x7C6: "ECU 7C6", 0x7D2: "ECU 7D2",
    0x724: "Tyre pressure (TPMS)", 0x733: "ECU 733", 0x784: "ECU 784", 0x7B3: "ECU 7B3",
}
# 29-bit normal-fixed addressing target byte -> name (18DA<tt>F1)
KNOWN_29BIT = {0x10: "Engine (ECM)", 0x18: "Transmission (TCM)", 0x28: "ABS / Brakes",
               0x58: "Airbag (SRS)", 0x40: "Body (BCM)", 0x60: "Instrument cluster"}

STATUS_BITS = [
    "testFailed", "testFailedThisCycle", "pending", "confirmed",
    "notCompletedSinceClear", "failedSinceClear", "notCompletedThisCycle", "warningLamp",
]

NRC = {0x10: "general reject", 0x11: "service not supported", 0x12: "sub-function not supported",
       0x13: "bad length", 0x22: "conditions not correct", 0x31: "request out of range",
       0x33: "security access denied", 0x7E: "not supported in this session",
       0x7F: "service not supported in session"}


def classify(status: int):
    """Turn the UDS status byte into human categories."""
    tags = [n for i, n in enumerate(STATUS_BITS) if status & (1 << i)]
    if status & 0x01:
        state = "ACTIVE"           # failing right now
    elif status & 0x04 and not status & 0x08:
        state = "PENDING"
    elif status & 0x08:
        state = "HISTORY"          # stored/confirmed, not failing now -> past / intermittent
    else:
        state = "INFO"
    return state, tags


class ModuleScanner:
    def __init__(self, elm, progress=None):
        self.elm = elm
        self.progress = progress or (lambda **_: None)
        self.cancel = False
        self.bits29 = elm.protocol in (7, 9)

    # ------------------------------------------------------------- setup
    def _setup(self):
        e = self.elm
        for c in ("ATH1", "ATS1", "ATCAF1", "ATAT0", "ATST19"):   # 0x19*4ms = 100 ms timeout
            e.at(c)
        if self.bits29:
            e.at("ATCF18DAF100")
            e.at("ATCM1FFFFF00")
        else:
            e.at("ATCF700")
            e.at("ATCM700")                 # accept replies from 0x700-0x7FF
        e.at("ATFCSH" + ("18DA10F1" if self.bits29 else "7E0"))
        e.at("ATFCSD300000")                # flow control: CTS, no block limit, 0 ms
        e.at("ATFCSM1")

    def _address(self, target: int):
        hdr = f"18DA{target:02X}F1" if self.bits29 else f"{target:03X}"
        self.elm.at("ATSH" + hdr)
        self.elm.at("ATFCSH" + hdr)

    def _send(self, cmd, timeout=3.0, retry_pending=True):
        """Send to current address, return (responder_header, payload) or (None, None)."""
        msgs, _ = self.elm.request(cmd, timeout)
        sid = int(cmd[:2], 16)
        best = (None, None)
        for hdr, d in msgs:
            if not d:
                continue
            if d[0] == 0x7F and len(d) >= 3 and d[2] == 0x78:        # response pending
                continue
            if d[0] in (sid + 0x40, 0x7F):
                return hdr, d
        if retry_pending and any(d and d[0] == 0x7F and len(d) >= 3 and d[2] == 0x78 for _, d in msgs):
            time.sleep(0.3)
            self.elm.at("ATST FF")
            r = self._send(cmd, timeout, retry_pending=False)
            self.elm.at("ATST19")
            return r
        return best

    # ------------------------------------------------------------- scan
    def scan(self, full=True):
        if self.elm.protocol not in (6, 7, 8, 9):
            raise RuntimeError("Module scan needs a CAN car (protocol 6-9). This car uses an older "
                               "protocol; only generic OBD-II codes are available.")
        self._setup()
        if self.bits29:
            targets = list(range(0x00, 0x100)) if full else list(KNOWN_29BIT)
            names = KNOWN_29BIT
        else:
            targets = [t for t in range(0x700, 0x7F8) if t != 0x7DF] if full else list(KNOWN_11BIT)
            names = KNOWN_11BIT
        found = []
        try:
            for i, tgt in enumerate(targets):
                if self.cancel:
                    break
                self.progress(done=i, total=len(targets), current=f"{tgt:03X}", found=len(found))
                self._address(tgt)
                hdr, d = self._send("1902FF", 1.5)
                if hdr is None:
                    continue
                mod = {"request_id": f"{tgt:03X}" if not self.bits29 else f"18DA{tgt:02X}F1",
                       "response_id": hdr, "name": names.get(tgt, f"Module {tgt:03X}"),
                       "protocol": "UDS", "dtcs": [], "error": None}
                if d[0] == 0x59:
                    mod["dtcs"] = self._parse_uds_dtcs(d)
                else:                                  # negative -> try KWP2000 0x18
                    h2, d2 = self._send("1802FF00", 1.5)
                    if d2 and d2[0] == 0x58:
                        mod["protocol"] = "KWP2000"
                        mod["dtcs"] = self._parse_kwp_dtcs(d2)
                    else:
                        mod["error"] = "DTC read refused: " + NRC.get(d[2] if len(d) > 2 else 0, "unknown")
                self._identify(mod)
                found.append(mod)
                self.progress(done=i + 1, total=len(targets), current=f"{tgt:03X}", found=len(found))
        finally:
            self.elm.restore()
        self.progress(done=len(targets), total=len(targets), current="", found=len(found))
        return found

    def _identify(self, mod):
        for did, key in ((0xF197, "system_name"), (0xF187, "part_number"), (0xF188, "software"),
                         (0xF190, "vin")):
            hdr, d = self._send(f"22{did:04X}", 1.0, retry_pending=False)
            if d and d[0] == 0x62 and len(d) > 3:
                txt = "".join(chr(c) for c in d[3:] if 32 <= c < 127).strip()
                if txt:
                    mod[key] = txt
        mod["role"] = mod["name"]           # generic role from the address table
        if mod.get("system_name"):
            mod["name"] = mod["system_name"]    # what the module calls itself

    @staticmethod
    def _parse_uds_dtcs(d):
        out = []
        recs = d[3:]                               # 59 02 <availability mask> then 4-byte records
        for i in range(0, len(recs) - 3, 4):
            b1, b2, ftb, st = recs[i:i + 4]
            if b1 == b2 == ftb == 0:
                continue
            code = decode_dtc(b1, b2)
            state, tags = classify(st)
            out.append({"code": code, "ftb": f"{ftb:02X}", "full": f"{code}-{ftb:02X}",
                        "status": f"{st:02X}", "state": state, "flags": tags,
                        "description": describe(code), "raw": f"{b1:02X}{b2:02X}{ftb:02X}"})
        return out

    @staticmethod
    def _parse_kwp_dtcs(d):
        out = []
        recs = d[2:]                               # 58 <count> then 3-byte records
        for i in range(0, len(recs) - 2, 3):
            b1, b2, st = recs[i:i + 3]
            if b1 == b2 == 0:
                continue
            code = decode_dtc(b1, b2)
            active = bool(st & 0x40) or bool(st & 0x01)
            out.append({"code": code, "ftb": "", "full": code, "status": f"{st:02X}",
                        "state": "ACTIVE" if active else "HISTORY", "flags": [],
                        "description": describe(code), "raw": f"{b1:02X}{b2:02X}"})
        return out

    # ------------------------------------------------------------- per-module actions
    def with_module(self, request_id: str, fn):
        self._setup()
        try:
            tgt = int(request_id[4:6], 16) if len(request_id) == 8 else int(request_id, 16)
            self._address(tgt)
            return fn()
        finally:
            self.elm.restore()

    def clear_module(self, request_id: str):
        def go():
            hdr, d = self._send("14FFFFFF", 5.0)
            if d and d[0] == 0x54:
                return {"ok": True}
            # some modules need the extended session first
            self._send("1003", 2.0)
            hdr, d = self._send("14FFFFFF", 5.0)
            if d and d[0] == 0x54:
                return {"ok": True, "note": "cleared in extended session"}
            if d and d[0] == 0x7F:
                return {"ok": False, "error": NRC.get(d[2], f"NRC {d[2]:02X}")}
            return {"ok": False, "error": "no response"}
        return self.with_module(request_id, go)

    def snapshot(self, request_id: str, raw_dtc: str):
        """Freeze-frame for a module DTC (UDS 19 04). Record layout is manufacturer
        specific, so we return raw hex plus any DIDs we can recognise."""
        def go():
            hdr, d = self._send(f"1904{raw_dtc}FF", 3.0)
            if not d or d[0] != 0x59:
                return {"ok": False, "error": "not available"}
            data = list(d[6:])
            return {"ok": True, "raw": " ".join(f"{b:02X}" for b in data), "hints": _scan_dids(data)}
        return self.with_module(request_id, go)

    def fault_mileage_stamps(self, modules):
        """Read the snapshot of every UDS fault code and pull out the odometer value
        the module stored when the fault was recorded. Returns
        [{"module", "module_name", "code", "km"}]. Codes without a recognisable
        odometer DID are skipped (layout is manufacturer specific)."""
        stamps = []
        for mod in modules:
            dtcs = [d for d in mod.get("dtcs", []) if d.get("raw") and len(d["raw"]) == 6]
            if mod.get("protocol") != "UDS" or not dtcs:
                continue

            def go(mod=mod, dtcs=dtcs):
                out = []
                for dtc in dtcs:
                    _, d = self._send(f"1904{dtc['raw']}FF", 3.0, retry_pending=False)
                    if not d or d[0] != 0x59:
                        continue
                    km = snapshot_odometer(list(d[6:]))
                    if km is not None:
                        out.append({"module": mod["request_id"], "module_name": mod["name"],
                                    "code": dtc["full"], "km": km})
                return out
            stamps += self.with_module(mod["request_id"], go)
        return stamps

    def read_odometer(self, request_ids):
        """Try known odometer DIDs on the discovered modules."""
        results = []
        for rid in request_ids:
            def go():
                found = []
                for did, size, scale, label in ODO_DIDS:
                    hdr, d = self._send(f"22{did:04X}", 1.0, retry_pending=False)
                    if d and d[0] == 0x62 and len(d) >= 3 + size:
                        v = int.from_bytes(bytes(d[3:3 + size]), "big") * scale
                        found.append({"did": f"{did:04X}", "km": round(v, 1), "label": label})
                return found
            for f in self.with_module(rid, go):
                f["module"] = rid
                results.append(f)
        return results


# (DID, bytes, scale, label) — first is the ISO 27145/J1979-2 standard, others are common brand DIDs
ODO_DIDS = [
    (0xF4A6, 4, 0.1, "Odometer (J1979-2 standard)"),
    (0xDD01, 3, 1, "Odometer (Ford/Mazda style DID DD01)"),
]


# DIDs that commonly carry the odometer inside a DTC snapshot record: (did, bytes, scale)
SNAPSHOT_ODO_DIDS = [(0xDD01, 3, 1.0), (0xF4A6, 4, 0.1)]


def _scan_dids(data):
    hints = []
    for did, size, scale in SNAPSHOT_ODO_DIDS:
        hi, lo = did >> 8, did & 0xFF
        for i in range(len(data) - 1):
            if data[i] == hi and data[i + 1] == lo and i + 2 + size <= len(data):
                hints.append({"label": f"Odometer at fault (DID {did:04X}, km)",
                              "value": round(int.from_bytes(bytes(data[i + 2:i + 2 + size]), "big") * scale, 1)})
                break
    return hints


def snapshot_odometer(data) -> float | None:
    """Odometer (km) found in a UDS snapshot record, or None."""
    hints = _scan_dids(data)
    return hints[0]["value"] if hints else None
