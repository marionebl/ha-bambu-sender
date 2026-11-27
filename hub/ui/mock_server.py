#!/usr/bin/env python3
"""Mock of the Druckwerk API for UI work. Serves hub/ui/ under a fake ingress prefix so that
absolute URLs (which break behind HA ingress) fail here too.

  python3 hub/ui/mock_server.py [port]      then open  http://127.0.0.1:8099/?as=admin|user|admin-new
All serials, names and entities are fake. State lives in memory and resets on restart."""
import hashlib
import json
import math
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = "/api/hassio_ingress/MOCKTOKEN/"
NOW = time.time

IDENT = {
    "admin": {"user_id": "u-alex", "name": "Alex Admin", "is_admin": True, "setup_done": True},
    "admin-new": {"user_id": "u-alex", "name": "Alex Admin", "is_admin": True, "setup_done": False},
    "user": {"user_id": "u-lena", "name": "Lena Tester", "is_admin": False, "setup_done": True},
}
USERS = {"u-alex": "Alex Admin", "u-lena": "Lena Tester", "u-jonas": "Jonas Example"}
# serial -> printer; "t0" is when the fake print started
SERIALS = {"H2S": "FAKE-H2S-0001", "P1S": "FAKE-P1S-0002", "X1C": "FAKE-X1C-0003"}
H2S, P1S, X1C = SERIALS["H2S"], SERIALS["P1S"], SERIALS["X1C"]
USER_PRINTERS = {H2S: "user", P1S: "user"}


def tray(slot, type_, colour, name, pct, spool=None):
    return {"slot": slot, "type": type_, "colour": colour, "name": name, "remain_pct": pct, "spool_id": spool}


PRINTERS = {
    H2S: {"serial": H2S, "name": "Workshop H2S", "model": "H2S", "online": True, "status": "running", "stage": "printing",
          "progress": 42, "remaining_min": 95, "layer": 128, "total_layers": 300, "nozzle_temp": 249, "nozzle_target": 250,
          "bed_temp": 59.6, "bed_target": 60, "chamber_temp": 38, "task_name": "Gridfinity bin 4x2",
          "active_tray": {"unit": 0, "slot": 1},
          "ams": [{"unit": 0, "humidity_index": 4, "temp": 27, "trays": [
              tray(0, "PLA", "F2F2F2", "Acme PLA White", 88, "s1"), tray(1, "PETG", "1F6FEB", "Prusament PETG Blue", 61, "s2"),
              tray(2, "PLA", "E4572E", "Sunlu PLA Red", 14, "s3"), tray(3, "", "", "", -1)]},
              {"unit": 1, "humidity_index": 2, "temp": 31, "trays": [tray(0, "TPU", "222222", "TPU 95A Black", 73, "s4")]}],
          "external": None, "hms": []},
    P1S: {"serial": P1S, "name": "Office P1S", "model": "P1S", "online": True, "status": "idle", "stage": "idle",
          "progress": 0, "remaining_min": 0, "layer": 0, "total_layers": 0, "nozzle_temp": 31, "nozzle_target": 0,
          "bed_temp": 29, "bed_target": 0, "chamber_temp": 27, "task_name": "", "active_tray": None,
          "ams": [{"unit": 0, "humidity_index": 5, "temp": 24, "trays": [
              tray(0, "PLA", "2E2E2E", "Generic PLA Black", 95), tray(1, "PLA", "F2C94C", "Generic PLA Yellow", 40),
              tray(2, "", "", "", -1), tray(3, "", "", "", -1)]}],
          "external": None, "hms": []},
    X1C: {"serial": X1C, "name": "Garage X1C", "model": "X1C", "online": False, "status": "offline", "stage": "offline",
          "progress": 0, "remaining_min": 0, "layer": 0, "total_layers": 0, "nozzle_temp": 22, "nozzle_target": 0,
          "bed_temp": 21, "bed_target": 0, "chamber_temp": 21, "task_name": "", "active_tray": None, "ams": [],
          "external": {"type": "PLA", "colour": "3FB68B", "name": "Spool on holder"},
          "hms": [{"code": "0300-0100-0001-0007", "severity": "serious", "text": "The nozzle temperature is abnormal; the heater may be loose.",
                   "url": "https://example.com/hms/0300_0100_0001_0007"}]},
}
T0 = {H2S: NOW() - 58 * 60}


