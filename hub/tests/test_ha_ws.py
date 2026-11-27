"""The stdlib websocket client against a minimal fake HA websocket server."""
import base64
import hashlib
import json
import socket
import struct
import threading

import pytest

from ha import GUID, HA, HAError


def read_exact(c, n):
    out = b""
    while len(out) < n:
        chunk = c.recv(n - len(out))
        if not chunk:
            raise OSError("closed")
        out += chunk
    return out


def read_frame(c):
    b0, b1 = read_exact(c, 2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", read_exact(c, 2))[0]
    mask = read_exact(c, 4)
    assert b1 & 0x80, "client frames must be masked"
    return json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(read_exact(c, n))))


def write_frame(c, obj, big=False):
    data = json.dumps(obj).encode()
    head = bytes([0x81]) + (bytes([127]) + struct.pack(">Q", len(data)) if big else
                            bytes([126]) + struct.pack(">H", len(data)) if len(data) > 125 else bytes([len(data)]))
    c.sendall(head + data)


@pytest.fixture
def server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen = []

    def run():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            req = b""
            while b"\r\n\r\n" not in req:
                req += c.recv(1024)
            key = [l for l in req.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0].split(": ")[1]
            acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
            write_frame(c, {"type": "auth_required"})
            auth = read_frame(c)
            seen.append(auth)
            if auth.get("access_token") != "tok":
                write_frame(c, {"type": "auth_invalid"})
                continue
            write_frame(c, {"type": "auth_ok"})
            msg = read_frame(c)
            seen.append(msg)
            if msg["type"] == "subscribe_events":
                c.sendall(bytes([0x89, 0]))  # a ping first: must be answered or skipped
                write_frame(c, {"id": 1, "type": "event", "event": {"event_type": "state_changed", "data": {
                    "entity_id": "sensor.x_current_stage", "new_state": {"state": "changing_filament"}}}})
                c.close()
            elif msg["type"] == "boom":
                write_frame(c, {"id": 1, "type": "result", "success": False, "error": {"message": "nope"}})
            else:
                write_frame(c, {"id": 1, "type": "result", "success": True, "result": [{"n": "x" * 70000}]}, big=True)
            c.close()
    threading.Thread(target=run, daemon=True).start()
    yield "ws://127.0.0.1:%d/core/websocket" % srv.getsockname()[1], seen
    srv.close()


def test_command_roundtrip_with_a_large_frame(server):
    url, seen = server
    result = HA("http://x", url, "tok").command("config/auth/list", extra=1)
    assert result == [{"n": "x" * 70000}]
    assert seen[0] == {"type": "auth", "access_token": "tok"} and seen[1] == {"extra": 1, "id": 1, "type": "config/auth/list"}


def test_errors(server):
    url, _ = server
    with pytest.raises(HAError, match="nope"):
        HA("http://x", url, "tok").command("boom")
    with pytest.raises(HAError, match="refused"):
        HA("http://x", url, "bad").command("x")


def test_watch_state_changes(server):
    url, _ = server
    got, stop = [], threading.Event()
    ha = HA("http://x", url, "tok")

    def cb(eid, state):
        got.append((eid, state))
        stop.set()
    ha.watch_state_changes(cb, stop)
    assert got == [("sensor.x_current_stage", "changing_filament")]


def test_unreachable_ha_is_an_haerror_not_a_crash():
    with pytest.raises(HAError, match="not reachable"):
        HA("http://x", "ws://127.0.0.1:1/core/websocket", "tok").command("config/auth/list")
