"""Owns the (single) physical adapter: connection, locking, background module scans.

One USB adapter can only talk to one car at a time, so the manager records which
user connected it; other users get a clear "in use" error instead of racing for
the serial port.
"""
from __future__ import annotations

import collections
import threading
import time
import traceback

from .obd import ELM327, OBD, PROTOCOLS, AdapterError, ModuleScanner, SimulatedELM
from .obd.obd import LIVE_PIDS, STAT_PIDS


class Busy(Exception):
    pass


class NotOwner(Exception):
    pass


class AdapterManager:
    def __init__(self, elm_factory=None):
        self._factory = elm_factory or self._default_factory
        self.elm = None
        self.obd: OBD | None = None
        self.info: dict = {}
        self.owner: int | None = None
        self.lock = threading.Lock()
        self.log = collections.deque(maxlen=500)
        self.scan: dict = {"running": False}
        self.scanner: ModuleScanner | None = None
        self.modules: list = []

    @staticmethod
    def _default_factory(port, baud, simulate, scenario, logger):
        if simulate:
            return SimulatedELM(logger, scenario=scenario or "genuine")
        if not port:
            raise AdapterError("Choose a port, or use the demo car.")
        return ELM327(port, baud, logger)

    def logger(self, msg: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')} {msg}")

    # ------------------------------------------------------------ ownership
    def check(self, user_id: int) -> None:
        if not self.elm:
            raise AdapterError("No adapter connected.")
        if self.owner != user_id:
            raise NotOwner("The adapter is in use by another account.")

    def status(self, user_id: int) -> dict:
        mine = self.elm is not None and self.owner == user_id
        return {
            "connected": mine,
            "in_use_by_other": self.elm is not None and not mine,
            "info": self.info if mine else {},
            "scan": {k: v for k, v in self.scan.items() if k != "result"} if mine else {"running": False},
        }

    def run(self, fn, wait: float = 8.0):
        if not self.lock.acquire(timeout=wait):
            raise Busy("The adapter is busy (a module scan may be running).")
        try:
            return fn()
        finally:
            self.lock.release()

    # ------------------------------------------------------------ lifecycle
    def connect(self, user_id: int, port: str | None, baud: int | None = None,
                simulate: bool = False, scenario: str | None = None) -> dict:
        if self.elm and self.owner not in (None, user_id):
            raise NotOwner("The adapter is in use by another account.")

        def go():
            self._close()
            elm = self._factory(port, baud, simulate, scenario, self.logger)
            try:
                info = elm.connect()
            except Exception:
                elm.close()
                raise
            obd = OBD(elm)
            info["port"] = elm.port
            info["simulated"] = bool(simulate)
            info["supported_pids"] = obd.discover_pids()
            info["voltage"] = elm.voltage()
            info["vin"] = obd.vin()
            self.elm, self.obd, self.info, self.owner = elm, obd, info, user_id
            self.modules, self.scan = [], {"running": False}
            return info
        return self.run(go, wait=20)

    def _close(self):
        if self.scanner:
            self.scanner.cancel = True
        if self.elm:
            try:
                self.elm.close()
            except Exception:  # pragma: no cover - best effort on hardware errors
                self.logger("Error while closing the adapter (ignored).")
        self.elm = self.obd = None
        self.info, self.owner = {}, None

    def disconnect(self, user_id: int) -> None:
        if self.elm and self.owner != user_id:
            raise NotOwner("The adapter is in use by another account.")
        self._close()

    # ------------------------------------------------------------ reads
    def live(self, user_id: int) -> dict:
        self.check(user_id)
        return self.run(lambda: {"values": self.obd.read_many(LIVE_PIDS), "voltage": self.elm.voltage()}, wait=1)

    def read_pids(self, user_id: int, pids: list[int]) -> dict:
        self.check(user_id)
        return self.run(lambda: {"values": self.obd.read_many(pids), "voltage": self.elm.voltage()}, wait=2)

    def vehicle(self, user_id: int) -> dict:
        self.check(user_id)

        def go():
            o = self.obd
            return {"vin": o.vin(), "ecu_names": o.ecu_names(), "monitors": o.monitor_status(),
                    "stats": o.read_many(STAT_PIDS), "voltage": self.elm.voltage(),
                    "protocol": PROTOCOLS.get(self.elm.protocol, str(self.elm.protocol))}
        return self.run(go)

    def obd_codes(self, user_id: int) -> dict:
        self.check(user_id)

        def go():
            o = self.obd
            return {"stored": o.stored_dtcs(), "pending": o.pending_dtcs(),
                    "permanent": o.permanent_dtcs(), "freeze_frame": o.freeze_frame()}
        return self.run(go)

    def mileage_evidence(self, user_id: int) -> dict:
        """Everything the car itself can tell us about its mileage."""
        self.check(user_id)

        def go():
            out = {"module_readings": [], "fault_stamps": [], "engine_hours": None, "obd_odometer_km": None}
            for r in self.obd.read_many([0xA6, 0x7F]):
                if r["pid"] == "A6":
                    out["obd_odometer_km"] = r["value"]
                elif r["pid"] == "7F":
                    out["engine_hours"] = r["value"]
            if self.elm.protocol in (6, 7, 8, 9):
                sc = ModuleScanner(self.elm)
                ids = [m["request_id"] for m in self.modules] or (["18DA10F1"] if sc.bits29 else ["7E0"])
                for r in sc.read_odometer(ids):
                    name = next((m["name"] for m in self.modules if m["request_id"] == r["module"]), r["module"])
                    out["module_readings"].append({"module": r["module"], "name": name, "km": r["km"],
                                                   "source": r["label"]})
                if self.modules:
                    out["fault_stamps"] = sc.fault_mileage_stamps(self.modules)
            return out
        return self.run(go, wait=20)

    def clear(self, user_id: int, target: str) -> dict:
        self.check(user_id)
        if target == "obd":
            return self.run(self.obd.clear_dtcs, wait=15)
        return self.run(lambda: ModuleScanner(self.elm).clear_module(target), wait=15)

    # ------------------------------------------------------------ module scan
    def start_scan(self, user_id: int, full: bool, on_done=None) -> dict:
        self.check(user_id)
        if self.scan.get("running"):
            raise Busy("A scan is already running.")
        if not self.lock.acquire(timeout=8):
            raise Busy("The adapter is busy.")
        self.scan = {"running": True, "done": 0, "total": 1, "found": 0, "current": "", "started": time.time()}

        def progress(**kw):
            self.scan.update(kw)

        def work():
            try:
                self.scanner = ModuleScanner(self.elm, progress)
                mods = self.scanner.scan(full=full)
                self.modules = mods
                self.scan.update(running=False, result=mods, finished=time.time(), cancelled=self.scanner.cancel)
            except Exception as e:  # surfaced to the client via scan status
                self.logger(traceback.format_exc())
                self.scan.update(running=False, error=str(e))
            finally:
                self.scanner = None
                self.lock.release()
            if on_done and not self.scan.get("error"):
                try:
                    on_done(self.modules)
                except Exception:  # pragma: no cover
                    self.logger(traceback.format_exc())

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    def cancel_scan(self, user_id: int) -> None:
        self.check(user_id)
        if self.scanner:
            self.scanner.cancel = True

    def scan_result(self, user_id: int) -> dict:
        self.check(user_id)
        return {**self.scan, "result": self.scan.get("result", self.modules)}
