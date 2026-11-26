"""Low-level transport for ELM327-compatible adapters (vLinker FS USB, OBDLink, etc).

Responsibilities:
  * open the serial port and auto-detect baud rate
  * send a command, read until the '>' prompt
  * split raw response lines into (header, data-bytes) frames
  * re-assemble ISO-TP (ISO 15765-2) multi-frame CAN messages
"""
from __future__ import annotations

import re
import threading
import time

import serial
import serial.tools.list_ports

HEX_TOKEN = re.compile(r"^[0-9A-F]+$")
ERROR_STRINGS = (
    "NO DATA", "UNABLE TO CONNECT", "CAN ERROR", "BUS ERROR", "BUS BUSY",
    "FB ERROR", "DATA ERROR", "BUFFER FULL", "STOPPED", "ERR", "?",
    "LV RESET", "ACT ALERT",
)

# ELM protocol numbers (ATDPN)
PROTOCOLS = {
    1: "SAE J1850 PWM (41.6 kbaud)",
    2: "SAE J1850 VPW (10.4 kbaud)",
    3: "ISO 9141-2 (5 baud init)",
    4: "ISO 14230-4 KWP (5 baud init)",
    5: "ISO 14230-4 KWP (fast init)",
    6: "ISO 15765-4 CAN (11 bit, 500 kbaud)",
    7: "ISO 15765-4 CAN (29 bit, 500 kbaud)",
    8: "ISO 15765-4 CAN (11 bit, 250 kbaud)",
    9: "ISO 15765-4 CAN (29 bit, 250 kbaud)",
    10: "SAE J1939 CAN (29 bit, 250 kbaud)",
}


class AdapterError(Exception):
    pass


def list_ports():
    ports = []
    for p in serial.tools.list_ports.comports():
        # macOS: prefer /dev/cu.* (call-out) devices over /dev/tty.*
        if p.device.startswith("/dev/tty.") :
            continue
        ports.append({
            "device": p.device,
            "description": p.description or "",
            "hwid": p.hwid or "",
            "likely_obd": bool(re.search(r"usbserial|FTDI|vLinker|OBD|wchusb|SLAB|usbmodem",
                                         f"{p.device} {p.description} {p.hwid}", re.I)),
        })
    ports.sort(key=lambda p: not p["likely_obd"])
    return ports


def is_can(protocol: int) -> bool:
    return protocol in (6, 7, 8, 9, 10, 11, 12)


def split_frames(lines, protocol):
    """Turn raw ELM lines (headers ON, spaces ON) into [(header_str, [bytes])]."""
    frames = []
    for ln in lines:
        toks = ln.split()
        if not toks or not all(HEX_TOKEN.match(t) for t in toks):
            continue
        if len(toks[0]) == 3:                      # 11-bit CAN id, e.g. 7E8
            hdr, data = toks[0], toks[1:]
        elif protocol in (7, 9, 10):               # 29-bit CAN, 4 header bytes
            if len(toks) < 5:
                continue
            hdr, data = "".join(toks[:4]), toks[4:]
        else:                                      # J1850 / ISO9141 / KWP: 3 hdr bytes + checksum
            if len(toks) < 5:
                continue
            hdr, data = "".join(toks[:3]), toks[3:-1]
        try:
            frames.append((hdr, [int(t, 16) for t in data]))
        except ValueError:
            continue
    return frames


def assemble(frames, protocol):
    """Return list of (header, bytes) complete messages."""
    if not is_can(protocol):
        return [(h, bytes(d)) for h, d in frames if d]
    out, pending = [], {}
    for hdr, d in frames:
        if not d:
            continue
        kind = d[0] >> 4
        if kind == 0:                               # single frame
            n = d[0] & 0x0F
            out.append((hdr, bytes(d[1:1 + n])))
        elif kind == 1 and len(d) >= 2:             # first frame
            total = ((d[0] & 0x0F) << 8) | d[1]
            pending[hdr] = [total, bytearray(d[2:])]
        elif kind == 2 and hdr in pending:          # consecutive frame
            pending[hdr][1] += bytes(d[1:])
            total, buf = pending[hdr]
            if len(buf) >= total:
                out.append((hdr, bytes(buf[:total])))
                del pending[hdr]
        # kind 3 = flow control from someone else; ignore
    for hdr, (total, buf) in pending.items():       # incomplete — return what we have
        out.append((hdr, bytes(buf[:total])))
    return out


