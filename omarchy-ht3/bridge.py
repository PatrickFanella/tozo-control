#!/usr/bin/python3
"""HT3 RFCOMM transport. One JSON request/response per line; no GTK or pip.

Protocol observations and provenance are recorded in PROTOCOL.md.
"""
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

FREQUENCIES = [5, 6, 7, 8, 10, 12, 14, 16, 18, 20]
MODES = {"normal": (4, 0, 0), "anc": (4, 1, 1), "transparency": (5, 1, 2)}
MODE_NAMES = {0: "normal", 1: "anc", 2: "transparency", 3: "wind", 4: "leisure", 6: "adaptive"}
LENGTHS = {2: 1, 11: 20, 48: 1, 19: 1}


def packet(parameter, payload=None):
    data = bytes(payload or [])
    return bytes([0 if payload is None else 16, parameter, len(data)]) + data + bytes([sum(data) & 255])


class Frames:
    """Recover known replies from fragmented/coalesced RFCOMM stream data."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        replies = []
        while len(self.buffer) >= 3:
            opcode, parameter, size = self.buffer[:3]
            if opcode != 0 or LENGTHS.get(parameter) != size:
                del self.buffer[0]
                continue
            total = size + 4
            if len(self.buffer) < total:
                break
            payload = bytes(self.buffer[3:3 + size])
            if sum(payload) & 255 != self.buffer[total - 1]:
                del self.buffer[0]
                continue
            replies.append((parameter, payload))
            del self.buffer[:total]
        return replies


def decode(parameter, data):
    if len(data) != LENGTHS.get(parameter):
        raise ValueError("Unexpected response length")
    if parameter == 2 and data[0] <= 100:
        return {"battery": data[0]}
    if parameter == 48 and data[0] in MODE_NAMES:
        return {"mode": MODE_NAMES[data[0]]}
    if parameter == 19 and data[0] in (0, 1):
        return {"spatial": bool(data[0])}
    if parameter == 11:
        gains = [value if value < 128 else value - 256 for value in data[:10]]
        if all(-50 <= value <= 50 for value in gains) and list(data[10:]) == FREQUENCIES:
            return {"eq": gains}
    raise ValueError("Headphones returned an unrecognized value")


def devices():
    result = subprocess.run(["bluetoothctl", "devices", "Paired"], capture_output=True, text=True, timeout=5, check=True)
    found = []
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    for line in output.splitlines():
        match = re.fullmatch(r"Device ([0-9A-Fa-f:]{17}) (.+)", line)
        if match and "HT3" in match[2].upper() and "TOZO" in match[2].upper():
            found.append({"address": match[1].upper(), "name": match[2]})
    return found


class Headphones:
    def __init__(self):
        self.sock = None
        self.lock = None
        self.state = {"connected": False}

    def close(self):
        if self.sock:
            self.sock.close()
        if self.lock:
            self.lock.close()
        self.sock = self.lock = None
        self.state = {"connected": False}

    def connect(self, address):
        if address not in {device["address"] for device in devices()}:
            raise ValueError("Select a paired TOZO HT3 first")
        self.close()
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        self.lock = (runtime / ("omarchy-ht3-" + address.replace(":", "") + ".lock")).open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            self.sock.settimeout(5)
            self.sock.connect((address, 1))
            self.state = {"connected": True, "address": address}
            self.refresh()
        except Exception:
            self.close()
            raise

    def query(self, parameter):
        if not self.sock:
            raise ValueError("Connect the headphone controls first")
        # Discard old telemetry before requesting a new value. Commands are serialized.
        self.sock.settimeout(0.01)
        while True:
            try:
                if not self.sock.recv(4096):
                    raise ConnectionError("Headphones disconnected")
            except socket.timeout:
                break
        self.sock.sendall(packet(parameter))
        parser = Frames()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.01, deadline - time.monotonic()))
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("Headphones disconnected")
            for key, payload in parser.feed(data):
                if key == parameter:
                    value = decode(key, payload)
                    self.state.update(value)
                    return value
        raise TimeoutError("No matching headphone response")

    def refresh(self):
        # Never leave stale values enabled after a failed read.
        for parameter, field in ((2, "battery"), (48, "mode"), (19, "spatial"), (11, "eq")):
            self.state.pop(field, None)
            try:
                self.query(parameter)
            except (TimeoutError, ValueError):
                continue

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("Unknown noise control mode")
        self.query(48)
        parameter, value, _ = MODES[mode]
        self.sock.sendall(packet(parameter, [value]))
        time.sleep(0.15)
        self.state.pop("mode", None)
        if self.query(48)["mode"] != mode:
            raise ValueError("Headphones did not confirm the requested mode")

    def set_eq(self, gains):
        if not isinstance(gains, list) or len(gains) != 10 or any(type(x) is not int or not -50 <= x <= 50 for x in gains):
            raise ValueError("EQ needs ten integer gains between -50 and 50")
        if self.query(19)["spatial"]:
            raise ValueError("Turn off spatial audio on the headphones before changing EQ")
        self.query(11)
        self.sock.sendall(packet(11, [x & 255 for x in gains] + FREQUENCIES + [1]))
        time.sleep(0.15)
        self.state.pop("eq", None)
        if self.query(11)["eq"] != gains:
            raise ValueError("Headphones did not confirm the requested EQ")


def main():
    headphones = Headphones()
    try:
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Expected a JSON object")
                action = request.get("action")
                result = {}
                if action == "devices":
                    result["devices"] = devices()
                elif action == "connect":
                    headphones.connect(request.get("address", ""))
                elif action == "refresh":
                    headphones.refresh()
                elif action == "mode":
                    headphones.set_mode(request.get("mode"))
                elif action == "eq":
                    headphones.set_eq(request.get("gains"))
                elif action == "disconnect":
                    headphones.close()
                else:
                    raise ValueError("Unknown action")
                response = {"ok": True, **result}
            except Exception as error:
                if isinstance(error, (ConnectionError, OSError)) and not isinstance(error, TimeoutError):
                    headphones.close()
                response = {"ok": False, "error": str(error) or type(error).__name__}
            response["action"] = request.get("action") if isinstance(request, dict) else None
            response["state"] = headphones.state.copy()
            print(json.dumps(response), flush=True)
    finally:
        headphones.close()


if __name__ == "__main__":
    main()
