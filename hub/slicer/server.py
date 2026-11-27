#!/usr/bin/env python3
"""Slicer server: upload a model, slice it on this machine, preview it, send it to a printer.

Printers and AMS trays come from the bridge. A print goes through Home Assistant's
druckwerk.print_file service, so its busy check and G-code guard apply.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.dirname(HERE)):  # python -I drops the script dir; the repo keeps externals.py one level up
    sys.path.insert(0, _d)
import externals  # noqa: E402

env = os.environ.get
JOBS = env("SLICER_JOBS", "/data/jobs")
SHARE = env("SLICER_SHARE", "/share/druckwerk")
SENDER = env("SLICER_SENDER_URL", "http://127.0.0.1:8098")
CORE = env("SLICER_CORE_URL", "http://supervisor/core/api")
SLICE = env("SLICER_SLICE_CMD", os.path.join(HERE, "slice.sh")).split()
PROFILES = env("SLICER_PROFILES", "/opt/bs/resources/profiles")
BS = env("SLICER_BS", os.path.join(HERE, "bs.py"))
MAX_MODEL = 100 * 1024 * 1024
KEEP_JOBS = 10
JOB_ID = r"([0-9a-f]{12})"

# Preset names per printer model (dev_product_name) live in externals.py. Quality = process profile.
PRINTERS = externals.PRINTERS
BEDS = ["Cool Plate", "Engineering Plate", "High Temp Plate", "Textured PEI Plate", "Supertack Plate"]

jobs = {}
jobs_lock = threading.Lock()
slice_lock = threading.Lock()  # one slicer at a time; the Pi has no room for two


def log(msg):
    print("slicer: " + msg, file=sys.stderr, flush=True)


def http_json(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": raw.decode(errors="replace")[:500]}


def trays(ams):
    out = []
    for unit in ((ams or {}).get("ams") or {}).get("ams") or []:
        for t in unit.get("tray") or []:
            if not t.get("tray_type"):
                continue  # empty slot
            out.append({"ams": str(unit.get("id", "0")), "id": str(t.get("id")), "type": t.get("tray_type"),
                        "idx": t.get("tray_info_idx") or "", "name": t.get("tray_sub_brands") or t.get("tray_type"),
                        "colour": "#" + (t.get("tray_color") or "C8C8C8FF")[:6]})
    return out


def printers():
    status, data = http_json("GET", SENDER + "/api/printers", timeout=10)
    if status != 200 or not isinstance(data, list):
        raise RuntimeError("Bridge answered %s: %s" % (status, data))
    out = []
    for p in data:
        model = p.get("dev_product_name") or p.get("dev_model_name") or ""
        cfg = PRINTERS.get(model)
        if not cfg:
            continue
        s, ams = http_json("GET", SENDER + "/api/ams/" + p["serial"], timeout=10)
        out.append({"serial": p["serial"], "name": p.get("name") or p["serial"], "model": model,
                    "bed": cfg["bed"], "qualities": list(cfg["quality"]),
                    "trays": trays(ams) if s == 200 else [], "online": bool(p.get("live"))})
    return out


def job_dir(jid):
    return os.path.join(JOBS, jid)


def save_job(job):
    with open(os.path.join(job_dir(job["id"]), "job.json"), "w") as f:
        json.dump(job, f)


def load_jobs():
    os.makedirs(JOBS, exist_ok=True)
    for jid in os.listdir(JOBS):
        try:
            with open(os.path.join(job_dir(jid), "job.json")) as f:
                job = json.load(f)
        except (OSError, ValueError):
            continue
        if job.get("state") == "slicing":
            job.update(state="failed", error="The app restarted during the slice.")
        jobs[jid] = job


def prune():
    with jobs_lock:
        old = sorted(jobs.values(), key=lambda j: j["created"])[:-KEEP_JOBS]
        for j in old:
            jobs.pop(j["id"], None)
            shutil.rmtree(job_dir(j["id"]), ignore_errors=True)


def slice_info(path):
    """Print time, grams and filaments from the slice's Metadata/slice_info.config."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("Metadata/slice_info.config"))
        names = z.namelist()
    plate = root.find("plate")
    meta = {m.get("key"): m.get("value") for m in plate.iter("metadata")}
    fils = [{k: f.get(k) for k in ("id", "type", "color", "used_g", "used_m")} for f in plate.iter("filament")]
    warn = [w.get("msg") for w in plate.iter("warning")]
    return {"seconds": int(float(meta.get("prediction") or 0)), "grams": float(meta.get("weight") or 0),
            "filaments": fils, "warnings": warn, "has_png": "Metadata/plate_1.png" in names,
            "objects": [o.get("name") for o in plate.iter("object")]}


