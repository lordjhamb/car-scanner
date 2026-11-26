"""Generic OBD-II services (SAE J1979) that every 1996+ (US) / 2001+ (EU petrol) /
2004+ (EU diesel) car supports, whatever the brand."""
from __future__ import annotations

from .dtc_db import describe

# ---------------------------------------------------------------- helpers

def decode_dtc(b1: int, b2: int) -> str:
    return f"{'PCBU'[b1 >> 6]}{(b1 >> 4) & 3}{b1 & 0xF:X}{b2:02X}"


def ecu_name(header: str) -> str:
    h = header.upper()
    if len(h) == 3:                               # 11-bit CAN
        known = {"7E8": "Engine (ECM/PCM)", "7E9": "Transmission (TCM)", "7EA": "Hybrid / ECU #3",
                 "7EB": "ECU #4", "7EC": "ECU #5", "7ED": "ECU #6", "7EE": "ECU #7", "7EF": "ECU #8"}
        return known.get(h, f"ECU {h}")
    src = h[-2:]                                  # 29-bit CAN or legacy: last byte is sender
    return {"10": "Engine (ECM/PCM)", "18": "Transmission (TCM)", "11": "Engine #2"}.get(src, f"ECU {src}")


def _u16(d):
    return (d[0] << 8) | d[1]


