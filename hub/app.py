"""Routes and background work of the Druckwerk app (contract: hub/API.md).

Mounted onto the slicer server (hub/slicer/server.py) by hub.py: install() adds the routes, puts an identity check in front of
every one of them and adds PUT/DELETE dispatch. Home Assistant I/O goes through the `ha` object (hub/ha.py; fakes in tests).
"""
import collections
import hashlib
import hmac
import http.client
import io
import json
import os
import re
import secrets
import sys
import threading
import time
import zipfile
from urllib.parse import parse_qs, urlsplit

import externals
import filament
from ha import HAError
from queue_access import Access, Forbidden, Grants, Unauthorized, caller_from_headers
from queue_engine import WAITING, NotFound, Queue, QueueError
from queue_registry import Registry

HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "server", "date"}
SECRET_HEADER = "X-Hub-Secret"
IDENTITY_HEADERS = ("X-Remote-User-Id", "X-Remote-User-Name", "X-Remote-User-Display-Name")
JOB_ID = r"([0-9a-f]{12})"
# Sensor names of the vendor HA integration after the printer prefix (verified against its translations/en.json).
SENSORS = {"status": "print_status", "stage": "current_stage", "progress": "print_progress",
           "remaining": "remaining_time", "end": "end_time", "layer": "current_layer", "total": "total_layer_count",
           "nozzle": "nozzle_temperature", "nozzle_t": "nozzle_target_temperature", "bed": "bed_temperature",
           "bed_t": "bed_target_temperature", "chamber": "chamber_temperature", "tray": "active_tray",
           "task": "task_name"}
BRIDGE_STATE = {"RUNNING": "running", "FINISH": "finish", "FAILED": "failed", "IDLE": "idle", "PAUSE": "pause"}
UI_FILES = r"(index\.html|style\.css|app\.js|api\.js|util\.js|views/[a-z]+\.js)"
MIME = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8"}
DOMAIN_DEFAULTS = "schedule,input_boolean,binary_sensor,person"


def log(msg):
    print("druckwerk: " + msg, file=sys.stderr, flush=True)


class HttpError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status = status


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def num(v, cast=float):
    try:
        return cast(float(v))
    except (TypeError, ValueError):
        return None


class Capture:
    """Stands in for the request handler so a slicer handler's answer can be post-processed."""

    def __init__(self, h):
        self.h, self.out = h, None

    def __getattr__(self, name):
        return getattr(self.h, name)

    def send(self, status, body, ctype="application/json"):
        self.out = (status, body, ctype)