def run_slice(jid, printer, quality, tray, bed):
    job = jobs[jid]
    d = job_dir(jid)
    cfg = PRINTERS[printer["model"]]
    with slice_lock:
        job.update(state="slicing", started=time.time())
        save_job(job)
        try:
            preset = cfg["preset"]
            fil = subprocess.run([sys.executable, "-I", BS, "filament-for", PROFILES, preset,
                                  tray["idx"], tray["type"]], capture_output=True, text=True, timeout=120)
            if fil.returncode:
                raise RuntimeError(fil.stderr.strip() or fil.stdout.strip())
            filament = fil.stdout.strip()
            job["settings"] = {"printer": preset, "process": cfg["quality"][quality], "filament": filament,
                               "bed": bed, "tray": tray}
            save_job(job)
            out = os.path.join(d, "out")
            shutil.rmtree(out, ignore_errors=True)
            e = dict(os.environ, PRINTER=preset, PROCESS=cfg["quality"][quality], BED=bed, COLOURS=tray["colour"])
            p = subprocess.run(SLICE + [job["model"], out, filament], capture_output=True, text=True, env=e,
                               timeout=3 * 3600)
            job["log"] = (p.stdout + p.stderr)[-4000:]
            if p.returncode:
                raise RuntimeError("Slicer failed (exit %d)." % p.returncode)
            result = os.path.join(out, os.listdir(out)[0])
            job.update(state="done", result=result, info=slice_info(result))
        except Exception as ex:  # noqa: BLE001 - every failure is shown to the user
            job.update(state="failed", error=str(ex))
            log("job %s failed: %s" % (jid, ex))
        job["finished"] = time.time()
        save_job(job)


def print_name(job):
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.splitext(job["filename"])[0])[:60] or "model"
    return "%s_%s_%s.gcode.3mf" % (stem, job["quality"], job["printer"]["model"].lower())


# --- HTTP handlers -------------------------------------------------------------------------------

def get_printers(h, m, q):
    try:
        h.send(200, {"printers": printers(), "beds": BEDS})
    except (OSError, RuntimeError) as e:
        h.send(502, {"error": "Cannot read printers from the bridge: %s" % e})


def post_job(h, m, q):
    name = os.path.basename((q.get("name") or [""])[0])
    if not re.search(r"\.(stl|3mf|obj|step|stp)$", name, re.I):
        return h.send(400, {"error": "Upload an .stl, .3mf, .obj or .step file."})
    length = int(h.headers.get("Content-Length") or 0)
    if not 0 < length <= MAX_MODEL:
        return h.send(413, {"error": "The file must be 1 byte to 100 MB."})
    jid = secrets.token_hex(6)
    d = job_dir(jid)
    os.makedirs(d)
    model = os.path.join(d, re.sub(r"[^A-Za-z0-9._-]", "_", name))
    with open(model, "wb") as f:
        left = length
        while left:
            chunk = h.rfile.read(min(left, 1 << 20))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    job = {"id": jid, "filename": name, "model": model, "state": "uploaded", "created": time.time()}
    with jobs_lock:
        jobs[jid] = job
    save_job(job)
    prune()
    h.send(200, public(job))


def public(job):
    return {k: v for k, v in job.items() if k not in ("model", "result")}


def get_job(h, m, q):
    job = jobs.get(m.group(1))
    if not job:
        return h.send(404, {"error": "unknown job"})
    out = public(job)
    if job.get("state") == "slicing":
        out["elapsed"] = int(time.time() - job["started"])
    h.send(200, out)