# pid -> (name, unit, decoder(bytes) -> value, nbytes)
PIDS = {
    0x04: ("Engine load", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x05: ("Coolant temp", "°C", lambda d: d[0] - 40),
    0x06: ("Short fuel trim B1", "%", lambda d: round(d[0] * 100 / 128 - 100, 1)),
    0x07: ("Long fuel trim B1", "%", lambda d: round(d[0] * 100 / 128 - 100, 1)),
    0x08: ("Short fuel trim B2", "%", lambda d: round(d[0] * 100 / 128 - 100, 1)),
    0x09: ("Long fuel trim B2", "%", lambda d: round(d[0] * 100 / 128 - 100, 1)),
    0x0A: ("Fuel pressure", "kPa", lambda d: d[0] * 3),
    0x0B: ("Intake manifold pressure", "kPa", lambda d: d[0]),
    0x0C: ("Engine RPM", "rpm", lambda d: round(_u16(d) / 4)),
    0x0D: ("Vehicle speed", "km/h", lambda d: d[0]),
    0x0E: ("Timing advance", "°", lambda d: d[0] / 2 - 64),
    0x0F: ("Intake air temp", "°C", lambda d: d[0] - 40),
    0x10: ("MAF air flow", "g/s", lambda d: round(_u16(d) / 100, 2)),
    0x11: ("Throttle position", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x1F: ("Run time since engine start", "s", lambda d: _u16(d)),
    0x21: ("Distance with MIL on", "km", lambda d: _u16(d)),
    0x22: ("Fuel rail pressure (rel)", "kPa", lambda d: round(_u16(d) * 0.079, 1)),
    0x23: ("Fuel rail pressure", "kPa", lambda d: _u16(d) * 10),
    0x2C: ("Commanded EGR", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x2F: ("Fuel level", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x30: ("Warm-ups since codes cleared", "", lambda d: d[0]),
    0x31: ("Distance since codes cleared", "km", lambda d: _u16(d)),
    0x33: ("Barometric pressure", "kPa", lambda d: d[0]),
    0x42: ("Control module voltage", "V", lambda d: round(_u16(d) / 1000, 2)),
    0x43: ("Absolute load", "%", lambda d: round(_u16(d) * 100 / 255, 1)),
    0x44: ("Commanded AFR (lambda)", "λ", lambda d: round(_u16(d) * 2 / 65536, 3)),
    0x45: ("Relative throttle", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x46: ("Ambient air temp", "°C", lambda d: d[0] - 40),
    0x49: ("Accelerator pedal D", "%", lambda d: round(d[0] * 100 / 255, 1)),
    0x4D: ("Time run with MIL on", "min", lambda d: _u16(d)),
    0x4E: ("Time since codes cleared", "min", lambda d: _u16(d)),
    0x51: ("Fuel type", "", lambda d: FUEL_TYPES.get(d[0], d[0])),
    0x5C: ("Engine oil temp", "°C", lambda d: d[0] - 40),
    0x5E: ("Engine fuel rate", "L/h", lambda d: round(_u16(d) / 20, 2)),
    0x62: ("Actual engine torque", "%", lambda d: d[0] - 125),
    0x7F: ("Total engine run time", "h", lambda d: round(int.from_bytes(bytes(d[1:5]), "big") / 3600, 1)),
    0xA6: ("Odometer", "km", lambda d: round(int.from_bytes(bytes(d[:4]), "big") / 10, 1)),
}
FUEL_TYPES = {1: "Gasoline", 2: "Methanol", 3: "Ethanol", 4: "Diesel", 5: "LPG", 6: "CNG",
              8: "Electric", 9: "Bifuel gasoline", 17: "Hybrid gasoline", 19: "Hybrid diesel"}

LIVE_PIDS = [0x0C, 0x0D, 0x05, 0x04, 0x11, 0x0F, 0x10, 0x0B, 0x0E, 0x06, 0x07, 0x2F, 0x42,
             0x46, 0x5C, 0x5E, 0x1F, 0x33, 0x44, 0x45, 0x49, 0x62, 0x22, 0x23, 0x2C]
STAT_PIDS = [0x1F, 0x21, 0x30, 0x31, 0x4D, 0x4E, 0x7F, 0xA6, 0x51]
FREEZE_PIDS = [0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0F, 0x10, 0x11, 0x1F, 0x42]


class OBD:
    def __init__(self, elm):
        self.elm = elm
        self.supported: dict[str, set[int]] = {}     # ecu header -> set(pids)

    # ----------------------------------------------------------- mode 01
    def discover_pids(self):
        self.supported = {}
        base = 0x00
        while base <= 0xE0:
            msgs, _ = self.elm.request(f"01{base:02X}")
            more = False
            for hdr, d in msgs:
                if len(d) >= 6 and d[0] == 0x41 and d[1] == base:
                    bits = int.from_bytes(d[2:6], "big")
                    s = self.supported.setdefault(hdr, set())
                    for i in range(32):
                        if bits & (1 << (31 - i)):
                            s.add(base + i + 1)
                    more = more or bool(bits & 1)
            if not more:
                break
            base += 0x20
        return {h: sorted(s) for h, s in self.supported.items()}

    def all_supported(self):
        out = set()
        for s in self.supported.values():
            out |= s
        return out

    def read_pid(self, pid: int):
        msgs, _ = self.elm.request(f"01{pid:02X}", 2.0)
        results = []
        for hdr, d in msgs:
            if len(d) >= 3 and d[0] == 0x41 and d[1] == pid and pid in PIDS:
                name, unit, fn = PIDS[pid]
                try:
                    results.append({"ecu": ecu_name(hdr), "pid": f"{pid:02X}", "name": name,
                                    "value": fn(list(d[2:])), "unit": unit})
                except (IndexError, ValueError, TypeError):
                    pass
        return results

    def read_many(self, pids):
        sup = self.all_supported()
        out = []
        for p in pids:
            if p in sup:
                r = self.read_pid(p)
                if r:
                    out.append(r[0])        # first (usually engine) answer
        return out

    def monitor_status(self):
        msgs, _ = self.elm.request("0101")
        res = []
        for hdr, d in msgs:
            if len(d) < 6 or d[0] != 0x41 or d[1] != 0x01:
                continue
            a, b, c, dd = d[2:6]
            diesel = bool(b & 0x08)
            names_c = (["Catalyst", "Heated catalyst", "Evap system", "Secondary air",
                        "A/C refrigerant", "O2 sensor", "O2 sensor heater", "EGR system"]
                       if not diesel else
                       ["NMHC catalyst", "NOx/SCR", "(reserved)", "Boost pressure",
                        "(reserved)", "Exhaust gas sensor", "PM filter", "EGR/VVT"])
            monitors = []
            for i, nm in enumerate(["Misfire", "Fuel system", "Components"]):
                if b & (1 << i):
                    monitors.append({"name": nm, "complete": not (b & (1 << (i + 4)))})
            for i, nm in enumerate(names_c):
                if c & (1 << i) and "reserved" not in nm:
                    monitors.append({"name": nm, "complete": not (dd & (1 << i))})
            res.append({"ecu": ecu_name(hdr), "mil_on": bool(a & 0x80), "dtc_count": a & 0x7F,
                        "engine": "Diesel (compression)" if diesel else "Petrol (spark)",
                        "monitors": monitors})
        return res

    # ----------------------------------------------------------- DTC modes
    def _read_dtc_mode(self, mode: int):
        msgs, errs = self.elm.request(f"{mode:02X}", 6.0)
        resp_sid = mode + 0x40
        codes = []
        can = self.elm.protocol in (6, 7, 8, 9)
        for hdr, d in msgs:
            if not d or d[0] != resp_sid:
                continue
            body = d[2:] if can else d[1:]          # CAN has a DTC-count byte
            for i in range(0, len(body) - 1, 2):
                if body[i] == 0 and body[i + 1] == 0:
                    continue
                code = decode_dtc(body[i], body[i + 1])
                codes.append({"code": code, "ecu": ecu_name(hdr), "description": describe(code)})
        return codes

    def stored_dtcs(self):
        return self._read_dtc_mode(0x03)

    def pending_dtcs(self):
        return self._read_dtc_mode(0x07)

    def permanent_dtcs(self):
        return self._read_dtc_mode(0x0A)

    def freeze_frame(self):
        msgs, _ = self.elm.request("020200")
        ff = {"dtc": None, "values": []}
        for hdr, d in msgs:
            if len(d) >= 5 and d[0] == 0x42 and d[1] == 0x02:
                if d[3] or d[4]:
                    ff["dtc"] = decode_dtc(d[3], d[4])
                    ff["ecu"] = ecu_name(hdr)
        if not ff["dtc"]:
            return ff
        for pid in FREEZE_PIDS:
            msgs, _ = self.elm.request(f"02{pid:02X}00", 2.0)
            for _hdr, d in msgs:
                if len(d) >= 4 and d[0] == 0x42 and d[1] == pid and pid in PIDS:
                    name, unit, fn = PIDS[pid]
                    try:
                        ff["values"].append({"name": name, "value": fn(list(d[3:])), "unit": unit})
                    except (IndexError, ValueError):
                        pass
                    break
        return ff

    def clear_dtcs(self):
        msgs, errs = self.elm.request("04", 10.0)
        ok = any(d and d[0] == 0x44 for _, d in msgs)
        return {"ok": ok, "responders": [ecu_name(h) for h, d in msgs if d and d[0] == 0x44],
                "errors": errs}

    # ----------------------------------------------------------- mode 09
    def vin(self):
        msgs, _ = self.elm.request("0902", 5.0)
        for _hdr, d in msgs:
            if len(d) > 3 and d[0] == 0x49 and d[1] == 0x02:
                raw = d[3:] if self.elm.protocol in (6, 7, 8, 9) else d[2:]
                txt = "".join(chr(c) for c in raw if 32 < c < 127)
                if len(txt) >= 17:
                    return txt[-17:]
        if self.elm.protocol not in (6, 7, 8, 9):      # legacy: one frame per 4 bytes
            chars = []
            for _hdr, d in msgs:
                if len(d) >= 3 and d[0] == 0x49 and d[1] == 0x02:
                    chars += [chr(c) for c in d[3:] if 32 < c < 127]
            if len(chars) >= 17:
                return "".join(chars)[-17:]
        return None

    def ecu_names(self):
        msgs, _ = self.elm.request("090A", 5.0)
        out = {}
        for hdr, d in msgs:
            if len(d) > 3 and d[0] == 0x49 and d[1] == 0x0A:
                out[ecu_name(hdr)] = "".join(chr(c) for c in d[3:] if 32 <= c < 127).strip()
        return out