class App:
    def __init__(self, ha, data_dir, share, bridge, slicer, hostname_fn, port, clock=time.time,
                 trusted_ips=(), ui_dir=None):
        self.ha, self.share, self.bridge, self.s = ha, share, bridge, slicer
        self.hostname_fn, self.port, self.clock, self.trusted_ips = hostname_fn, port, clock, set(trusted_ips)
        self.data_dir, self.ui_dir = data_dir, ui_dir
        os.makedirs(os.path.join(data_dir, "plates"), exist_ok=True)
        self.registry = Registry(data_dir)
        self.spools = filament.Spools(data_dir)
        self.grants = Grants(self.registry, data_dir, self._grant_state, clock)
        self.access = Access(self.registry, self.grants, self.get_user)
        self.queue = Queue(self.registry, data_dir, ha.state, self._call_service, self.get_trays, clock=clock,
                           job_allowed=self.access.job_allowed)
        self.secret = self._load_secret()
        self._cache = {}
        self._cache_lock = threading.Lock()
        # Events: seq starts at the boot time so it keeps growing across restarts (pollers hold the last seq they saw).
        self.seq = int(clock())
        self.events = collections.deque(maxlen=500)
        self.sync_lock = threading.RLock()
        self.known = self._states_of_jobs()
        self.waiting = self._waiting_ids()

    # -- identity --
    def _load_secret(self):
        path = os.path.join(self.data_dir, "hub.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)["secret"]
        secret = secrets.token_urlsafe(32)
        from queue_registry import write_json
        write_json(path, {"secret": secret})
        return secret

    def is_internal(self, h):
        given = h.headers.get(SECRET_HEADER) or ""
        return bool(given) and hmac.compare_digest(given.encode(), self.secret.encode())

    def caller(self, h):
        return caller_from_headers(h.headers, self.is_internal(h))

    def _cached(self, key, ttl, fn):
        """fn() result for ttl seconds; on failure the last good value, else the error."""
        with self._cache_lock:
            at, val = self._cache.get(key, (0, None))
        if self.clock() - at < ttl and val is not None:
            return val
        try:
            val = fn()
        except HAError:
            if val is None:
                raise
            return val
        with self._cache_lock:
            self._cache[key] = (self.clock(), val)
        return val

    def get_user(self, user_id):
        try:
            users = self._cached("users", 30, lambda: self.ha.command("config/auth/list"))
        except HAError:
            return None  # unknown means no admin rights
        u = next((u for u in users if u["id"] == user_id), None)
        if not u or not u.get("is_active", True):
            return None
        return {"name": u.get("name") or "", "is_admin": bool(u.get("is_owner") or "system-admin" in u.get("group_ids", []))}

    def _grant_state(self, entity_id):
        try:
            s = self.ha.states().get(entity_id)
        except HAError:
            return None  # a condition that cannot be read does not grant
        return s["state"] if s else None

    # -- HA registries --
    def registries(self):
        def load():
            return (self.ha.command("config/device_registry/list"), self.ha.command("config/entity_registry/list"))
        try:
            return self._cached("registries", 60, load)
        except HAError:
            return [], []

    def suggested_prefix(self, serial):
        devices, entities = self.registries()
        dev = next((d for d in devices if [externals.HA_VENDOR_DOMAIN, serial] in [list(i) for i in d.get("identifiers", [])]), None)
        if not dev:
            return None
        main = next((e for e in entities if e.get("device_id") == dev["id"] and e.get("unique_id") == serial + "_print_status"), None)
        if main:
            return main["entity_id"].split(".", 1)[1].removesuffix("_print_status")
        return slug(dev.get("name_by_user") or dev.get("name") or "") or None

    def ams(self, serial, states):
        """AMS units of one printer from the entity registry: [{unit, humidity_index, temp, trays: [...]}]."""
        devices, entities = self.registries()
        dev_name = {d["id"]: d.get("name") or "" for d in devices}
        units = {}
        for e in entities:
            uid = e.get("unique_id") or ""
            if e.get("platform") != externals.HA_VENDOR_DOMAIN or "_%s_AMS_" % serial not in uid:
                continue
            ams_serial, _, key = uid.split("_%s_AMS_" % serial, 1)[1].partition("_")
            u = units.setdefault(ams_serial, {"name": dev_name.get(e.get("device_id"), ""), "e": {}})
            u["e"][key] = e["entity_id"]
        out = []
        for u in units.values():
            m = re.search(r"_AMS_(\d+)$", u["name"])
            if not m:
                continue  # rack AMS (HT, 3H) has no slot numbering we can match to spools
            n = int(m.group(1))
            unit = n - 1 if n < 128 else n
            trays = []
            for key, eid in sorted(u["e"].items()):
                tm = re.fullmatch(r"tray_(\d+)", key)
                st = states.get(eid)
                if not tm or not st:
                    continue
                a = st.get("attributes", {})
                slot = int(tm.group(1)) - 1
                empty = a.get("empty") or st["state"] in ("unknown", "unavailable", "Empty", "")
                spool = self.spools.in_tray(serial, unit, slot)
                remain = a.get("remain")
                trays.append({"slot": slot, "type": "" if empty else a.get("type") or "",
                              "colour": "" if empty else re.sub(r"[^0-9A-Fa-f]", "", a.get("color") or "")[:6].upper(),
                              "name": "" if empty else st["state"],
                              "remain_pct": remain if isinstance(remain, (int, float)) and remain >= 0 else None,
                              "spool_id": spool["id"] if spool else None})
            hum = states.get(u["e"].get("humidity_index", ""))
            temp = states.get(u["e"].get("temperature", ""))
            out.append({"unit": unit, "humidity_index": num(hum and hum["state"], int),
                        "temp": num(temp and temp["state"]), "trays": trays})
        return sorted(out, key=lambda a: a["unit"])

    def external(self, serial, states):
        _, entities = self.registries()
        rx = re.compile(r"_%s_ExternalSpool\w*_external_spool$" % re.escape(serial))
        for e in entities:
            if e.get("platform") == externals.HA_VENDOR_DOMAIN and rx.search(e.get("unique_id") or ""):
                st = states.get(e["entity_id"])
                if st and not st["attributes"].get("empty") and st["state"] not in ("unknown", "unavailable", "Empty"):
                    a = st["attributes"]
                    return {"type": a.get("type") or "", "name": st["state"],
                            "colour": re.sub(r"[^0-9A-Fa-f]", "", a.get("color") or "")[:6].upper()}
        return None

    def get_trays(self, serial):
        try:
            states = self.ha.states(max_age=0)
        except HAError:
            return []
        return [{"type": t["type"], "colour": "#" + t["colour"]} for a in self.ams(serial, states)
                for t in a["trays"] if t["type"]]

    # -- calls to the bridge and HA --
    def bridge_json(self, method, path, body=None, timeout=30):
        conn = http.client.HTTPConnection(*self.bridge, timeout=timeout)
        try:
            conn.request(method, path, json.dumps(body).encode() if body is not None else None,
                         {"Content-Type": "application/json"})
            r = conn.getresponse()
            raw = r.read()
        except OSError as e:
            raise HttpError(502, "bridge is not reachable: %s" % e)
        finally:
            conn.close()
        try:
            data = json.loads(raw or b"null")
        except ValueError:
            data = {"error": raw.decode(errors="replace")[:300]}
        return r.status, data

    def bridge_ok(self, method, path, body=None, timeout=30):
        status, data = self.bridge_json(method, path, body, timeout)
        if status != 200:
            raise HttpError(status if status in (400, 404, 409) else 502,
                            (data or {}).get("error", "bridge answered %s" % status) if isinstance(data, dict) else str(data))
        return data

    def proxy(self, h, path, query=""):
        """Stream a bridge answer to the client (the camera stream has no length)."""
        conn = http.client.HTTPConnection(*self.bridge, timeout=300)
        try:
            conn.request(h.command, path + ("?" + query if query else ""),
                         h.rfile.read(int(h.headers.get("Content-Length") or 0)) or None,
                         {k: v for k, v in h.headers.items() if k.lower() in ("content-type",)})
            r = conn.getresponse()
        except OSError as e:
            return h.send(502, {"error": "bridge is not reachable: %s" % e})
        h.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in HOP:
                h.send_header(k, v)
        h.close_connection = True  # HTTP/1.0 style: the body ends when we close
        h.end_headers()
        try:
            while chunk := r.read(65536):
                h.wfile.write(chunk)
        except OSError:
            pass  # viewer left
        finally:
            conn.close()

    def _call_service(self, domain, service, data):
        data = dict(data)
        if (domain, service) == ("druckwerk", "print_file") and not os.path.isabs(data["file"]):
            data["file"] = os.path.join(self.share, data["file"])
        return self.ha.call_service(domain, service, data, response=(domain == "druckwerk"))

    # -- printers --
    def entity(self, p, key):
        return "sensor.%s_%s" % (p["prefix"], SENSORS[key])

    def bridge_print(self, serial):
        try:
            status, data = self.bridge_json("GET", "/api/status/" + serial, timeout=10)
        except HttpError:
            return {}
        return (data or {}).get("print") or {} if status == 200 and isinstance(data, dict) else {}

    def printer_view(self, p, role, states):
        serial, pre = p["serial"], p["prefix"]

        def v(key):
            s = states.get(self.entity(p, key))
            return None if not s or s["state"] in ("unknown", "unavailable") else s["state"]
        status = v("status")
        b = {}
        if status is None:
            b = self.bridge_print(serial)  # no HA entities yet: fall back to the bridge's MQTT status
            status = BRIDGE_STATE.get(b.get("gcode_state"), None)
        tray = states.get(self.entity(p, "tray"))
        ta = tray["attributes"] if tray and tray["state"] not in ("none", "unknown", "unavailable") else None
        hms = states.get("binary_sensor.%s_hms_errors" % pre)
        hms_out = []
        if hms:
            a = hms["attributes"]
            for i in range(1, int(a.get("Count") or 0) + 1):
                hms_out.append({"code": a.get("%d-Code" % i, ""), "severity": a.get("%d-Severity" % i, "info"),
                                "text": a.get("%d-Error" % i, ""), "url": a.get("%d-Wiki" % i, "")})
        online = states.get("binary_sensor.%s_online" % pre)
        job = next((j for j in self.queue.jobs if j.get("printer") == serial and j["state"] in ("starting", "printing")), None)
        has_cover = "image.%s_cover_image" % pre in states
        return {
            "serial": serial, "name": p["name"], "model": p["model"], "role": role,
            "online": online["state"] == "on" if online else status not in (None, "offline"),
            "status": status, "stage": v("stage"),
            "progress": num(v("progress")) if v("progress") is not None else num(b.get("mc_percent")),
            "remaining_min": num(v("remaining"), int) if v("remaining") is not None else num(b.get("mc_remaining_time"), int),
            "end_time": v("end"),
            "layer": num(v("layer"), int) if v("layer") is not None else num(b.get("layer_num"), int),
            "total_layers": num(v("total"), int) if v("total") is not None else num(b.get("total_layer_num"), int),
            "nozzle_temp": num(v("nozzle")) if v("nozzle") is not None else num(b.get("nozzle_temper")),
            "nozzle_target": num(v("nozzle_t")) if v("nozzle_t") is not None else num(b.get("nozzle_target_temper")),
            "bed_temp": num(v("bed")) if v("bed") is not None else num(b.get("bed_temper")),
            "bed_target": num(v("bed_t")) if v("bed_t") is not None else num(b.get("bed_target_temper")),
            "chamber_temp": num(v("chamber")) if v("chamber") is not None else num(b.get("chamber_temper")),
            "task_name": v("task") or b.get("subtask_name") or None,
            "cover_url": "api/printers/%s/cover" % serial if has_cover else None,
            "camera_url": "api/printers/%s/camera" % serial,
            "active_tray": {"unit": num(ta.get("ams_index"), int), "slot": num(ta.get("tray_index"), int)}
            if ta and ta.get("ams_index") is not None and ta.get("tray_index") is not None else None,
            "ams": self.ams(serial, states), "external": self.external(serial, states), "hms": hms_out,
            "current_job": dict(job) if job else None,
            "queue_length": sum(1 for j in self.queue.jobs if j["state"] in WAITING and serial in self.access.serials_of(j)),
        }

    # -- grams, cost, events --
    def _states_of_jobs(self):
        return {j["id"]: j["state"] for j in self.queue.jobs + self.queue.history}

    def _waiting_ids(self):
        return [j["id"] for j in self.queue.jobs if j["state"] in WAITING]

    def emit(self, type_, serial, job):
        self.seq += 1
        # job always carries id, file, owner_name, target and printer (None until a printer took it).
        self.events.append({"seq": self.seq, "type": type_, "serial": serial or "",
                            "job": dict({"printer": None}, **job) if job else None})

    def sync(self):
        """Turn queue changes into events, and settle a finished job's spools and cost exactly once."""
        with self.sync_lock, self.queue.lock:
            now = self._states_of_jobs()
            changed = now != self.known or self._waiting_ids() != self.waiting
            for j in self.queue.jobs + self.queue.history:
                before, state = self.known.get(j["id"]), j["state"]
                if before == state:
                    continue
                serial = j.get("printer") or (j["target"] if j["target"] != "any" else "")
                if state == "pending_approval":
                    self.emit("approval_needed", serial, j)
                elif state == "starting":
                    self.emit("job_started", serial, j)
                elif state == "done":
                    self.settle(j)
                    self.emit("job_done", serial, j)
                elif state == "failed" and before in ("starting", "printing"):
                    self.emit("job_failed", serial, j)
            if changed:
                self.emit("queue_changed", "", None)
                self.queue._save()
            self.known, self.waiting = now, self._waiting_ids()

    def settle(self, job):
        """Take the grams off the spools in use and price the job."""
        serial = job.get("printer")
        printer = self.registry.get(serial) or {}
        grams = job.get("filament_grams") or []
        hint = tuple(job["slice_tray"]) if job.get("slice_tray") and len(job["filaments"]) == 1 else None
        prices, used = [], []
        for f, g in zip(job["filaments"], grams):
            sp = self.spools.match(serial, f["type"], f["colour"], hint)
            prices.append((sp["price_per_kg"] or 0) if sp else 0)
            if sp and g:
                self.spools.consume(sp, g)
                used.append({"spool_id": sp["id"], "grams": g})
        job["spools_used"] = used
        job["cost"] = filament.job_cost(grams, prices, job.get("print_seconds") or 0, printer.get("model"),
                                        printer.get("electricity_kwh_price") or 0)
        try:  # the shared copy is only needed until the print is done; retry is for failed jobs
            os.remove(os.path.join(self.share, job["file"]))
        except OSError:
            pass

    def discovery(self):
        """Supervisor discovery message that hands the integration host, ports and secret."""
        return {"service": "druckwerk", "config": {"host": self.hostname_fn(), "bridge_port": self.bridge[1],
                                                       "hub_port": self.port, "secret": self.secret}}

    def tick_all(self):
        for p in self.registry.all():
            try:
                self.queue.tick(p["serial"])
            except Exception as e:  # noqa: BLE001 - one printer's HA error must not stop the others
                log("tick %s: %s" % (p["serial"], e))
        try:
            self.sync()
        except Exception as e:  # noqa: BLE001
            log("sync: %s" % e)

    def run_ticker(self, stop, every=10):
        while not stop.wait(every):
            self.tick_all()

    def on_state(self, entity_id, state):
        for p in self.registry.all():
            if entity_id == self.entity(p, "stage"):
                self.queue.observe(p["serial"], state)

    # -- request plumbing --
    def need(self, c, need):
        if need == "ha_admin" and not self.access.is_global_admin(c):
            raise Forbidden("rule: only Home Assistant admins can do this")
        if need == "internal" and not c.internal:
            raise Forbidden("rule: only the Home Assistant integration can do this")

    def wrap(self, fn, need="user"):
        def run(h, m, q):
            c = self.caller(h)
            self.need(c, need)
            h.send(200, fn(h, m, q, c))
        return run

    def dispatch(self, h, method, routes):
        u = urlsplit(h.path)
        internal = self.is_internal(h)
        if not internal and self.trusted_ips and h.client_address[0] not in self.trusted_ips:
            for k in IDENTITY_HEADERS:  # only the Supervisor's ingress gateway may vouch for a user
                del h.headers[k]
        for meth, regex, fn in routes:
            m = meth == method and re.fullmatch(regex, u.path)
            if not m:
                continue
            # Mutating requests are answered after sync(), so a client that reads the answer then polls events sees the change.
            out = Capture(h) if method != "GET" else h
            try:
                if not self.is_public(u.path) and not internal and not (h.headers.get("X-Remote-User-Id") or "").strip():
                    raise Unauthorized("missing X-Remote-User-Id: requests need a Home Assistant identity")
                fn(out, m, parse_qs(u.query))
            except Unauthorized as e:
                return h.send(401, {"error": str(e)})
            except Forbidden as e:
                return h.send(403, {"error": str(e)})
            except NotFound as e:
                return h.send(404, {"error": str(e)})
            except QueueError as e:
                return h.send(409, {"error": str(e)})
            except HttpError as e:
                return h.send(e.status, {"error": str(e)})
            except HAError as e:
                return h.send(502, {"error": str(e)})
            except ValueError as e:
                return h.send(400, {"error": str(e)})
            if out is not h:
                try:
                    self.sync()
                except Exception as e:  # noqa: BLE001
                    log("sync: %s" % e)
                if out.out:
                    h.send(*out.out)
            return
        h.send(404, {"error": "not found"})

    def is_public(self, path):
        """The web UI's own files carry no data, so they load without identity (the API behind them does not)."""
        return bool(self.ui_dir) and (path == "/" or re.fullmatch("/" + UI_FILES, path) is not None)

    def get_ui(self, h, m, q):
        name = (m.group(1) if m.re.groups else None) or "index.html"
        try:
            with open(os.path.join(self.ui_dir, name), "rb") as f:
                h.send(200, f.read(), MIME[name.rsplit(".", 1)[1]])
        except OSError:
            raise NotFound("no such file")

    # -- route functions --
    def role_or_none(self, c, serial):
        return self.access.role(c, serial)

    def get_me(self, h, m, q, c):
        roles = [{"serial": p["serial"], "role": self.access.role(c, p["serial"])} for p in self.registry.all()]
        u = self.get_user(c.user_id) or {}
        return {"user_id": c.user_id, "name": c.display or u.get("name", ""), "is_admin": self.access.is_global_admin(c),
                "setup_done": bool(self.registry.all()), "printers": [r for r in roles if r["role"]]}

    def get_setup(self, h, m, q, c):
        health = self.bridge_ok("GET", "/api/health", timeout=10)
        listed = self.bridge_ok("GET", "/api/printers", timeout=20)
        printers = []
        for d in listed:
            serial = d["serial"]
            cfg = self.registry.get(serial)
            printers.append({"serial": serial, "device_name": d.get("name") or serial,
                             "model": d.get("dev_product_name") or d.get("dev_model_name") or "", "account": d.get("account"),
                             "configured": bool(cfg), "suggested_prefix": self.suggested_prefix(serial),
                             "config": self.config_view(cfg) if cfg else None})
        return {"accounts": [{"id": a["id"], "email": a["email"], "logged_in": a.get("logged_in", True),
                              "daemon_running": bool((a.get("daemon") or {}).get("running"))} for a in health["accounts"]],
                "printers": printers}

    @staticmethod
    def config_view(p):
        return {"serial": p["serial"], "name": p["name"], "model": p["model"], "account": p["account"],
                "ha_prefix": p["prefix"], "plate": p["plate_type"], "nozzle": p["nozzle"],
                "pause_entities": p["pause_entities"], "electricity_kwh_price": p.get("electricity_kwh_price") or 0}

    def login(self, step):
        def run(h, m, q, c):
            b = h.json()
            return self.bridge_ok("POST", "/api/login/" + step, b, timeout=120)
        return run

    def delete_account(self, h, m, q, c):
        return self.bridge_ok("DELETE", "/api/accounts/" + m.group(1))

    def put_printer(self, h, m, q, c):
        serial, b = m.group(1), h.json()
        prefix = str(b.get("ha_prefix") or "").strip()
        if not prefix:
            raise ValueError("ha_prefix is required")
        if "sensor.%s_print_status" % prefix not in self.ha.states(max_age=0):
            raise ValueError("Home Assistant has no sensor.%s_print_status: check the printer integration's entity prefix" % prefix)
        price = b.get("electricity_kwh_price", 0)
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
            raise ValueError("electricity_kwh_price must be a number >= 0")
        known = next((d for d in self.bridge_ok("GET", "/api/printers", timeout=20) if d["serial"] == serial), None)
        old = self.registry.get(serial)
        if not known and not old:
            raise NotFound("the logged-in accounts have no printer with that serial")
        model = (known and (known.get("dev_product_name") or known.get("dev_model_name"))) or old["model"]
        p = self.registry.put(serial, name=b.get("name"), model=model, account=known["account"] if known else old["account"],
                              prefix=prefix, plate_type=b.get("plate") or "", nozzle=b.get("nozzle") or "",
                              pause_entities=b.get("pause_entities") or [], electricity_kwh_price=price)
        return self.config_view(p)

    def delete_printer(self, h, m, q, c):
        if not self.registry.get(m.group(1)):
            raise NotFound("unknown printer")
        self.registry.remove(m.group(1))
        return {"removed": m.group(1)}

    def get_integration(self, h, m, q, c):
        return {"url": "http://%s:%s" % (self.hostname_fn(), self.port), "secret": self.secret}

    def get_printers(self, h, m, q, c):
        states = self.ha.states()
        out = []
        for p in self.registry.all():
            role = self.access.role(c, p["serial"])
            if role:
                out.append(self.printer_view(p, role, states))
        return out

    def printer_access(self, c, serial, admin=False):
        p = self.registry.get(serial)
        role = self.access.role(c, serial) if p else None
        if not role:
            raise NotFound("unknown printer") if not p else Forbidden("rule: you have no active grant on this printer")
        return p

    def get_camera(self, h, m, q, c):
        serial = m.group(1)
        self.printer_access(c, serial)
        kind = "snapshot" if (q.get("mode") or [""])[0] == "snapshot" else "live"
        query = urlsplit(h.path).query if kind == "live" else ""
        self.proxy(h, "/api/camera/%s/%s" % (serial, kind), query)

    def get_cover(self, h, m, q, c):
        p = self.printer_access(c, m.group(1))
        ctype, data = self.ha.image("image.%s_cover_image" % p["prefix"])
        h.send(200, data, ctype)

    # -- slices and jobs --
    def own(self, c, job_id):
        job = self.s.jobs.get(job_id)
        if not job:
            raise NotFound("unknown job")
        if not (self.access.is_global_admin(c) or job.get("owner_user_id") == c.user_id):
            raise Forbidden("rule: slices belong to the person who uploaded them")
        return job

    def slicer_job(self, fn, check=None):
        def run(h, m, q):
            c = self.caller(h)
            job = self.own(c, m.group(1))
            if check:
                check(h, c, job)
            return fn(h, m, q)
        return run

    def check_slice(self, h, c, job):
        body = h.json()
        h.json = lambda: body  # the slicer handler reads the body again
        if self.access.role(c, str(body.get("serial"))) is None:
            raise Forbidden("rule: slicing needs an active grant on the printer")

    def check_print(self, h, c, job):
        serial = (job.get("printer") or {}).get("serial")
        if self.access.role(c, serial) != "admin":
            raise Forbidden("rule: only an admin of the printer can print directly; use the queue")

    def post_job(self, h, m, q):
        c = self.caller(h)
        cap = Capture(h)
        self.s.post_job(cap, m, q)
        status, body, ctype = cap.out
        if status == 200:
            job = self.s.jobs[body["id"]]
            job["owner_user_id"] = c.user_id
            self.s.save_job(job)
            body = self.s.public(job)
        h.send(status, body, ctype)

    def queue_job(self, h, m, q, c):
        job = self.own(c, m.group(1))
        if job.get("state") != "done":
            raise QueueError("slice the model first")
        b = h.json()
        target = b.get("target")
        if not target:
            raise ValueError("target is required: a serial or any")
        model = b.get("model") or job["printer"]["model"]
        use = filament.slice_usage(job["result"])
        name = self.s.print_name(job)
        name = name[:-len(".gcode.3mf")].replace("..", "_") + "_" + secrets.token_hex(3) + ".gcode.3mf"
        added = self.access.bind(self.queue, c).add(
            name, job["filename"], target, model,
            [{"type": f["type"], "colour": f["colour"]} for f in use["filaments"]], b.get("options"))
        os.makedirs(self.share, exist_ok=True)
        with open(job["result"], "rb") as src:
            # Written after the job passed the access check, so a refused request leaves nothing in the share.
            with open(os.path.join(self.share, name), "wb") as dst:
                dst.write(src.read())
        png = None
        with zipfile.ZipFile(job["result"]) as z:
            if "Metadata/plate_1.png" in z.namelist():
                with open(os.path.join(self.data_dir, "plates", added["id"] + ".png"), "wb") as f:
                    f.write(z.read("Metadata/plate_1.png"))
                png = "api/queue/%s/plate.png" % added["id"]
        tray = (job.get("settings") or {}).get("tray")
        with self.queue.lock:
            added.update(grams=use["grams"], filament_grams=[f["grams"] for f in use["filaments"]],
                         print_seconds=use["seconds"], plate_png=png,
                         slice_tray=[int(tray["ams"]), int(tray["id"])] if tray else None)
            self.queue._save()
        return added

    def get_plate(self, h, m, q, c):
        jid = m.group(1)
        snap = self.access.bind(self.queue, c).snapshot()
        if not any(j["id"] == jid for j in snap["jobs"] + snap["history"]):
            raise NotFound("no such job")
        try:
            with open(os.path.join(self.data_dir, "plates", jid + ".png"), "rb") as f:
                h.send(200, f.read(), "image/png")
        except OSError:
            raise NotFound("no plate image")

    # -- spools --
    def spool_admin(self, c, spool):
        """HA admins manage all spools; the admin or owner of a printer manages the spools assigned to it."""
        if spool.get("printer") and self.access.role(c, spool["printer"]) == "admin":
            return
        if not self.access.is_global_admin(c):
            raise Forbidden("rule: only an admin of the spool's printer (or a Home Assistant admin) manages spools")

    def get_spools(self, h, m, q, c):
        return [s for s in self.spools.items
                if (self.access.role(c, s["printer"]) if s["printer"] else self.access.is_global_admin(c))]

    def post_spool(self, h, m, q, c):
        b = h.json()
        self.spool_admin(c, {"printer": b.get("printer")})
        if b.get("printer") and not self.registry.get(b["printer"]):
            raise ValueError("unknown printer")
        return self.spools.add(**b)

    def put_spool(self, h, m, q, c):
        s = self.spools.get(m.group(1))
        if not s:
            raise NotFound("no such spool")
        self.spool_admin(c, s)
        b = h.json()
        if b.get("printer"):  # unlinking is fine; linking needs admin of the target printer too
            self.spool_admin(c, {"printer": b["printer"]})
        return self.spools.update(m.group(1), **b)

    def delete_spool(self, h, m, q, c):
        s = self.spools.get(m.group(1))
        if not s:
            raise NotFound("no such spool")
        self.spool_admin(c, s)
        self.spools.remove(m.group(1))
        return {"removed": m.group(1)}

    # -- HA lookups, events --
    def get_ha_users(self, h, m, q, c):
        users = self.ha.command("config/auth/list")
        return [{"id": u["id"], "name": u.get("name") or "",
                 "is_admin": bool(u.get("is_owner") or "system-admin" in u.get("group_ids", []))}
                for u in users if u.get("is_active", True) and not u.get("system_generated")]

    def get_ha_entities(self, h, m, q, c):
        domains = set(((q.get("domains") or [DOMAIN_DEFAULTS])[0]).split(","))
        out = [{"entity_id": eid, "name": s["attributes"].get("friendly_name") or eid, "state": s["state"]}
               for eid, s in self.ha.states().items() if eid.split(".", 1)[0] in domains]
        return sorted(out, key=lambda e: e["entity_id"])

    def grant_view(self, g):
        u = self.get_user(g["user_id"]) or {}
        return dict(g, user_name=u.get("name") or g["user_id"], active=self.grants.is_active(g))

    def get_grants(self, h, m, q, c):
        return [self.grant_view(g) for g in self.access.bind(self.queue, c).grants_list()]

    def post_grants(self, h, m, q, c):
        from queue_routes import GRANT_FIELDS
        b = h.json()
        return self.grant_view(self.access.bind(self.queue, c).grants_add(**{k: b[k] for k in GRANT_FIELDS if k in b}))

    def get_events(self, h, m, q, c):
        self.sync()
        if "since" not in q:
            return {"seq": self.seq, "events": []}  # a first poll starts from now, no backlog
        since = num(q["since"][0], int)
        if since is None:
            raise ValueError("since must be an integer")
        return {"seq": self.seq, "events": [e for e in list(self.events) if e["seq"] > since]}

    def bridge_route(self, h, m, q):
        self.need(self.caller(h), "ha_admin")
        self.proxy(h, "/" + m.group(1), urlsplit(h.path).query)

    # -- mounting --
    def routes(self):
        from queue_routes import make_routes
        w = self.wrap
        mine = [
            ("GET", r"/api/me", w(self.get_me)),
            ("GET", r"/api/setup", w(self.get_setup, "ha_admin")),
            ("POST", r"/api/setup/login/start", w(self.login("start"), "ha_admin")),
            ("POST", r"/api/setup/login/code", w(self.login("code"), "ha_admin")),
            ("DELETE", r"/api/setup/accounts/([a-z0-9_]{1,64})", w(self.delete_account, "ha_admin")),
            ("PUT", r"/api/setup/printers/([A-Za-z0-9_-]{1,40})", w(self.put_printer, "ha_admin")),
            ("DELETE", r"/api/setup/printers/([A-Za-z0-9_-]{1,40})", w(self.delete_printer, "ha_admin")),
            ("GET", r"/api/integration", w(self.get_integration, "ha_admin")),
            ("GET", r"/api/printers", w(self.get_printers)),
            ("GET", r"/api/printers/([^/]+)/camera", lambda h, m, q: self.get_camera(h, m, q, self.caller(h))),
            ("GET", r"/api/printers/([^/]+)/cover", lambda h, m, q: self.get_cover(h, m, q, self.caller(h))),
            ("GET", r"/api/queue/" + JOB_ID + r"/plate\.png", lambda h, m, q: self.get_plate(h, m, q, self.caller(h))),
            ("POST", r"/api/jobs", self.post_job),
            ("GET", r"/api/jobs/" + JOB_ID, self.slicer_job(self.s.get_job)),
            ("POST", r"/api/jobs/" + JOB_ID + r"/slice", self.slicer_job(self.s.post_slice, self.check_slice)),
            ("GET", r"/api/jobs/" + JOB_ID + r"/(plate\.png|plate\.gcode)", self.slicer_job(self.s.get_file)),
            ("POST", r"/api/jobs/" + JOB_ID + r"/print", self.slicer_job(self.s.post_print, self.check_print)),
            ("POST", r"/api/jobs/" + JOB_ID + r"/queue", w(self.queue_job)),
            ("GET", r"/api/spools", w(self.get_spools)),
            ("POST", r"/api/spools", w(self.post_spool)),
            ("PUT", r"/api/spools/([0-9a-f]{12})", w(self.put_spool)),
            ("DELETE", r"/api/spools/([0-9a-f]{12})", w(self.delete_spool)),
            ("GET", r"/api/ha/users", w(self.get_ha_users, "ha_admin")),
            ("GET", r"/api/ha/entities", w(self.get_ha_entities, "ha_admin")),
            ("GET", r"/api/events", w(self.get_events, "internal")),
            ("GET", r"/api/grants", w(self.get_grants)),
            ("POST", r"/api/grants", w(self.post_grants)),
            # The bridge's own page and API (account login), for HA admins only.
            ("GET", r"/api/bridge/(.*)", self.bridge_route),
            ("POST", r"/api/bridge/(.*)", self.bridge_route),
            ("DELETE", r"/api/bridge/(.*)", self.bridge_route),
        ]
        if self.ui_dir:
            mine += [("GET", "/", self.get_ui), ("GET", "/" + UI_FILES, self.get_ui)]
        return mine + make_routes(self.queue, self.access, self.is_internal)

    def install(self):
        """Replace the slicer's overlapping routes with ours, check identity first, add PUT and DELETE."""
        s, mine = self.s, self.routes()
        taken = {(meth, rx) for meth, rx, _ in mine}
        s.ROUTES[:] = mine + [r for r in s.ROUTES if (r[0], r[1]) not in taken and r[:2] != ("GET", "/api/printers")]
        s.Handler.dispatch = lambda h, method: self.dispatch(h, method, s.ROUTES)
        s.Handler.do_PUT = lambda h: h.dispatch("PUT")
        s.Handler.do_DELETE = lambda h: h.dispatch("DELETE")