def post_slice(h, m, q):
    job = jobs.get(m.group(1))
    if not job:
        return h.send(404, {"error": "unknown job"})
    if job["state"] == "slicing":
        return h.send(409, {"error": "This job is slicing."})
    body = h.json()
    try:
        printer = next(p for p in printers() if p["serial"] == body.get("serial"))
    except StopIteration:
        return h.send(400, {"error": "Unknown printer."})
    except (OSError, RuntimeError) as e:
        return h.send(502, {"error": str(e)})
    quality, bed = body.get("quality"), body.get("bed") or printer["bed"]
    if quality not in PRINTERS[printer["model"]]["quality"] or bed not in BEDS:
        return h.send(400, {"error": "Unknown quality or plate."})
    tray = next((t for t in printer["trays"] if t["ams"] + ":" + t["id"] == body.get("tray")), None)
    if not tray:
        return h.send(400, {"error": "Pick a loaded AMS tray."})
    job.update(state="queued", printer={k: printer[k] for k in ("serial", "name", "model")}, quality=quality,
               error=None, info=None, log="", sent=None)
    save_job(job)
    threading.Thread(target=run_slice, args=(job["id"], printer, quality, tray, bed), daemon=True).start()
    h.send(202, public(job))


def get_file(h, m, q):
    job = jobs.get(m.group(1))
    if not job or job.get("state") != "done":
        return h.send(404, {"error": "no slice"})
    member = {"plate.png": "Metadata/plate_1.png", "plate.gcode": "Metadata/plate_1.gcode"}[m.group(2)]
    with zipfile.ZipFile(job["result"]) as z:
        if member not in z.namelist():
            return h.send(404, {"error": "not in the slice"})
        data = z.read(member)
    h.send(200, data, "image/png" if member.endswith("png") else "text/plain; charset=utf-8")


def post_print(h, m, q):
    job = jobs.get(m.group(1))
    if not job or job.get("state") != "done":
        return h.send(404, {"error": "no slice"})
    body = h.json()
    if body.get("confirm") is not True:
        return h.send(400, {"error": "Confirm the print first."})
    os.makedirs(SHARE, exist_ok=True)
    dest = os.path.join(SHARE, print_name(job))
    shutil.copyfile(job["result"], dest)
    call = {"serial": job["printer"]["serial"], "file": dest, "use_ams": True, "bed_leveling": True,
            "timelapse": bool(body.get("timelapse")), "force": bool(body.get("force"))}
    token = env("SUPERVISOR_TOKEN", "")
    status, answer = http_json("POST", CORE + "/services/druckwerk/print_file?return_response", call,
                               {"Authorization": "Bearer " + token}, timeout=360)
    job["sent"] = {"at": time.time(), "file": dest, "status": status, "answer": answer}
    save_job(job)
    log("print %s -> %s: %s" % (os.path.basename(dest), job["printer"]["serial"], status))
    if status != 200:
        msg = answer.get("message") or answer.get("error") if isinstance(answer, dict) else answer
        return h.send(502, {"error": "Home Assistant refused the print (%s): %s" % (status, msg)})
    h.send(200, {"answer": answer, "file": os.path.basename(dest)})


ROUTES = [
    ("GET", r"/api/printers", get_printers),
    ("POST", r"/api/jobs", post_job),
    ("GET", r"/api/jobs/" + JOB_ID, get_job),
    ("POST", r"/api/jobs/" + JOB_ID + r"/slice", post_slice),
    ("GET", r"/api/jobs/" + JOB_ID + r"/(plate\.png|plate\.gcode)", get_file),
    ("POST", r"/api/jobs/" + JOB_ID + r"/print", post_print),
]


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 8192:
            raise ValueError("Body too large.")
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("Body must be a JSON object.")
        return body

    def dispatch(self, method):
        u = urlsplit(self.path)
        for meth, regex, fn in ROUTES:
            match = meth == method and re.fullmatch(regex, u.path)
            if match:
                try:
                    return fn(self, match, parse_qs(u.query))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
        self.send(404, {"error": "not found"})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def log_message(self, fmt, *args):
        pass


def main():
    load_jobs()
    server = ThreadingHTTPServer((env("SLICER_HOST", "0.0.0.0"), int(env("SLICER_PORT", "8099"))), Handler)
    server.daemon_threads = True
    log("listening on port %d" % server.server_address[1])
    server.serve_forever()


if __name__ == "__main__":
    main()