def job(id_, file, target, owner, state, model="", **kw):
    j = {"id": id_, "file": file, "source": file.replace(".gcode.3mf", ""), "target": target, "model": model,
         "filaments": [{"type": "PLA", "colour": "F2F2F2"}], "options": {"timelapse": False, "bed_leveling": True, "use_ams": True},
         "state": state, "error": None, "created_at": NOW() - 3600 * kw.pop("age", 2), "owner_user_id": owner, "owner_name": USERS[owner],
         "plate_png": f"api/queue/{id_}/plate.png"}
    j.update(kw)
    return j


JOBS = [
    job("a00000000001", "gridfinity_bin_4x2.gcode.3mf", H2S, "u-alex", "printing", printer=H2S, started_at=NOW() - 3500, grams=38.2, cost=0.84),
    job("a00000000002", "cable_clip_set.gcode.3mf", H2S, "u-lena", "queued", grams=12.5, cost=0.31),
    job("a00000000003", "phone_stand_v3.gcode.3mf", H2S, "u-jonas", "queued", grams=55.0, cost=1.20),
    job("a00000000004", "bracket_shelf_left.gcode.3mf", "any", "u-jonas", "queued", model="P1S", grams=71.4, cost=1.62),
    job("a00000000005", "toy_dragon_articulated.gcode.3mf", P1S, "u-lena", "pending_approval", grams=96.0, cost=2.10),
    job("a00000000006", "drawer_divider_s.gcode.3mf", P1S, "u-alex", "queued", grams=8.9, cost=0.22),
]
HISTORY = [
    job("b00000000001", "benchy_test.gcode.3mf", H2S, "u-lena", "done", printer=H2S, grams=14.0, cost=0.35, age=30, finished_at=NOW() - 3600 * 28),
    job("b00000000002", "hook_wall_large.gcode.3mf", P1S, "u-jonas", "failed", printer=P1S, grams=3.1, cost=0.08, age=50,
        error="printer reported a failed print", finished_at=NOW() - 3600 * 47),
    job("b00000000003", "lens_cap.gcode.3mf", P1S, "u-alex", "cancelled", age=70, finished_at=NOW() - 3600 * 70),
]
SPOOLS = [
    {"id": "s1", "brand": "Acme", "type": "PLA", "colour": "F2F2F2", "name": "White", "grams_left": 880, "grams_total": 1000, "price_per_kg": 19.99, "printer": H2S, "unit": 0, "slot": 0},
    {"id": "s2", "brand": "Prusament", "type": "PETG", "colour": "1F6FEB", "name": "Blue", "grams_left": 610, "grams_total": 1000, "price_per_kg": 27.5, "printer": H2S, "unit": 0, "slot": 1},
    {"id": "s3", "brand": "Sunlu", "type": "PLA", "colour": "E4572E", "name": "Red", "grams_left": 140, "grams_total": 1000, "price_per_kg": 14.0, "printer": H2S, "unit": 0, "slot": 2},
    {"id": "s4", "brand": "Generic", "type": "TPU", "colour": "222222", "name": "Black 95A", "grams_left": 730, "grams_total": 1000, "price_per_kg": 32.0, "printer": H2S, "unit": 1, "slot": 0},
]
GRANTS = [
    {"id": "c00000000001", "user_id": "u-lena", "user_name": "Lena Tester", "printer": H2S, "role": "user",
     "until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW() + 86400 * 30)), "condition_entity": "schedule.workshop_hours", "needs_approval": False, "active": True},
    {"id": "c00000000002", "user_id": "u-jonas", "user_name": "Jonas Example", "printer": "*", "role": "user",
     "until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW() - 86400)), "condition_entity": None, "needs_approval": True, "active": False},
]
HA_USERS = [{"id": "u-alex", "name": "Alex Admin", "is_admin": True}, {"id": "u-lena", "name": "Lena Tester", "is_admin": False},
            {"id": "u-jonas", "name": "Jonas Example", "is_admin": False}]
ENTITIES = [
    {"entity_id": "schedule.workshop_hours", "name": "Workshop hours", "state": "on"},
    {"entity_id": "input_boolean.guests_home", "name": "Guests home", "state": "off"},
    {"entity_id": "binary_sensor.workshop_door", "name": "Workshop door", "state": "off"},
    {"entity_id": "person.lena", "name": "Lena", "state": "home"},
]
SETUP = {"accounts": [{"id": "acc1", "email": "owner@example.invalid", "logged_in": True, "daemon_running": True}],
         "printers": {s: {"serial": s, "device_name": PRINTERS[s]["name"], "model": PRINTERS[s]["model"], "account": "acc1",
                          "configured": s != X1C, "config": None, "suggested_prefix": {H2S: "h2s_fake0001", P1S: "p1s_fake0002"}.get(s)} for s in PRINTERS}}
SLICES = {}
LOCK = threading.Lock()
QUALITY = {"good": (7800, 41.0, "0.24mm Draft"), "better": (10500, 42.5, "0.20mm Standard"), "best": (14700, 44.1, "0.16mm High Quality")}
WAITING = ("queued", "pending_approval")


def hue(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:4], 16) % 360


def plate_svg(seed):
    c = hue(seed)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect width="120" height="120" fill="#e9ecef"/>'
            f'<path d="M20 78l40-22 40 22-40 22z" fill="#cfd4da"/><path d="M34 70l26-14 26 14v22l-26 14-26-14z" fill="hsl({c} 60% 55%)"/>'
            f'<path d="M34 70l26-14 26 14-26 14z" fill="hsl({c} 60% 68%)"/></svg>')


