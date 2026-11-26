"""A fake car + ELM327 adapter used for development, demos and tests.

Emulates an 11-bit 500 kbaud CAN vehicle (ELM protocol 6) with several ECUs that
answer OBD-II (modes 01/02/03/04/07/09/0A) and UDS (0x10, 0x14, 0x19, 0x22, 0x3E).
Output is formatted exactly like a real ELM327 with ``ATH1 ATS1`` (headers and
spaces on), so the real parsing code is exercised end to end.

Scenarios
---------
``genuine``  every stored mileage is consistent with the instrument cluster.
``rollback`` the cluster reads ~48,000 km less than the other modules and the
             mileage stamped on past fault codes - a rolled-back odometer.
"""
from __future__ import annotations

import math
import random
import time

from .elm327 import PROTOCOLS, assemble, split_frames

TRUE_ODOMETER_KM = 148_290          # what the engine/body modules remember
ROLLBACK_ODOMETER_KM = 100_260      # what a tampered cluster shows
ENGINE_HOURS = 2_940


def _dtc_bytes(code: str) -> list[int]:
    letter = "PCBU".index(code[0])
    b1 = (letter << 6) | (int(code[1]) << 4) | int(code[2], 16)
    return [b1, int(code[3:5], 16)]


def _isotp(hdr: str, payload: list[int]) -> list[str]:
    def fmt(b):
        b = b + [0x00] * (8 - len(b))
        return hdr + " " + " ".join(f"{x:02X}" for x in b)
    if len(payload) <= 7:
        return [fmt([len(payload)] + payload)]
    lines = [fmt([0x10 | (len(payload) >> 8), len(payload) & 0xFF] + payload[:6])]
    rest, seq = payload[6:], 1
    while rest:
        lines.append(fmt([0x20 | (seq & 0xF)] + rest[:7]))
        rest, seq = rest[7:], seq + 1
    return lines


class SimModule:
    def __init__(self, req, name, uds_dtcs=None, obd=None, odo_km=None):
        self.req = req
        self.resp = req + 8
        self.name = name
        # [(code, failure_type_byte, status_byte, odometer_km_when_stored)]
        self.uds_dtcs = list(uds_dtcs or [])
        self.obd = obd
        self.odo_km = odo_km