class ELM327:
    """Real adapter over a serial port."""

    BAUDS = [115200, 38400, 500000, 230400, 1000000, 2000000, 9600]

    def __init__(self, port: str, baud: int | None = None, logger=None):
        self.port = port
        self.baud = baud
        self.ser: serial.Serial | None = None
        self.protocol = 0
        self.identity = ""
        self.log = logger or (lambda *_: None)
        self.lock = threading.RLock()

    # ---------- raw I/O ----------
    def _write_read(self, cmd: str, timeout: float) -> str:
        assert self.ser
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode("ascii"))
        self.ser.flush()
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buf += chunk
                if b">" in chunk:
                    break
        text = buf.decode("ascii", errors="replace").replace("\x00", "")
        self.log(f">> {cmd}\n<< {text.strip()}")
        return text

    def query(self, cmd: str, timeout: float = 5.0) -> list[str]:
        """Send a command, return cleaned response lines (no echo, no prompt)."""
        with self.lock:
            text = self._write_read(cmd, timeout)
        lines = []
        for raw in re.split(r"[\r\n]+", text.replace(">", "")):
            ln = raw.strip()
            if not ln or ln == cmd or ln.startswith("SEARCHING") or ln.startswith("BUS INIT"):
                continue
            lines.append(ln)
        return lines

    def at(self, cmd: str, timeout: float = 2.0) -> str:
        return " ".join(self.query(cmd, timeout))

    # ---------- lifecycle ----------
    def connect(self):
        bauds = [self.baud] if self.baud else self.BAUDS
        last_err = None
        for b in bauds:
            try:
                self.ser = serial.Serial(self.port, b, timeout=0.05, write_timeout=2)
            except serial.SerialException as e:
                raise AdapterError(f"Cannot open {self.port}: {e}") from e
            try:
                self.ser.write(b"\r\r")
                time.sleep(0.15)
                self.ser.reset_input_buffer()
                resp = self._write_read("ATI", 1.5)
                if ">" in resp or "ELM" in resp.upper():
                    self.baud = b
                    break
            except serial.SerialException as e:
                last_err = e
            self.ser.close()
            self.ser = None
        if not self.ser:
            raise AdapterError(f"No ELM327 response on {self.port} (tried {bauds}). {last_err or ''}")

        self.at("ATZ", 3.0)              # reset
        time.sleep(0.5)
        self.at("ATE0")                  # echo off
        self.at("ATL0")                  # linefeeds off
        self.at("ATS1")                  # spaces on (easier parsing)
        self.at("ATH1")                  # headers on (know which ECU answered)
        self.at("ATAT1")                 # adaptive timing
        self.identity = self.at("ATI")
        self.at("ATSP0")                 # automatic protocol search
        first = self.query("0100", 15.0)  # forces the protocol search
        if any("UNABLE TO CONNECT" in ln for ln in first):
            raise AdapterError("Adapter is OK but could not talk to the car. "
                               "Turn ignition ON (or start engine) and retry.")
        dpn = self.at("ATDPN").replace("A", "").strip()
        try:
            self.protocol = int(dpn, 16)
        except ValueError:
            self.protocol = 0
        return {"baud": self.baud, "identity": self.identity, "protocol": self.protocol,
                "protocol_name": PROTOCOLS.get(self.protocol, f"#{dpn}")}

    def restore(self):
        """Return adapter to the state set up by connect() (after a module scan)."""
        for c in ("ATFCSM0", "ATCRA", "ATAT1", "ATST32", "ATH1", "ATS1", "ATCAF1"):
            self.at(c)
        if self.protocol in (6, 8):
            self.at("ATSH7DF")           # OBD functional (broadcast) address
        elif self.protocol in (7, 9):
            self.at("ATSH18DB33F1")

    def voltage(self):
        v = self.at("ATRV")
        m = re.search(r"(\d+\.\d+)", v)
        return float(m.group(1)) if m else None

    def close(self):
        try:
            if self.ser:
                self.ser.close()
        finally:
            self.ser = None

    # ---------- messages ----------
    def request(self, cmd: str, timeout: float = 5.0):
        lines = self.query(cmd, timeout)
        errors = [ln for ln in lines if any(ln.startswith(e) for e in ERROR_STRINGS)]
        msgs = assemble(split_frames(lines, self.protocol), self.protocol)
        return msgs, errors