def camera_svg(p):
    t = int(NOW()) % 60
    if p["status"] == "running":
        h = min(80, 20 + p["progress"] * 0.8)
        obj = f'<rect x="250" y="{210 - h}" width="140" height="{h}" rx="6" fill="#e4572e"/><rect x="250" y="{210 - h}" width="140" height="6" fill="#f08a6a"/>'
        nozzle = f'<rect x="305" y="{150 - h}" width="30" height="40" fill="#555"/><rect x="316" y="{185 - h}" width="8" height="12" fill="#d8a"/>'
    else:
        obj, nozzle = "", '<rect x="305" y="60" width="30" height="40" fill="#555"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360"><rect width="640" height="360" fill="#23272d"/>'
            f'<rect x="40" y="20" width="560" height="10" fill="#3a4048"/><path d="M110 270l60-70h300l60 70z" fill="#3b4a40"/>'
            f'<rect x="110" y="270" width="420" height="14" fill="#2c3238"/>{obj}{nozzle}'
            f'<text x="16" y="344" font-size="14" fill="#8b949e" font-family="monospace">mock camera {t:02d}s</text></svg>')


def gcode():
    out = ["; mock gcode", "M83"]
    for n in range(60):
        z = 0.2 * (n + 1)
        out += [";LAYER_CHANGE", f";Z:{z:.2f}", f"; Z_HEIGHT: {z:.2f}", f"G1 Z{z:.2f}"]
        for feat, inset in (("Outer wall", 0), ("Inner wall", 0.8)):
            out.append(f";TYPE:{feat}")
            a, b = 98 + inset, 158 - inset
            out.append(f"G1 X{a} Y{a}")
            for x, y in ((b, a), (b, b), (a, b), (a, a)):
                out.append(f"G1 X{x} Y{y} E0.05")
        out.append(";TYPE:Sparse infill")
        for k in range(100, 156, 6):
            out += [f"G1 X{k} Y100", f"G1 X{k + (n % 3) * 2} Y156 E0.04"]
    return "\n".join(out)


def me(ident):
    base = dict(IDENT[ident])
    base["printers"] = [] if base["is_admin"] else [{"serial": s, "role": r} for s, r in USER_PRINTERS.items()]
    if base["is_admin"]:
        base["printers"] = [{"serial": s, "role": "admin"} for s in PRINTERS]
    return base


def visible(ident):
    return list(PRINTERS) if IDENT[ident]["is_admin"] else list(USER_PRINTERS)


