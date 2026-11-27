"""Hub app tests: the real server on a loopback port with a fake Home Assistant and a fake bridge, no network."""
import http.client
import importlib.util
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
# HUB_IMPL lets the suite run against a stub dir to show it fails without the implementation.
for p in (os.environ.get("HUB_IMPL") or os.path.join(REPO, "hub"), os.path.join(REPO, "hub", "queue")):
    sys.path.insert(0, p)

from externals import HA_VENDOR_DOMAIN as VENDOR  # noqa: E402

SERIAL, SERIAL2 = "FAKE0001", "FAKE0002"
ADMIN, USER, OTHER = "u-admin", "u-user", "u-other"
USERS = [
    {"id": ADMIN, "name": "Ada", "is_owner": False, "is_active": True, "system_generated": False, "group_ids": ["system-admin"]},
    {"id": USER, "name": "Ben", "is_owner": False, "is_active": True, "system_generated": False, "group_ids": ["system-users"]},
    {"id": OTHER, "name": "Cy", "is_owner": False, "is_active": True, "system_generated": False, "group_ids": ["system-users"]},
    {"id": "sys", "name": "Supervisor", "is_owner": False, "is_active": True, "system_generated": True, "group_ids": []},
]
DEVICES = [{"id": "dev1", "identifiers": [[VENDOR, SERIAL]], "name": "h2s_" + SERIAL.lower(), "name_by_user": "Lab H2S", "model": "H2S"},
           {"id": "ams1", "identifiers": [[VENDOR, "AMSFAKE1"]], "name": "h2s_%s_AMS_1" % SERIAL.lower(), "model": "AMS 2 Pro"}]
ENTITIES = [
    {"entity_id": "sensor.lab_h2s_print_status", "device_id": "dev1", "platform": VENDOR, "unique_id": SERIAL + "_print_status"},
    {"entity_id": "sensor.ams_tray_1", "device_id": "ams1", "platform": VENDOR, "unique_id": "H2S_%s_AMS_AMSFAKE1_tray_1" % SERIAL},
    {"entity_id": "sensor.ams_tray_2", "device_id": "ams1", "platform": VENDOR, "unique_id": "H2S_%s_AMS_AMSFAKE1_tray_2" % SERIAL},
    {"entity_id": "sensor.ams_humidity", "device_id": "ams1", "platform": VENDOR, "unique_id": "H2S_%s_AMS_AMSFAKE1_humidity_index" % SERIAL},
]
SLICE_INFO = open(os.path.join(HERE, "fixtures", "slice_info.config")).read()


def make_3mf(path, slice_info=SLICE_INFO):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/slice_info.config", slice_info)
        z.writestr("Metadata/plate_1.gcode", "; CHANGE_LAYER\nG1 X1 Y1 E1\n")
        z.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\nfake")
    return str(path)


def st(state, **attrs):
    return {"state": state, "attributes": attrs}


class FakeHA:
    def __init__(self):
        self.users, self.devices, self.entities = USERS, DEVICES, ENTITIES
        self.calls, self.fail = [], {}
        self.s = {
            "sensor.lab_h2s_print_status": st("running"), "sensor.lab_h2s_current_stage": st("printing"),
            "sensor.lab_h2s_print_progress": st("42"), "sensor.lab_h2s_remaining_time": st("73"),
            "sensor.lab_h2s_end_time": st("2026-11-27T20:00:00+00:00"), "sensor.lab_h2s_current_layer": st("10"),
            "sensor.lab_h2s_total_layer_count": st("50"), "sensor.lab_h2s_nozzle_temperature": st("220.5"),
            "sensor.lab_h2s_nozzle_target_temperature": st("220"), "sensor.lab_h2s_bed_temperature": st("60"),
            "sensor.lab_h2s_bed_target_temperature": st("60"), "sensor.lab_h2s_chamber_temperature": st("30"),
            "sensor.lab_h2s_task_name": st("cube"), "binary_sensor.lab_h2s_online": st("on"),
            "sensor.lab_h2s_active_tray": st("PLA Basic", ams_index=0, tray_index=1),
            "image.lab_h2s_cover_image": st("2026-11-27T19:00:00"),
            "binary_sensor.lab_h2s_hms_errors": st("on", Count=1, **{"1-Code": "HMS_0300_0100_0001_0007", "1-Error": "Bed sensor",
                                                                   "1-Wiki": "https://wiki.example/x", "1-Severity": "serious"}),
            "sensor.ams_tray_1": st("PETG HF", slot=1, type="PETG", color="#ADB1B2FF", remain=80, empty=False),
            "sensor.ams_tray_2": st("PLA Basic", slot=2, type="PLA", color="#BB3D43FF", remain=55, empty=False),
            "sensor.ams_humidity": st("4"),
            "schedule.workday": st("on", friendly_name="Workday"), "light.lamp": st("on"),
            "sensor.lab_p1s_print_status": st("idle"),
        }

    def state(self, eid):
        return (self.s.get(eid) or {}).get("state")

    def states(self, max_age=3):
        return dict({k: dict(v, entity_id=k) for k, v in self.s.items()})

    def command(self, type_, **kw):
        return {"config/auth/list": self.users, "config/device_registry/list": self.devices,
                "config/entity_registry/list": self.entities}[type_]

    def call_service(self, domain, service, data, response=False):
        self.calls.append((domain, service, data))
        if (domain, service) in self.fail:
            raise RuntimeError(self.fail[(domain, service)])
        if domain == "homeassistant":
            self.s[data["entity_id"]] = st("on" if service == "turn_on" else "off")
        return {}

    def image(self, eid):
        return "image/jpeg", b"JPEGDATA"