class SimulatedELM:
    """Drop-in replacement for :class:`carscan.obd.elm327.ELM327`."""

    def __init__(self, logger=None, scenario: str = "genuine", seed: int | None = None):
        self.port = "SIMULATOR"
        self.baud = 0
        self.protocol = 6
        self.scenario = scenario
        self.identity = "ELM327 v2.3 (vLinker FS simulator)"
        self.log = logger or (lambda *_: None)
        self.header = "7DF"
        self.t0 = time.monotonic()
        self.rng = random.Random(seed)
        self.vin = "1FTFW1ET9DFC10312"
        self.closed = False
        cluster_km = ROLLBACK_ODOMETER_KM if scenario == "rollback" else TRUE_ODOMETER_KM + 3
        engine_obd = {"stored": ["P0301", "P0420"], "pending": ["P0171"], "permanent": ["P0420"],
                      "freeze_dtc": "P0301"}
        trans_obd = {"stored": ["P0700"], "pending": [], "permanent": [], "freeze_dtc": None}
        self.modules = {m.req: m for m in [
            SimModule(0x7E0, "ECM Engine", [("P0301", 0x00, 0x2F, 148_225), ("P0420", 0x00, 0x08, 135_540),
                                            ("P0171", 0x00, 0x24, 148_275)], engine_obd, odo_km=TRUE_ODOMETER_KM),
            SimModule(0x7E1, "TCM Transmission", [("P0700", 0x00, 0x09, 148_225)], trans_obd),
            SimModule(0x760, "ABS Brakes", [("C0035", 0x1C, 0x08, 142_284), ("U0100", 0x00, 0x28, 142_284)]),
            SimModule(0x737, "SRS Airbag", [("B0012", 0x13, 0x0B, 147_850)]),
            SimModule(0x726, "BCM Body", [("B1342", 0x00, 0x28, 144_843), ("U0140", 0x87, 0x08, 144_843)],
                      odo_km=TRUE_ODOMETER_KM - 64),
            SimModule(0x720, "IPC Cluster", [], odo_km=cluster_km),
            SimModule(0x7D0, "APIM Infotainment", []),
            SimModule(0x716, "GWM Gateway", [("U3003", 0x16, 0x08, 139_930)]),
        ]}

    # ------------------------------------------------------------ interface
    def connect(self):
        return {"baud": 0, "identity": self.identity, "protocol": 6, "protocol_name": PROTOCOLS[6]}

    def restore(self):
        self.header = "7DF"

    def voltage(self):
        return round(13.9 + self.rng.uniform(-0.1, 0.1), 1)

    def close(self):
        self.closed = True

    def at(self, cmd, timeout=2.0):
        return " ".join(self.query(cmd, timeout))

    def request(self, cmd, timeout=5.0):
        lines = self.query(cmd, timeout)
        errors = [ln for ln in lines if ln in ("NO DATA", "?")]
        return assemble(split_frames(lines, 6), 6), errors

    def query(self, cmd, timeout=5.0):
        c = cmd.replace(" ", "").upper()
        out = self._handle(c)
        self.log(f">> {cmd}\n<< {' | '.join(out)}")
        return out

    # ------------------------------------------------------------ emulation
    def _handle(self, c):
        if c.startswith("AT"):
            if c.startswith("ATSH"):
                self.header = c[4:]
            if c in ("ATI", "ATZ"):
                return [self.identity]
            if c == "ATRV":
                return [f"{self.voltage()}V"]
            if c == "ATDPN":
                return ["A6"]
            return ["OK"]
        try:
            req = [int(c[i:i + 2], 16) for i in range(0, len(c), 2)]
        except ValueError:
            return ["?"]
        if not req:
            return ["?"]
        if self.header == "7DF":
            targets = [m for m in self.modules.values() if m.obd]
        else:
            try:
                m = self.modules.get(int(self.header, 16))
            except ValueError:
                m = None
            targets = [m] if m else []
        lines = []
        for m in targets:
            resp = self._respond(m, req)
            if resp is not None:
                lines += _isotp(f"{m.resp:03X}", resp)
        return lines or ["NO DATA"]

    def _live(self):
        t = time.monotonic() - self.t0
        rpm = 780 + 600 * (1 + math.sin(t / 4)) + self.rng.uniform(-15, 15)
        speed = max(0, 35 + 30 * math.sin(t / 9))
        odo_tenths = (ROLLBACK_ODOMETER_KM if self.scenario == "rollback" else TRUE_ODOMETER_KM) * 10
        run_s = ENGINE_HOURS * 3600
        return {
            0x01: [0x82, 0x07, 0x65, 0x04],
            0x04: [int(25 + 20 * (1 + math.sin(t / 4)))],
            0x05: [int(128 + self.rng.uniform(-1, 1))],
            0x06: [0x7E], 0x07: [0x81],
            0x0B: [int(35 + 10 * (1 + math.sin(t / 4)))],
            0x0C: list(divmod(int(rpm * 4), 256)),
            0x0D: [int(speed)],
            0x0E: [0x90],
            0x0F: [67],
            0x10: list(divmod(int(450 + 300 * (1 + math.sin(t / 4))), 256)),
            0x11: [int(30 + 20 * (1 + math.sin(t / 4)))],
            0x1F: list(divmod(int(t) + 312, 256)),
            0x21: [0x00, 0x7B],
            0x2F: [0xA8],
            0x30: [0x0C],
            0x31: [0x05, 0x3A],
            0x33: [0x65],
            0x42: list(divmod(int(self.voltage() * 1000), 256)),
            0x46: [0x3C],
            0x4D: [0x00, 0x5A],
            0x4E: [0x07, 0xD0],
            0x5C: [0x7F],
            0x7F: [0x01] + list(run_s.to_bytes(4, "big")) + [0] * 8,
            0xA6: list(odo_tenths.to_bytes(4, "big")),
        }

    @staticmethod
    def _bitmap(base, have):
        val = 0
        for pid in have:
            if base < pid <= base + 32:
                val |= 1 << (32 - (pid - base))
        if any(p > base + 32 for p in have):
            val |= 1
        return list(val.to_bytes(4, "big"))

    def _respond(self, m: SimModule, req):  # noqa: C901 - a protocol switchboard
        sid = req[0]
        obd = m.obd
        # ---------------- OBD-II ----------------
        if sid == 0x01 and obd and len(req) >= 2:
            live = self._live() if m.req == 0x7E0 else {0x01: [0, 0, 0, 0], 0x1F: [0, 50]}
            pid = req[1]
            if pid % 0x20 == 0:
                return [0x41, pid] + self._bitmap(pid, live.keys())
            return [0x41, pid] + live[pid] if pid in live else None
        if sid == 0x02 and obd and len(req) >= 2:
            if not obd["freeze_dtc"]:
                return None
            pid = req[1]
            if pid == 0x02:
                return [0x42, 0x02, 0x00] + _dtc_bytes(obd["freeze_dtc"])
            frozen = {0x04: [0x5A], 0x05: [0x48], 0x0C: [0x0C, 0xB0], 0x0D: [0x00], 0x0F: [0x2C],
                      0x11: [0x33], 0x06: [0x8C], 0x07: [0x90], 0x0B: [0x40], 0x1F: [0x00, 0x30]}
            if pid % 0x20 == 0:
                return [0x42, pid, 0x00] + self._bitmap(pid, list(frozen) + [0x02])
            return [0x42, pid, 0x00] + frozen[pid] if pid in frozen else None
        if sid in (0x03, 0x07, 0x0A) and obd:
            codes = obd[{0x03: "stored", 0x07: "pending", 0x0A: "permanent"}[sid]]
            body = [sid + 0x40, len(codes)]
            for cd in codes:
                body += _dtc_bytes(cd)
            return body
        if sid == 0x04 and obd:
            obd["stored"], obd["pending"], obd["freeze_dtc"] = [], [], None
            m.uds_dtcs = []
            return [0x44]
        if sid == 0x09 and obd and len(req) >= 2:
            if req[1] == 0x00:
                return [0x49, 0x00, 0x55, 0x40, 0x00, 0x00]
            if req[1] == 0x02 and m.req == 0x7E0:
                return [0x49, 0x02, 0x01] + list(self.vin.encode())
            if req[1] == 0x0A:
                return [0x49, 0x0A, 0x01] + list(m.name.ljust(20, "\0").encode()[:20])
            return None
        # ---------------- UDS ----------------
        if sid == 0x3E:
            return [0x7E, 0x00]
        if sid == 0x10 and len(req) >= 2:
            return [0x50, req[1], 0x00, 0x32, 0x01, 0xF4]
        if sid == 0x19 and len(req) >= 3 and req[1] == 0x02:
            body = [0x59, 0x02, 0xFF]
            for code, ftb, status, _odo in m.uds_dtcs:
                if status & req[2]:
                    body += _dtc_bytes(code) + [ftb, status]
            return body
        if sid == 0x19 and len(req) >= 6 and req[1] == 0x04:
            want = req[2:5]
            for code, ftb, status, odo in m.uds_dtcs:
                if _dtc_bytes(code) + [ftb] == want:
                    # record 1 with two DIDs: DD01 odometer (3 bytes, km) and F40D speed (1 byte)
                    return [0x59, 0x04] + want + [status, 0x01, 0x02, 0xDD, 0x01] + \
                        list(odo.to_bytes(3, "big")) + [0xF4, 0x0D, 0x2D]
            return [0x7F, 0x19, 0x31]
        if sid == 0x14:
            m.uds_dtcs = []
            if obd:
                obd["stored"], obd["pending"], obd["freeze_dtc"] = [], [], None
            return [0x54]
        if sid == 0x22 and len(req) >= 3:
            did = (req[1] << 8) | req[2]
            if did == 0xF190 and m.req in (0x7E0, 0x726):
                return [0x62, 0xF1, 0x90] + list(self.vin.encode())
            if did == 0xF197:
                return [0x62, 0xF1, 0x97] + list(m.name.encode())
            if did == 0xF188:
                return [0x62, 0xF1, 0x88] + list(f"SIM-{m.req:03X}-AA".encode())
            if did == 0xDD01 and m.odo_km and m.req != 0x7E0:
                return [0x62, 0xDD, 0x01] + list(m.odo_km.to_bytes(3, "big"))
            if did == 0xF4A6 and m.req == 0x7E0 and m.odo_km:
                return [0x62, 0xF4, 0xA6] + list((m.odo_km * 10).to_bytes(4, "big"))
            return [0x7F, 0x22, 0x31]
        return [0x7F, sid, 0x11]
