#!/usr/bin/env python3
"""Fake bridge, Supervisor and Home Assistant for the image end-to-end test (hub/tests/ui_e2e.sh).

  python3 e2e_mock.py HTTP_PORT WS_PORT
HTTP_PORT: bridge API (/api/...), HA core REST (/core/...), Supervisor (/supervisor/...). WS_PORT: HA websocket.
All serials, names and entities are fake; the printers match the ones in hub/ui/mock_server.py."""
import base64
import hashlib
import json
import socket
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from externals import HA_VENDOR_DOMAIN as VENDOR  # noqa: E402

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
H2S, P1S, X1C = "FAKE-H2S-0001", "FAKE-P1S-0002", "FAKE-X1C-0003"
PREFIX = {H2S: "h2s_fake0001", P1S: "p1s_fake0002", X1C: "x1c_fake0003"}
NAMES = {H2S: ("Workshop H2S", "H2S"), P1S: ("Office P1S", "P1S"), X1C: ("Garage X1C", "X1C")}
# A 1x1 JPEG, so camera and cover <img> elements really decode.
JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
                        "wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")
# (type, colour, name, remain %, bridge tray_info_idx); None = empty slot
UNITS = [[("PLA", "F2F2F2", "Acme PLA White", 88, "GFA00"), ("PETG", "1F6FEB", "Prusament PETG Blue", 61, "GFG02"),
          ("PLA", "E4572E", "Sunlu PLA Red", 14, "GFA00"), None],
         [("TPU", "222222", "TPU 95A Black", 73, "GFU01")]]
P1S_TRAYS = [("PLA", "2E2E2E", "Generic PLA Black", 95, "GFA00"), ("PLA", "F2C94C", "Generic PLA Yellow", 40, "GFA00"), None, None]


def st(state, **attrs):
    return {"state": state, "attributes": attrs}


def printer_states(serial, status, **kw):
    p = PREFIX[serial]
    s = {"sensor.%s_print_status" % p: st(status), "sensor.%s_current_stage" % p: st(kw.get("stage", "idle")),
         "sensor.%s_print_progress" % p: st(str(kw.get("progress", 0))), "sensor.%s_remaining_time" % p: st(str(kw.get("remaining", 0))),
         "sensor.%s_end_time" % p: st(kw.get("end", "unknown")), "sensor.%s_current_layer" % p: st(str(kw.get("layer", 0))),
         "sensor.%s_total_layer_count" % p: st(str(kw.get("total", 0))), "sensor.%s_nozzle_temperature" % p: st(str(kw.get("nozzle", 25))),
         "sensor.%s_nozzle_target_temperature" % p: st(str(kw.get("nozzle_t", 0))), "sensor.%s_bed_temperature" % p: st(str(kw.get("bed", 25))),
         "sensor.%s_bed_target_temperature" % p: st(str(kw.get("bed_t", 0))), "sensor.%s_chamber_temperature" % p: st("27"),
         "sensor.%s_task_name" % p: st(kw.get("task", "")), "binary_sensor.%s_online" % p: st("off" if kw.get("offline") else "on"),
         "sensor.%s_active_tray" % p: st("none"), "image.%s_cover_image" % p: st("2026-11-27T10:00:00"),
         "binary_sensor.%s_hms_errors" % p: st("off", Count=0)}
    return s


def tray_state(t, n):
    if not t:
        return st("Empty", slot=n, type="", color="#00000000", remain=-1, empty=True)
    return st(t[2], slot=n, type=t[0], color="#%sFF" % t[1], remain=t[3], empty=False)


def build_states():
    from time import strftime, gmtime, time
    end = strftime("%Y-%m-%dT%H:%M:%S+00:00", gmtime(time() + 95 * 60))
    s = printer_states(H2S, "running", stage="printing", progress=42, remaining=95, end=end, layer=128, total=300, nozzle=249,
                       nozzle_t=250, bed=59.6, bed_t=60, task="Gridfinity bin 4x2")
    s["sensor.h2s_fake0001_active_tray"] = st("Prusament PETG Blue", ams_index=0, tray_index=1)
    s.update(printer_states(P1S, "idle"))
    s.update(printer_states(X1C, "unavailable", offline=True))
    s["binary_sensor.x1c_fake0003_hms_errors"] = st("on", Count=1, **{"1-Code": "HMS_0700_0100_0001_0007", "1-Error": "The AMS filament has run out.",
                                                                    "1-Wiki": "https://wiki.example.invalid/hms", "1-Severity": "serious"})
    for u, trays in enumerate(UNITS):
        for n, t in enumerate(trays, 1):
            s["sensor.h2s_ams%d_tray_%d" % (u + 1, n)] = tray_state(t, n)
        s["sensor.h2s_ams%d_humidity_index" % (u + 1)] = st("4" if u == 0 else "2")
        s["sensor.h2s_ams%d_temperature" % (u + 1)] = st("27" if u == 0 else "31")
    for n, t in enumerate(P1S_TRAYS, 1):
        s["sensor.p1s_ams1_tray_%d" % n] = tray_state(t, n)
    s.update({"schedule.workshop_hours": st("on", friendly_name="Workshop hours"), "input_boolean.guests_home": st("off", friendly_name="Guests home"),
              "person.lena": st("home", friendly_name="Lena")})
    return s