class FakeBridge(BaseHTTPRequestHandler):
    log = []

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/health":
            return self.reply(200, {"server": "ok", "accounts": [{"id": "a_x", "email": "a@example.invalid", "logged_in": True,
                                                                  "daemon": {"running": True}}]})
        if self.path == "/api/printers":
            return self.reply(200, [{"serial": SERIAL, "name": "Lab", "dev_product_name": "H2S", "account": "a_x"},
                                    {"serial": SERIAL2, "name": "Other", "dev_product_name": "P1S", "account": "a_x"}])
        if self.path.startswith("/api/status/"):
            return self.reply(200, {"print": {"gcode_state": "RUNNING", "mc_percent": 7, "mc_remaining_time": 5, "layer_num": 2,
                                              "total_layer_num": 9, "nozzle_temper": 200, "subtask_name": "from bridge"}})
        if self.path.startswith("/api/camera/"):
            return self.reply(200, b"FRAME:" + self.path.encode(), "image/jpeg")
        self.reply(404, {"error": "no"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FakeBridge.log.append((self.path, body))
        if self.path == "/api/login/start":
            return self.reply(200, {"state": "need_code", "account": "a_x"})
        if self.path == "/api/login/code":
            return self.reply(200, {"state": "done", "account": "a_x"})
        self.reply(404, {"error": "no"})

    def do_DELETE(self):
        self.reply(200, {"deleted": self.path.rsplit("/", 1)[1]})

    def log_message(self, *a):
        pass


class Hub:
    def __init__(self, tmp_path):
        spec = importlib.util.spec_from_file_location("slicer_server", os.path.join(REPO, "hub", "slicer", "server.py"))
        self.s = importlib.util.module_from_spec(spec)
        sys.modules["slicer_server"] = self.s
        spec.loader.exec_module(self.s)
        self.s.JOBS, self.s.SHARE = str(tmp_path / "jobs"), str(tmp_path / "share")
        self.tmp = tmp_path
        self.now = 1000.0
        self.ha = FakeHA()
        FakeBridge.log = []
        self.bridge = ThreadingHTTPServer(("127.0.0.1", 0), FakeBridge)
        threading.Thread(target=self.bridge.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        from app import App
        self.app = App(self.ha, str(tmp_path / "data"), self.s.SHARE, ("127.0.0.1", self.bridge.server_address[1]), self.s,
                       lambda: "hub-host", 8099, clock=lambda: self.now, trusted_ips=["127.0.0.1"],
                       ui_dir=os.path.join(REPO, "hub", "ui"))
        self.app.install()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.s.Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.port = self.server.server_address[1]

    def req(self, method, path, body=None, user=None, secret=False, headers=None, raw=None):
        h = dict(headers or {})
        if user:
            h.update({"X-Remote-User-Id": user, "X-Remote-User-Name": user, "X-Remote-User-Display-Name": user.upper()})
        if secret:
            h["X-Hub-Secret"] = self.app.secret
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request(method, path, data, h)
        r = c.getresponse()
        out = r.read()
        c.close()
        try:
            return r.status, json.loads(out)
        except ValueError:
            return r.status, out

    def setup_printers(self):
        self.app.registry.put(SERIAL, name="Lab", model="H2S", account="a_x", prefix="lab_h2s", electricity_kwh_price=0.4)
        self.app.registry.put(SERIAL2, name="Other", model="P1S", account="a_x", prefix="lab_p1s")

    def grant(self, user, printer=SERIAL, **kw):
        return self.app.grants.add(user, printer, **kw)

    def sliced(self, owner=USER):
        """A finished slice job as the slicer app keeps it, with the real slice_info.config."""
        jid = "a1b2c3d4e5f6"
        d = self.tmp / "jobs" / jid
        d.mkdir(parents=True, exist_ok=True)
        result = make_3mf(d / "result.gcode.3mf")
        self.s.jobs[jid] = {"id": jid, "filename": "cube.stl", "model": str(d / "cube.stl"), "state": "done", "created": 1,
                            "printer": {"serial": SERIAL, "name": "Lab", "model": "H2S"}, "quality": "better", "result": result,
                            "owner_user_id": owner, "settings": {"tray": {"ams": "0", "id": "0"}}}
        return jid


