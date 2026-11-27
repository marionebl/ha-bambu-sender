"""Home Assistant access for the hub app: Supervisor Core REST API plus a minimal websocket client (stdlib only).

A pinned websocket library would be one more wheel to build for aarch64; the protocol parts needed here
(client handshake, masked text frames, ping/close) are ~60 lines, and test_ha_ws.py runs them against a fake server.
"""
import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class HAError(Exception):
    """HA refused or could not be reached; the text is HA's own where it gave one."""


class WebSocket:
    def __init__(self, url, timeout=30):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                           % (u.path or "/", u.netloc, key)).encode())
        self.buf = b""
        head = self._until(b"\r\n\r\n")
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if not head.startswith(b"HTTP/1.1 101") or want.encode() not in head:
            raise HAError("websocket handshake refused")

    def _until(self, marker):
        while marker not in self.buf:
            self._fill()
        head, _, self.buf = self.buf.partition(marker)
        return head

    def _fill(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise HAError("websocket closed")
        self.buf += chunk

    def _take(self, n):
        while len(self.buf) < n:
            self._fill()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode, payload):
        mask = os.urandom(4)  # clients must mask
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else
                                         bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                         bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, obj):
        self._send_frame(1, json.dumps(obj).encode())

    def recv(self):
        """Next JSON message; answers pings, raises HAError when the peer closes."""
        data = b""
        while True:
            b0, b1 = self._take(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._take(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._take(8))[0]
            payload = self._take(n)  # servers do not mask
            op = b0 & 0x0F
            if op == 8:
                raise HAError("websocket closed")
            if op == 9:
                self._send_frame(10, payload)
                continue
            if op == 10:
                continue
            data += payload
            if b0 & 0x80:
                return json.loads(data)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class HA:
    def __init__(self, core_url, ws_url, token, clock=time.time):
        self.core, self.ws_url, self.token, self.clock = core_url.rstrip("/"), ws_url, token, clock
        self._states = (0, {})
        self._lock = threading.Lock()

    # -- REST --
    def _rest(self, method, path, body=None, timeout=30):
        req = urllib.request.Request(self.core + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                d = json.loads(raw)
                text = d.get("message") or d.get("error") or str(d)
            except (ValueError, AttributeError):
                text = raw.decode(errors="replace")[:300]
            raise HAError("Home Assistant answered %s: %s" % (e.code, text))
        except OSError as e:
            raise HAError("Home Assistant is not reachable: %s" % e)

    def state(self, entity_id):
        """Fresh state string, or None for an unknown entity."""
        try:
            return json.loads(self._rest("GET", "/states/" + urllib.parse.quote(entity_id), timeout=10)[2])["state"]
        except HAError as e:
            if "404" in str(e):
                return None
            raise

    def states(self, max_age=3):
        """All states keyed by entity id, cached briefly so a page of printers is one request."""
        with self._lock:
            at, cached = self._states
            if self.clock() - at < max_age:
                return cached
        data = {s["entity_id"]: s for s in json.loads(self._rest("GET", "/states", timeout=15)[2])}
        with self._lock:
            self._states = (self.clock(), data)
        return data

    def call_service(self, domain, service, data, response=False):
        _, _, raw = self._rest("POST", "/services/%s/%s%s" % (domain, service, "?return_response" if response else ""),
                               data, timeout=360)
        return json.loads(raw or b"null")

    def image(self, entity_id):
        _, ctype, raw = self._rest("GET", "/image_proxy/" + urllib.parse.quote(entity_id), timeout=15)
        return ctype, raw

    # -- websocket --
    def _connect(self, timeout=30):
        try:
            ws = WebSocket(self.ws_url, timeout)
        except OSError as e:
            raise HAError("Home Assistant websocket is not reachable: %s" % e)
        try:
            if ws.recv().get("type") != "auth_required":
                raise HAError("unexpected websocket greeting")
            ws.send({"type": "auth", "access_token": self.token})
            if ws.recv().get("type") != "auth_ok":
                raise HAError("websocket login refused")
        except Exception:
            ws.close()
            raise
        return ws

    def command(self, type_, **fields):
        """One websocket command on its own short connection; for rare calls (registries, users)."""
        ws = self._connect()
        try:
            ws.send(dict(fields, id=1, type=type_))
            while True:
                m = ws.recv()
                if m.get("id") == 1 and m.get("type") == "result":
                    if not m.get("success"):
                        raise HAError((m.get("error") or {}).get("message", "command failed"))
                    return m.get("result")
        except OSError as e:
            raise HAError("Home Assistant websocket failed: %s" % e)
        finally:
            ws.close()

    def watch_state_changes(self, callback, stop):
        """Blocking loop: calls callback(entity_id, new_state) per state_changed; reconnects until stop is set."""
        pause = 2
        while not stop.is_set():
            try:
                ws = self._connect(timeout=None)  # idle is normal here
                try:
                    ws.send({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
                    pause = 2
                    while not stop.is_set():
                        m = ws.recv()
                        if m.get("type") == "event":
                            d = m["event"]["data"]
                            new = d.get("new_state")
                            if new:
                                callback(d["entity_id"], new["state"])
                finally:
                    ws.close()
            except (HAError, OSError, ValueError, KeyError):
                stop.wait(pause)
                pause = min(pause * 2, 60)