def printer_out(ident, p):
    p = json.loads(json.dumps(p))
    s = p["serial"]
    p["role"] = "admin" if IDENT[ident]["is_admin"] else USER_PRINTERS.get(s)
    if p["status"] == "running":
        elapsed = (NOW() - T0[s]) / 60
        p["remaining_min"] = max(1, int(95 - (elapsed - 58)))
        p["end_time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW() + p["remaining_min"] * 60))
    else:
        p["end_time"] = None
    p["cover_url"] = f"api/printers/{s}/cover" if p["online"] else None
    p["camera_url"] = f"api/printers/{s}/camera" if p["online"] else None
    p["current_job"] = next((j for j in JOBS if j["state"] == "printing" and j.get("printer") == s), None)
    p["queue_length"] = sum(1 for j in JOBS if j["state"] in WAITING and (j["target"] == s or (j["target"] == "any" and j["model"] == p["model"])))
    return p


def sees(ident, j):
    if IDENT[ident]["is_admin"]:
        return True
    vis = visible(ident)
    return j["target"] in vis or (j["target"] == "any" and any(PRINTERS[s]["model"] == j["model"] for s in vis))


class Err(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg


def is_admin_for(ident, j):
    return IDENT[ident]["is_admin"]


def find(id_):
    for j in JOBS:
        if j["id"] == id_:
            return j
    raise Err(404, "no such job")


def job_action(ident, jid, action, body):
    uid = IDENT[ident]["user_id"]
    j = find(jid) if action != "retry" else next((x for x in HISTORY if x["id"] == jid), None)
    if j is None:
        raise Err(404, "no such finished job")
    own, admin = j["owner_user_id"] == uid, IDENT[ident]["is_admin"]
    if action == "cancel":
        if not (own or admin):
            raise Err(403, "rule: users may only cancel their own jobs; admins cancel any job")
        j["state"] = "cancelled"; JOBS.remove(j); HISTORY.insert(0, j); j["finished_at"] = NOW()
    elif action == "reorder":
        if not (own or admin):
            raise Err(403, "rule: users may only reorder their own jobs; admins reorder any job")
        pos = body.get("position")
        if not isinstance(pos, int):
            raise Err(400, "position must be an integer")
        slots = [i for i, x in enumerate(JOBS) if x["state"] in WAITING and (admin or x["owner_user_id"] == uid)
                 and (not body.get("scope") or body["scope"] in (x["target"], x.get("printer")))]
        pool = [JOBS[i] for i in slots]
        if j not in pool:
            raise Err(409, "job is not in this reorder scope")
        pool.remove(j); pool.insert(max(0, min(pos, len(pool))), j)
        for i, x in zip(slots, pool):
            JOBS[i] = x
    elif action in ("approve", "reject", "skip", "move"):
        if not admin:
            raise Err(403, "rule: only admins of the printer may %s jobs" % action)
        if action == "approve":
            j["state"] = "queued"
        elif action == "reject":
            j["state"] = "cancelled"; j["error"] = "rejected by an admin"; JOBS.remove(j); HISTORY.insert(0, j); j["finished_at"] = NOW()
        elif action == "skip":
            JOBS.remove(j); JOBS.append(j)
        else:
            j["target"], j["model"] = body.get("target", "any"), body.get("model", "")
    elif action == "retry":
        if not (own or admin):
            raise Err(403, "rule: users may only retry their own jobs")
        HISTORY.remove(j); j.update(state="queued", error=None); JOBS.append(j)
    return j


def make_job_state(sl):
    """A fake slice finishes 2 s after /slice."""
    if sl.get("sliced_at") is None:
        return {"state": "uploaded"}
    el = NOW() - sl["sliced_at"]
    if el < 2.0:
        return {"state": "slicing", "elapsed": int(el)}
    sec, g, proc = QUALITY[sl["quality"]]
    return {"state": "done", "quality": sl["quality"], "info": {"seconds": sec, "grams": g, "warnings": [], "has_png": True, "filaments": []},
            "settings": {"process": proc + " @BBL", "tray": sl["tray"], "bed": sl["bed"]}}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        if os.environ.get("MOCK_LOG"):
            sys.stderr.write("%s\n" % (fmt % a))

    def send(self, code, body, ctype="application/json", extra=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return raw

    def js(self, raw):
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise Err(400, "bad JSON")

    def handle_any(self, method):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        ident = (q.get("as") or [cookies["as"].value if "as" in cookies else "admin"])[0]
        if ident not in IDENT:
            ident = "admin"
        extra = [("Set-Cookie", f"as={ident}; Path=/")] if "as" in q else []
        if u.path == "/" or u.path == PREFIX.rstrip("/"):
            return self.send(302, b"", extra=[("Location", PREFIX + ("?as=" + ident if "as" in q else ""))] + extra)
        if not u.path.startswith(PREFIX):
            return self.send(404, {"error": "outside the ingress prefix: " + u.path})
        path = u.path[len(PREFIX):]
        raw = self.body()
        try:
            with LOCK:
                if path.startswith("api/"):
                    code, out, ct = self.api(method, path, q, raw, ident)
                    return self.send(code, out, ct, extra)
            return self.static(path or "index.html", extra)
        except Err as e:
            return self.send(e.code, {"error": e.msg}, extra=extra)

    def static(self, rel, extra):
        f = os.path.normpath(os.path.join(HERE, rel))
        if not f.startswith(HERE + os.sep) or f.endswith(".py") or not os.path.isfile(f):
            return self.send(404, {"error": "not found"})
        ct = mimetypes.guess_type(f)[0] or "application/octet-stream"
        with open(f, "rb") as fh:
            self.send(200, fh.read(), ct, extra)

    def api(self, m, path, q, raw, ident):
        J, admin = (lambda: self.js(raw)), IDENT[ident]["is_admin"]
        uid = IDENT[ident]["user_id"]
        ok = lambda o: (200, o, "application/json")
        svg = lambda s: (200, s.encode(), "image/svg+xml")
        need_admin = lambda: None if admin else (_ for _ in ()).throw(Err(403, "rule: only Home Assistant admins can do this"))
        if path == "api/me":
            return ok(me(ident))
        if path == "api/printers" and m == "GET":
            return ok([printer_out(ident, PRINTERS[s]) for s in visible(ident)])
        r = re.fullmatch(r"api/printers/([^/]+)/(camera|cover)", path)
        if r:
            if r.group(1) not in visible(ident):
                raise Err(404, "no such printer")
            return svg(camera_svg(PRINTERS[r.group(1)]))
        r = re.fullmatch(r"api/printers/([^/]+)/(start-next|start)", path)
        if r and m == "POST":
            s, b = r.group(1), J()
            if not admin:
                raise Err(403, "rule: only admins of the printer may start prints")
            if b.get("plate_clear") is not True:
                raise Err(409, "confirm that the plate is clear")
            nxt = next((j for j in JOBS if j["id"] == b.get("job_id")), None) if r.group(2) == "start" else \
                next((j for j in JOBS if j["state"] == "queued" and (j["target"] == s or j["target"] == "any")), None)
            if not nxt:
                raise Err(409, "no job to start")
            nxt.update(state="printing", printer=s, started_at=NOW())
            return ok(nxt)
        if path == "api/queue" and m == "GET":
            vis = [j for j in JOBS if sees(ident, j)]
            return ok({"jobs": vis, "history": [j for j in HISTORY if sees(ident, j)], "printers": [{"serial": s} for s in visible(ident)]})
        r = re.fullmatch(r"api/queue/([0-9a-f]{12})/plate\.png", path)
        if r:
            return svg(plate_svg(r.group(1)))
        r = re.fullmatch(r"api/queue/([0-9a-f]{12})/(\w+)", path)
        if r and m == "POST":
            return ok(job_action(ident, r.group(1), r.group(2), J()))
        # slicing
        if path == "api/jobs" and m == "POST":
            name = (q.get("name") or [""])[0]
            if not re.search(r"\.(stl|3mf|step|stp|obj)$", name, re.I):
                raise Err(400, "Upload an .stl, .3mf, .obj or .step file.")
            jid = secrets.token_hex(6)
            SLICES[jid] = {"id": jid, "filename": name, "state": "uploaded"}
            return ok({"id": jid, "filename": name, "state": "uploaded"})
        r = re.fullmatch(r"api/jobs/([0-9a-f]{12})(?:/(\w+)(?:\.(\w+))?)?", path)
        if r and r.group(1) in SLICES:
            sl, what = SLICES[r.group(1)], r.group(2)
            if what is None and m == "GET":
                return ok({**sl, **make_job_state(sl)})
            if what == "slice" and m == "POST":
                b = J()
                if b.get("quality") not in QUALITY:
                    raise Err(400, "Unknown quality or plate.")
                sl.update(sliced_at=NOW(), quality=b["quality"], tray=b.get("tray"), bed=b.get("bed"), serial=b.get("serial"))
                return (202, {**sl, "state": "queued"}, "application/json")
            if what == "plate" and r.group(3) == "png":
                return svg(plate_svg(sl["id"] + sl.get("quality", "")))
            if what == "plate" and r.group(3) == "gcode":
                return (200, gcode().encode(), "text/plain")
            if what == "queue" and m == "POST":
                b = J()
                if make_job_state(sl).get("state") != "done":
                    raise Err(409, "slice first")
                if not (admin or visible(ident)):
                    raise Err(403, "rule: adding a job needs an active grant on the target printer")
                if b.get("target") not in ("any", *PRINTERS) or (b["target"] == "any" and not b.get("model")):
                    raise Err(400, "model is required for target any")
                st = make_job_state(sl)
                jb = job(secrets.token_hex(6), re.sub(r"\.\w+$", "", sl["filename"]) + ".gcode.3mf", b["target"], uid, "queued",
                         model=b.get("model", ""), grams=st["info"]["grams"], age=0)
                jb["options"].update(b.get("options") or {})
                if uid == "u-jonas":
                    jb["state"] = "pending_approval"
                JOBS.append(jb)
                return ok(jb)
        if path == "api/spools":
            if m == "GET":
                return ok(SPOOLS)
            b = J(); b["id"] = "s" + secrets.token_hex(3); SPOOLS.append(b)
            return ok(b)
        r = re.fullmatch(r"api/spools/(\w+)", path)
        if r:
            sp = next((s for s in SPOOLS if s["id"] == r.group(1)), None)
            if not sp:
                raise Err(404, "no such spool")
            if not admin:
                raise Err(403, "rule: only admins of the spool's printer may change it")
            if m == "DELETE":
                SPOOLS.remove(sp)
            else:
                sp.update(J())
            return ok(sp)
        # admin-only
        if path.startswith("api/grants") or path.startswith("api/ha/") or path.startswith("api/setup") or path == "api/integration":
            need_admin()
        if path == "api/grants" and m == "GET":
            return ok(GRANTS)
        if path == "api/grants" and m == "POST":
            b = J()
            g = {"id": secrets.token_hex(6), "user_id": b["user_id"], "user_name": USERS.get(b["user_id"], b["user_id"]), "printer": b["printer"],
                 "role": b.get("role", "user"), "until": b.get("until"), "condition_entity": b.get("condition_entity"),
                 "needs_approval": bool(b.get("needs_approval")), "active": True}
            GRANTS.append(g)
            return ok(g)
        r = re.fullmatch(r"api/grants/(\w+)", path)
        if r and m == "DELETE":
            GRANTS[:] = [g for g in GRANTS if g["id"] != r.group(1)]
            return ok({})
        if path == "api/ha/users":
            return ok(HA_USERS)
        if path == "api/ha/entities":
            doms = (q.get("domains") or [""])[0].split(",")
            return ok([e for e in ENTITIES if e["entity_id"].split(".")[0] in doms])
        if path == "api/integration":
            return ok({"url": "http://local-druckwerk:8099", "secret": "mock-secret-" + "0" * 20})
        if path == "api/setup" and m == "GET":
            return ok({"accounts": SETUP["accounts"], "printers": list(SETUP["printers"].values())})
        if path == "api/setup/login/start":
            b = J(); em = b.get("email", "")
            if not em or not b.get("password"):
                raise Err(400, "email and password are required")
            if "code" in em:
                return ok({"state": "need_code", "account": em})
            if "tfa" in em:
                return ok({"state": "need_tfa", "account": em})
            SETUP["accounts"].append({"id": secrets.token_hex(3), "email": em, "logged_in": True, "daemon_running": True})
            return ok({"state": "done", "account": em})
        if path == "api/setup/login/code":
            b = J()
            SETUP["accounts"].append({"id": secrets.token_hex(3), "email": b.get("email"), "logged_in": True, "daemon_running": True})
            return ok({"state": "done", "account": b.get("email")})
        r = re.fullmatch(r"api/setup/accounts/(\w+)", path)
        if r and m == "DELETE":
            SETUP["accounts"][:] = [a for a in SETUP["accounts"] if a["id"] != r.group(1)]
            return ok({})
        r = re.fullmatch(r"api/setup/printers/([^/]+)", path)
        if r and r.group(1) in SETUP["printers"]:
            p = SETUP["printers"][r.group(1)]
            if m == "PUT":
                p["configured"] = True; p["config"] = J()
            else:
                p["configured"] = False
            return ok(p)
        raise Err(404, "no route " + m + " " + path)

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PUT(self): self.handle_any("PUT")
    def do_DELETE(self): self.handle_any("DELETE")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"mock hub on http://127.0.0.1:{port}/?as=admin  (also user, admin-new)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