def build_registries():
    devices, entities = [], []
    for serial, p in PREFIX.items():
        devices.append({"id": "dev-" + p, "identifiers": [[VENDOR, serial]], "name": "%s_%s" % (NAMES[serial][1].lower(), serial.lower()),
                        "name_by_user": NAMES[serial][0], "model": NAMES[serial][1]})
        entities.append({"entity_id": "sensor.%s_print_status" % p, "device_id": "dev-" + p, "platform": VENDOR, "unique_id": serial + "_print_status"})
    for serial, prefix, units in ((H2S, "h2s", UNITS), (P1S, "p1s", [P1S_TRAYS])):
        for u, trays in enumerate(units):
            dev = "dev-%s-ams%d" % (prefix, u + 1)
            devices.append({"id": dev, "identifiers": [[VENDOR, "%sAMS%d" % (prefix.upper(), u + 1)]], "model": "AMS",
                            "name": "%s_%s_AMS_%d" % (prefix, serial.lower(), u + 1)})
            for key in ["tray_%d" % n for n in range(1, len(trays) + 1)] + (["humidity_index", "temperature"] if prefix == "h2s" else []):
                entities.append({"entity_id": "sensor.%s_ams%d_%s" % (prefix, u + 1, key), "device_id": dev, "platform": VENDOR,
                                 "unique_id": "%s_%s_AMS_%sAMS%d_%s" % (prefix.upper(), serial, prefix.upper(), u + 1, key)})
    return devices, entities


USERS = [{"id": "u-admin", "name": "Alex Admin", "is_owner": False, "is_active": True, "system_generated": False, "group_ids": ["system-admin"]},
         {"id": "u-user", "name": "Lena Tester", "is_owner": False, "is_active": True, "system_generated": False, "group_ids": ["system-users"]},
         {"id": "u-friend", "name": "Jonas Example", "is_owner": False, "is_active": True, "system_generated": False, "group_ids": ["system-users"]}]
STATES = build_states()
DEVICES, ENTITIES = build_registries()
CALLS = []


def bridge_ams(trays):
    return {"ams": {"ams": [{"id": "0", "tray": [{"id": str(i), "tray_type": t[0], "tray_color": t[1] + "FF", "tray_info_idx": t[4],
                                                  "tray_sub_brands": t[2]} if t else {"id": str(i)} for i, t in enumerate(trays)]}]}, "vt_tray": {}}


class H(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/health":
            return self.reply(200, {"server": "ok", "accounts": [{"id": "acc1", "email": "owner@example.invalid", "logged_in": True,
                                                                  "daemon": {"running": True}}]})
        if p == "/api/printers":
            return self.reply(200, [{"dev_id": s, "serial": s, "name": n, "dev_product_name": m, "account": "acc1", "live": {"ok": 1}}
                                    for s, (n, m) in NAMES.items()])
        if p.startswith("/api/ams/"):
            serial = p.rsplit("/", 1)[1]
            return self.reply(200, bridge_ams(UNITS[0] if serial == H2S else P1S_TRAYS))
        if p.startswith("/api/status/"):
            return self.reply(200, {"print": {}})
        if p.startswith("/api/camera/"):
            return self.reply(200, JPEG, "image/jpeg")
        if p == "/calls":
            return self.reply(200, CALLS)
        if p == "/core/states":
            return self.reply(200, [dict(v, entity_id=k) for k, v in STATES.items()])
        if p.startswith("/core/states/"):
            eid = p.rsplit("/", 1)[1]
            return self.reply(200, dict(STATES[eid], entity_id=eid)) if eid in STATES else self.reply(404, {"message": "Entity not found"})
        if p.startswith("/core/image_proxy/"):
            return self.reply(200, JPEG, "image/jpeg")
        self.reply(404, {"error": "no such route " + p})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        CALLS.append({"path": self.path, "body": body})
        if self.path == "/api/login/start":
            return self.reply(200, {"state": "need_code", "account": "acc1"})
        if self.path == "/api/login/code":
            return self.reply(200, {"state": "done", "account": "acc1"})
        if self.path.startswith("/core/services/"):
            return self.reply(200, {"service_response": {"result": "sent"}})
        self.reply(200, {})  # supervisor discovery

    def do_DELETE(self):
        self.reply(200, {"deleted": self.path.rsplit("/", 1)[1]})

    def log_message(self, *a):
        pass


def recv_exact(c, n):
    out = b""
    while len(out) < n:
        chunk = c.recv(n - len(out))
        if not chunk:
            raise OSError("closed")
        out += chunk
    return out


def read_frame(c):
    b0, b1 = recv_exact(c, 2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", recv_exact(c, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", recv_exact(c, 8))[0]
    mask = recv_exact(c, 4)
    data = bytes(b ^ mask[i % 4] for i, b in enumerate(recv_exact(c, n)))
    return None if b0 & 0x0F == 8 else json.loads(data) if data else {}


def write_frame(c, obj):
    data = json.dumps(obj).encode()
    n = len(data)
    head = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n))
    c.sendall(head + data)


def ws_client(c):
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            req += c.recv(1024)
        key = [l for l in req.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0].split(": ")[1]
        acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
        write_frame(c, {"type": "auth_required"})
        read_frame(c)
        write_frame(c, {"type": "auth_ok"})
        while (m := read_frame(c)) is not None:
            result = {"config/auth/list": USERS, "config/device_registry/list": DEVICES, "config/entity_registry/list": ENTITIES}.get(m["type"])
            write_frame(c, {"id": m["id"], "type": "result", "success": True, "result": result})
    except OSError:
        pass
    finally:
        c.close()


def serve_ws(port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(8)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=ws_client, args=(c,), daemon=True).start()


if __name__ == "__main__":
    threading.Thread(target=serve_ws, args=(int(sys.argv[2]),), daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
