#!/usr/bin/env python3
"""Druckwerk bridge: HTTP front-end (login page + JSON API) in front of the boocloud-bridge daemon.

One boocloud-bridge daemon per cloud account (the plugin holds one login per process).
Routes: GET /, GET /api/health, POST /api/login/start, POST /api/login/code, DELETE /api/accounts/<id>,
GET /api/printers, GET /api/status/<serial>, GET /api/ams/<serial>, GET /api/camera/<serial>/live, POST /api/print.
"""
import base64
import binascii
import http.client
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.dirname(HERE)):  # python -I drops the script dir; the repo keeps externals.py one level up
    sys.path.insert(0, _d)
import externals  # noqa: E402

env = os.environ.get
DATA = env("DW_DATA", "/data")
API_BASE = env("DW_API_BASE", externals.CLOUD_API_BASE)
PORT_BASE = int(env("DW_DAEMON_PORT_BASE", "8765"))
CERT_SRC = env("DW_CERT", "/opt/bridge/slicer_base64.cer")
DAEMON_CMD = shlex.split(env("DW_DAEMON_CMD", "/bridge.sh"))
BACKOFF_MIN = float(env("DW_BACKOFF_MIN", "5"))
BACKOFF_MAX = float(env("DW_BACKOFF_MAX", "60"))
LIVE_IDLE = float(env("DW_LIVE_IDLE", "30"))  # seconds an unused live upstream stays open for a quick reopen
MAX_PRINT_BODY = 50 * 1024 * 1024
MAX_SMALL_BODY = 8192
ACCOUNTS = os.path.join(DATA, "accounts")
plugin = None  # set in main when the image fetches the vendor plugin at start (hub/bridge/plugin.py)
SLICER_HEADERS = externals.CLOUD_HEADERS  # the cloud checks the client name
SERIAL = re.compile(r"[A-Za-z0-9_-]{1,64}")

secrets_seen = set()  # token strings, masked in everything we log
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy


def log(message):
    for s in secrets_seen:
        message = message.replace(s, "***")
    print("druckwerk-bridge: " + message, file=sys.stderr, flush=True)


class Upstream(Exception):
    """The cloud or the daemon did not answer."""


def call(method, url, headers=None, body=None, timeout=15):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "application/json")
    except (OSError, ValueError) as e:
        raise Upstream(type(e).__name__)


def read_cloud(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f).get("cloud", {})
    except (OSError, ValueError):
        return {}


def write_private(path, text):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


# ---- accounts and daemon supervision ----

accounts = {}  # id -> Account
lock = threading.RLock()  # guards accounts, pending and the login calls


def slug(email):
    return re.sub(r"[^a-z0-9]+", "_", email.lower()).strip("_")[:64]


def pick_port():
    used = {a.port for a in accounts.values()}
    return next(p for p in range(PORT_BASE, PORT_BASE + 1000) if p not in used)


def install_cert(home):
    dest = os.path.join(home, ".local/share/bambox/cert/slicer_base64.cer")  # where the bridge looks first
    if os.path.exists(CERT_SRC) and not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(CERT_SRC, dest)


class Account:
    def __init__(self, id, email):
        self.id, self.email = id, email
        self.dir = os.path.join(ACCOUNTS, id)
        self.creds = os.path.join(self.dir, "credentials.toml")
        self.printers = os.path.join(self.dir, "printers.json")
        self.home = os.path.join(self.dir, "home")
        os.makedirs(self.home, mode=0o700, exist_ok=True)
        portfile = os.path.join(self.dir, "port")
        try:
            with open(portfile) as f:
                self.port = int(f.read())
        except (OSError, ValueError):
            self.port = pick_port()
            write_private(portfile, str(self.port))  # kept so the port stays stable across restarts
        self.daemon = None

    def url(self, path):
        return "http://%s:%d%s" % (env("DW_DAEMON_HOST", "127.0.0.1"), self.port, path)

    def devices(self):
        try:
            with open(self.printers) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []


class Daemon:
    def __init__(self, acc):
        self.acc, self.proc, self.restarts = acc, None, 0
        self.dead = False
        self.wake = threading.Event()
        threading.Thread(target=self.loop, daemon=True).start()

    def restart(self):
        """Make the daemon reread the credentials."""
        self.wake.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def stop(self):
        self.dead = True
        self.wake.set()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    def loop(self):
        delay = BACKOFF_MIN
        tag = "[%s] " % self.acc.id
        while not self.dead:
            if plugin:
                plugin.ready.wait()  # a daemon without the plugin would only crash-loop
            began = time.time()
            try:
                install_cert(self.acc.home)
                self.proc = subprocess.Popen(DAEMON_CMD + [self.acc.creds, str(self.acc.port)],
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                             errors="replace", env=dict(os.environ, HOME=self.acc.home))
                if self.dead:  # stop() ran before the process existed
                    self.proc.kill()
                for line in self.proc.stdout:
                    log(tag + "daemon: " + line.rstrip())
                    # Under qemu an abort leaves the process hanging instead of exiting.
                    if line.startswith(("error:", "terminate called", "qemu: uncaught")):
                        self.proc.kill()
                code = self.proc.wait()
            except OSError as e:
                code = "start failed: %s" % type(e).__name__
            if self.dead:
                return
            if self.wake.is_set():  # restart requested by a new login
                self.wake.clear()
                delay = BACKOFF_MIN
                continue
            self.restarts += 1
            if time.time() - began > BACKOFF_MAX:  # it ran for a while, so this is a fresh failure
                delay = BACKOFF_MIN
            log(tag + "daemon exited (%s), restarting in %.1fs" % (code, delay))
            if self.wake.wait(delay):
                self.wake.clear()
                delay = BACKOFF_MIN
            else:
                delay = min(delay * 2, BACKOFF_MAX)

    def running(self):
        return self.proc is not None and self.proc.poll() is None


def add_account(id, email):
    acc = accounts[id] = Account(id, email)
    acc.daemon = Daemon(acc)
    return acc


def migrate_v01():
    """v0.1 kept one account in /data; move it into an account dir once."""
    old_creds, old_printers = os.path.join(DATA, "credentials.toml"), os.path.join(DATA, "printers.json")
    if not os.path.exists(old_creds):
        return
    id = slug(read_cloud(old_creds).get("email", "")) or "legacy"
    d = os.path.join(ACCOUNTS, id)
    os.makedirs(os.path.join(d, "home"), mode=0o700, exist_ok=True)
    # Credentials move last: if we crash before that, the next start repeats the whole move.
    for old, name in ((old_printers, "printers.json"), (old_creds, "credentials.toml")):
        if os.path.exists(old):
            os.chmod(old, 0o600)
            os.replace(old, os.path.join(d, name))
    log("migrated v0.1 login to account " + id)


def load_accounts():
    migrate_v01()
    for id in sorted(os.listdir(ACCOUNTS)) if os.path.isdir(ACCOUNTS) else []:
        cloud_cfg = read_cloud(os.path.join(ACCOUNTS, id, "credentials.toml"))
        if cloud_cfg.get("token"):
            secrets_seen.update(v for k, v in cloud_cfg.items() if k in ("token", "refresh_token") and v)
            add_account(id, cloud_cfg.get("email", id))


# ---- login ----

pending = {}  # lower-case email -> email and, for the 2FA step, the tfaKey; the password is never kept


def cloud(method, path, body=None, token=None, parse=True):
    headers = dict(SLICER_HEADERS)
    if token:
        headers["Authorization"] = "Bearer " + token
    status, raw, _ = call(method, API_BASE + path, headers, None if body is None else json.dumps(body).encode())
    if status >= 400:
        raise Upstream("%s cloud answered HTTP %d for %s" % (externals.CLOUD_NAME, status, path))
    if not parse:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise Upstream("%s cloud sent invalid JSON for %s" % (externals.CLOUD_NAME, path))


def advance(email, data):
    """Look at a login answer: finish, or say which code is needed next."""
    if data.get("accessToken"):
        finish(email, data["accessToken"], data.get("refreshToken", ""))
        return "done"
    if data.get("tfaKey"):
        pending[email.lower()] = {"email": email, "kind": "tfa", "tfaKey": data["tfaKey"]}
        return "need_tfa"
    if data.get("loginType") == "verifyCode":
        pending[email.lower()] = {"email": email, "kind": "code"}
        # The real cloud answers this call without a JSON body (upstream auth.py checks only the status).
        cloud("POST", "/v1/user-service/user/sendemail/code", {"email": email, "type": "codeLogin"}, parse=False)
        return "need_code"
    raise Upstream("Login failed: no access token in the answer")


def finish(email, token, refresh):
    id = slug(email)
    if id in accounts and accounts[id].email.lower() != email.lower():
        raise ValueError("Another account already uses the id " + id)
    secrets_seen.update(s for s in (token, refresh) if s)
    uid = str(cloud("GET", "/v1/design-user-service/my/preference", token=token).get("uid", ""))
    devices = [d for d in cloud("GET", "/v1/iot-service/api/user/bind", token=token).get("devices", []) if d.get("dev_id")]
    q = json.dumps  # a JSON string is a valid TOML basic string
    lines = ["[cloud]"] + ["%s = %s" % (k, q(v)) for k, v in
                           (("token", token), ("refresh_token", refresh), ("uid", uid), ("email", email))]
    used = set()
    for d in devices:
        slug_ = re.sub(r"[^a-z0-9]+", "_", str(d.get("name", "")).lower()).strip("_") or d["dev_id"].lower()
        if slug_ in used:
            slug_ += "_" + d["dev_id"].lower()
        used.add(slug_)
        lines += ["", "[printers.%s]" % slug_, "serial = %s" % q(d["dev_id"])]
    acc = accounts.get(id)
    if not acc:
        os.makedirs(os.path.join(ACCOUNTS, id), exist_ok=True)
        acc = Account(id, email)
    write_private(acc.printers, json.dumps(devices))
    write_private(acc.creds, "\n".join(lines) + "\n")
    pending.pop(email.lower(), None)
    if id in accounts:
        acc.daemon.restart()
    else:
        accounts[id] = acc
        acc.daemon = Daemon(acc)


def login_start(email, password):
    with lock:
        if not slug(email):
            raise ValueError("That is not an email address.")
        return advance(email, cloud("POST", "/v1/user-service/user/login",
                                    {"account": email, "password": password, "apiError": ""}))


def login_code(code, email):
    with lock:
        key = email.lower() if email else next(iter(pending)) if len(pending) == 1 else None
        p = pending.get(key)
        if not p:
            raise ValueError("No pending login. Start it first, and send the email when several are pending.")
        if p["kind"] == "tfa":
            body, path = {"tfaKey": p["tfaKey"], "tfaCode": code}, "/v1/user-service/user/tfa"
        else:
            body, path = {"account": p["email"], "code": code}, "/v1/user-service/user/login"
        return p["email"], advance(p["email"], cloud("POST", path, body))


# ---- API ----

def private(device):
    return {k: v for k, v in device.items() if "access_code" not in k}  # LAN access code, not for clients


def owner(serial):
    with lock:
        return next((a for a in accounts.values() if any(d.get("dev_id") == serial for d in a.devices())), None)


def proxy(h, prefix, serial):
    acc = owner(serial)
    if not acc:
        return h.send(404, {"error": "unknown printer"})
    try:
        status, raw, ctype = call("GET", acc.url(prefix + serial))
    except Upstream:
        return h.send(503, {"error": "daemon not reachable"})
    h.send(status, raw, ctype)


def camera(h, kind, serial):
    acc = owner(serial)
    if not acc:
        return h.send(404, {"error": "unknown printer"})
    if kind == "snapshot":
        with live_lock:
            live = lives.get(serial)
        # While a live upstream is open the bridge answers 409 busy to a snapshot, so never ask it.
        if live and live.ready.wait(90) and not live.error and not live.closed:
            jpeg = live.frame_jpeg()
            if jpeg:
                return h.send(200, jpeg, "image/jpeg")
            return h.send(502, {"error": "could not decode keyframe", "stage": "decode"})
    # The printer's ipcam does not always say which remote transport it uses, so callers can force one.
    proto = parse_qs(urlsplit(h.path).query).get("protocols", [""])[0]
    qs = "?protocols=" + proto if re.fullmatch(r"[a-z,]{1,40}", proto) else ""
    # Minting plus the first keyframe under qemu can take the bridge's full 45 s + 15 s budget.
    try:
        status, raw, ctype = call("GET", acc.url("/camera/%s/%s%s" % (serial, kind, qs)), timeout=90)
    except Upstream:
        return h.send(503, {"error": "daemon not reachable"})
    if status == 502 and b"waiting for camera URL callback" in raw:
        # Seen twice on the H2S: after this timeout the plugin stops delivering MQTT status until restarted.
        log("[%s] camera URL callback timed out, restarting daemon" % acc.id)
        acc.daemon.restart()
    if status == 200 and ctype.startswith("video/h264"):
        jpeg = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "h264", "-i", "-", "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-"],
                              input=raw, capture_output=True, timeout=60)
        if jpeg.returncode or not jpeg.stdout:
            return h.send(502, {"error": "could not decode keyframe", "stage": "decode", "detail": jpeg.stderr.decode(errors="replace")[-300:]})
        return h.send(200, jpeg.stdout, "image/jpeg")
    h.send(status, raw, ctype)


# ---- live camera ----

TS_PACKET = 188
PAT_PID, PMT_PID = 0, 0x1000  # ffmpeg's mpegts muxer puts the PMT here


def ffmpeg_jpeg(args, data):
    """First decoded frame of a stream on stdin, as JPEG; None if it did not decode."""
    out = subprocess.run(["ffmpeg", "-loglevel", "error"] + args + ["-i", "-", "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-"],
                         input=data, capture_output=True, timeout=60)
    return out.stdout if out.stdout and not out.returncode else None


class Live:
    """One upstream connection to the daemon for one printer, fanned out to any number of HTTP clients."""

    def __init__(self, acc, serial, qs):
        self.acc, self.serial, self.qs = acc, serial, qs
        self.lock = threading.Lock()
        self.clients = set()
        self.closed = False
        self.ready = threading.Event()  # set on the first frame, or on failure
        self.error = None  # (status, body, content type) when the daemon refused
        self.ctype = self.out_type = None  # what the daemon sends, and what our clients get
        self.jpeg = None  # JPEG mode: newest part, whole
        self.gop = []  # H.264 mode: TS chunks since the last keyframe, so a joiner decodes at once
        self.psi = {}  # H.264 mode: newest PAT and PMT packet
        self.idle_timer = None
        self.sock = self.proc = None
        threading.Thread(target=self.run, daemon=True).start()

    # -- upstream --

    def run(self):
        try:
            conn = http.client.HTTPConnection(env("DW_DAEMON_HOST", "127.0.0.1"), self.acc.port, timeout=90)  # also ends a stream that stalls
            conn.connect()
            self.sock = conn.sock  # http.client drops its own handle once the response starts
            conn.request("GET", "/camera/%s/live%s" % (self.serial, self.qs))
            resp = conn.getresponse()
            self.ctype = resp.getheader("Content-Type", "")
            if resp.status != 200:
                self.error = (resp.status, resp.read(), self.ctype)
                if resp.status == 502 and b"waiting for camera URL callback" in self.error[1]:
                    # Seen twice on the H2S: after this timeout the plugin stops delivering MQTT status until restarted.
                    log("[%s] camera URL callback timed out, restarting daemon" % self.acc.id)
                    self.acc.daemon.restart()
            else:
                if self.ctype.startswith("video/h264"):
                    self.out_type = "video/mp2t"
                    self.run_h264(resp)
                elif self.ctype.startswith("multipart/x-mixed-replace"):
                    self.out_type = self.ctype
                    self.run_jpeg(resp)
                else:
                    self.error = (502, b'{"error": "unexpected live stream type"}', "application/json")
        except (OSError, http.client.HTTPException, ValueError) as e:
            if not self.ready.is_set():
                self.error = (503, json.dumps({"error": "daemon not reachable: " + type(e).__name__}).encode(), "application/json")
        finally:
            self.close()

    def run_h264(self, resp):
        try:
            rate = float(json.loads(resp.getheader("x-stream-info", "{}")).get("frame_rate"))
        except (TypeError, ValueError):
            rate = 0
        # The raw stream has no timestamps: stamp frames at the advertised rate (15 if unknown).
        # Short probing keeps the start fast. Stream copy needs a camera feed without B-frames.
        self.proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-probesize", "32", "-analyzeduration", "0",
             "-framerate", str(rate if 1 <= rate <= 60 else 15), "-f", "h264", "-i", "-",
             "-c", "copy", "-flush_packets", "1", "-f", "mpegts", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        threading.Thread(target=self.read_ts, daemon=True).start()
        try:
            while True:
                data = resp.read1(65536)
                if not data:
                    break
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass  # ffmpeg or the stream is gone; read_ts and close() finish up
        finally:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def read_ts(self):
        rest = b""
        fd = self.proc.stdout.fileno()
        while True:
            data = os.read(fd, 65536)
            if not data:
                return self.close()
            data = rest + data
            n = len(data) // TS_PACKET * TS_PACKET
            chunk, rest = data[:n], data[n:]
            if not chunk:
                continue
            key = None
            for off in range(0, n, TS_PACKET):
                p = chunk[off:off + TS_PACKET]
                pid = (p[1] & 0x1F) << 8 | p[2]
                if pid in (PAT_PID, PMT_PID):
                    self.psi[pid] = p
                elif p[1] & 0x40 and p[3] & 0x20 and p[4] and p[5] & 0x40:  # payload start with random access flag: a keyframe
                    key = off
            with self.lock:
                if key is not None:
                    self.gop = [chunk[key:]]
                elif self.gop:
                    self.gop.append(chunk)
                self.broadcast(chunk)
            if key is not None:
                self.ready.set()

    def run_jpeg(self, resp):
        while True:
            part = resp.readline()
            if not part:
                return
            if not part.startswith(b"--"):
                continue
            head, length = part, 0
            while True:
                line = resp.readline()
                head += line
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1])
                if line in (b"\r\n", b"\n", b""):
                    break
            body = resp.read(length)
            if len(body) < length:
                return
            tail = resp.readline()  # the CRLF after the body
            with self.lock:
                self.jpeg = body
                self.broadcast(head + body + tail)
            self.ready.set()

    def broadcast(self, data):
        for c in list(self.clients):
            try:
                c.put_nowait(data)
            except queue.Full:
                self.drop(c)  # a client that cannot keep up must not hold memory

    def drop(self, c):
        self.clients.discard(c)
        while not c.empty():
            c.get_nowait()
        c.put_nowait(None)

    # -- clients --

    def join(self):
        """A queue of byte chunks for one client; None ends it. Starts at a keyframe or part boundary."""
        c = queue.Queue(maxsize=2048)
        with self.lock:
            if self.closed:
                c.put(None)
                return c
            if self.idle_timer:
                self.idle_timer.cancel()
                self.idle_timer = None
            if self.jpeg is not None:
                c.put(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(self.jpeg) + self.jpeg + b"\r\n")
            else:
                for chunk in list(self.psi.values()) + self.gop:
                    c.put_nowait(chunk)
            self.clients.add(c)
        return c

    def leave(self, c):
        with self.lock:
            self.clients.discard(c)
            if not self.clients and not self.closed:
                self.idle_timer = threading.Timer(LIVE_IDLE, self.close)
                self.idle_timer.daemon = True
                self.idle_timer.start()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.idle_timer:
                self.idle_timer.cancel()
            clients, self.clients = list(self.clients), set()
        with live_lock:
            if lives.get(self.serial) is self:
                del lives[self.serial]
        for c in clients:
            try:
                c.put_nowait(None)
            except queue.Full:
                pass
        if not self.ready.is_set() and not self.error:
            self.error = (502, b'{"error": "stream ended before the first frame"}', "application/json")
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)  # wakes the blocked read; the daemon then closes the cloud tunnel
            except OSError:
                pass
            self.sock.close()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.ready.set()

    # -- snapshot --

    def frame_jpeg(self):
        """The newest frame as JPEG (H.264: the last keyframe, whatever its age); None if there is none or it did not decode."""
        with self.lock:
            if self.closed:
                return None
            jpeg, data = self.jpeg, b"".join(list(self.psi.values()) + self.gop)
        if jpeg is not None:
            return jpeg
        return ffmpeg_jpeg(["-f", "mpegts"], data) if data else None


lives = {}  # serial -> Live
live_lock = threading.Lock()


def get_live(h, serial):
    acc = owner(serial)
    if not acc:
        return h.send(404, {"error": "unknown printer"})
    proto = parse_qs(urlsplit(h.path).query).get("protocols", [""])[0]
    qs = "?protocols=" + proto if re.fullmatch(r"[a-z,]{1,40}", proto) else ""
    with live_lock:
        live = lives.get(serial)
        if not live:
            live = lives[serial] = Live(acc, serial, qs)
        q = live.join()
    # Same budget as a snapshot: minting plus the first keyframe can take 60 s under qemu.
    if not live.ready.wait(100):
        live.close()
        return h.send(504, {"error": "no frame from the camera"})
    if live.error:
        return h.send(*live.error)
    h.send_response(200)  # HTTP/1.0: the body ends when we close
    h.send_header("Content-Type", live.out_type)
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.close_connection = True
    try:
        while True:
            chunk = q.get()
            if chunk is None:
                break
            h.wfile.write(chunk)
            h.wfile.flush()
    except OSError:
        pass  # the viewer left
    finally:
        live.leave(q)


def daemon_json(acc, path, timeout):
    try:
        status, raw, _ = call("GET", acc.url(path), timeout=timeout)
        return json.loads(raw) if status == 200 else None
    except (Upstream, ValueError):
        return None


def get_health(h, m, body):
    with lock:
        accs = list(accounts.values())
    h.send(200, {"server": "ok", "plugin": plugin.status() if plugin else {"state": "ready"}, "accounts": [
        {"id": a.id, "email": a.email, "logged_in": True,
         "daemon": {"running": a.daemon.running(), "restarts": a.daemon.restarts,
                    "health": daemon_json(a, "/health", 3)}} for a in accs]})


def get_printers(h, m, body):
    with lock:
        accs = list(accounts.values())
    out = []
    for a in accs:
        answer = daemon_json(a, "/printers", 5)
        try:
            live = {p.get("serial"): p for p in answer.get("printers", [])}
        except AttributeError:
            live = {}
        out += [dict(private(d), serial=d["dev_id"], account=a.id, email=a.email, live=live.get(d["dev_id"]))
                for d in a.devices()]
    h.send(200, out)


def post_login_start(h, m, body):
    if not isinstance(body, dict) or not all(isinstance(body.get(k), str) and body[k] for k in ("email", "password")):
        return h.send(400, {"error": "Body must be JSON with email and password."})
    h.send(200, {"state": login_start(body["email"], body["password"]), "account": slug(body["email"])})


def post_login_code(h, m, body):
    if not isinstance(body, dict) or not isinstance(body.get("code"), str) or not body["code"]:
        return h.send(400, {"error": "Body must be JSON with a code."})
    if not isinstance(body.get("email", ""), str):
        return h.send(400, {"error": "email must be a string."})
    email, state = login_code(body["code"], body.get("email"))
    h.send(200, {"state": state, "account": slug(email)})


def delete_account(h, m, body):
    with lock:
        acc = accounts.pop(m.group(1), None)
        if not acc:
            return h.send(404, {"error": "unknown account"})
        pending.pop(acc.email.lower(), None)
        acc.daemon.stop()
        shutil.rmtree(acc.dir, ignore_errors=True)
    h.send(200, {"deleted": acc.id})


def multipart(fields):
    boundary = secrets.token_hex(16)
    out = b""
    for name, filename, ctype, value in fields:
        disp = 'form-data; name="%s"' % name + ('; filename="%s"' % filename if filename else "")
        out += ("--%s\r\nContent-Disposition: %s\r\nContent-Type: %s\r\n\r\n" % (boundary, disp, ctype)).encode()
        out += value + b"\r\n"
    return out + ("--%s--\r\n" % boundary).encode(), "multipart/form-data; boundary=" + boundary


def post_print(h, m, body):
    if not isinstance(body, dict):
        return h.send(400, {"error": "Body must be a JSON object."})
    serial, filename = body.get("serial"), body.get("filename")
    if not (isinstance(serial, str) and SERIAL.fullmatch(serial) and isinstance(filename, str) and filename):
        return h.send(400, {"error": "serial and filename are required."})
    try:
        raw = base64.b64decode(body.get("file_b64") or "", validate=True)
    except (binascii.Error, ValueError, TypeError):
        return h.send(400, {"error": "file_b64 is not valid base64."})
    if not raw:
        return h.send(400, {"error": "file_b64 is empty."})
    acc = owner(serial)
    if not acc:
        return h.send(404, {"error": "unknown printer"})
    filename = os.path.basename(filename)
    params = {"device_id": serial, "filename": filename, "project_name": re.sub(r"(\.gcode)?\.3mf$", "", filename),
              "bed_leveling": body.get("bed_leveling", True), "flow_cali": True, "vibration_cali": True,
              "timelapse": body.get("timelapse", False), "use_ams": body.get("use_ams", True)}
    data, ctype = multipart([("file", filename, "application/octet-stream", raw),
                             ("params", None, "application/json", json.dumps(params).encode())])
    try:
        status, answer, actype = call("POST", acc.url("/print"), {"Content-Type": ctype}, data, timeout=300)
    except Upstream:
        return h.send(503, {"error": "daemon not reachable"})
    h.send(status, answer, actype)


def send_command(h, acc, serial, payloads):
    for payload in payloads:
        try:
            status, raw, ctype = call("POST", acc.url("/command/" + serial), {"Content-Type": "application/json"},
                                      json.dumps(payload).encode(), timeout=30)
        except Upstream:
            return h.send(503, {"error": "daemon not reachable"})
        if status != 200:
            return h.send(status, raw, ctype)
    h.send(200, {"sent": len(payloads)})


def post_light(h, m, body):
    serial, acc = m.group(1), owner(m.group(1))
    if not acc:
        return h.send(404, {"error": "unknown printer"})
    if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
        return h.send(400, {"error": "Body must be {\"on\": true|false}."})
    # Same payload as the vendor slicer's DevLamp; chamber_light2 is the H2 series' second light, others ignore it.
    payloads = [{"system": {"command": "ledctrl", "led_node": node, "sequence_id": "0",
                            "led_mode": "on" if body["on"] else "off", "led_on_time": 500,
                            "led_off_time": 500, "loop_times": 1, "interval_time": 1000}}
                for node in ("chamber_light", "chamber_light2")]
    send_command(h, acc, serial, payloads)


def post_rfid(h, m, body):
    serial, acc = m.group(1), owner(m.group(1))
    if not acc:
        return h.send(404, {"error": "unknown printer"})
    ams, slot = (body or {}).get("ams_id"), (body or {}).get("slot_id")
    if not (isinstance(ams, int) and 0 <= ams < 8 and isinstance(slot, int) and 0 <= slot < 4):
        return h.send(400, {"error": "Body must be {\"ams_id\": 0-7, \"slot_id\": 0-3}."})
    try:
        status, raw, _ = call("GET", acc.url("/status/" + serial), timeout=20)
        p = json.loads(raw).get("print", {}) if status == 200 else {}
    except (Upstream, ValueError):
        p = {}
    # The vendor slicer's check_enable_np: printers on the new protocol (H2 series) take ams_get_rfid,
    # the others an M620 R<tray> gcode line.
    if all(k in p for k in ("cfg", "fun", "aux", "stat")):
        payload = {"print": {"command": "ams_get_rfid", "sequence_id": "0", "ams_id": ams, "slot_id": slot, "mode": 0}}
    else:
        payload = {"print": {"command": "gcode_line", "sequence_id": "0", "param": "M620 R%d \n" % (ams * 4 + slot)}}
    send_command(h, acc, serial, [payload])


def post_pause(h, m, body):
    serial, acc = m.group(1), owner(m.group(1))
    if not acc:
        return h.send(404, {"error": "unknown printer"})
    # Same payload as the vendor slicer's command_task_pause and the vendor HA integration's PAUSE (which the firmware
    # ignores unsigned, hence this route).
    send_command(h, acc, serial, [{"print": {"command": "pause", "sequence_id": "0", "param": ""}}])


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Druckwerk bridge</title>
<style>body{font:16px system-ui,sans-serif;max-width:36rem;margin:2rem auto;padding:0 1rem}
input{width:100%;box-sizing:border-box;padding:.5rem;margin:.5rem 0}button{padding:.5rem 1rem}
li{margin:.5rem 0}</style></head>
<body><h1>Druckwerk bridge</h1>
<h2>Accounts</h2>
<ul id="accounts"></ul>
<h2>Add account</h2>
<form id="login"><input id="email" type="email" placeholder="Account email" autocomplete="username" required>
<input id="password" type="password" placeholder="Password" autocomplete="current-password" required>
<button>Log in</button></form>
<form id="codeform" hidden><label id="codelabel"></label><input id="code" autocomplete="one-time-code" required>
<button>Send code</button></form>
<p id="message"></p>
<script>
const $ = id => document.getElementById(id);
let pendingEmail = null;
async function call(path, method, body) {
  const r = await fetch(path, {method, body: body && JSON.stringify(body)});
  return [r.ok, await r.json()];
}
async function refresh() {
  const [, h] = await call("api/health", "GET");
  const [, p] = await call("api/printers", "GET");
  const ul = $("accounts");
  ul.replaceChildren();
  if (!h.accounts.length) ul.textContent = "No accounts yet.";
  for (const a of h.accounts) {  // textContent only: emails and printer names are not trusted HTML
    const names = p.filter(x => x.account === a.id).map(x => x.name || x.serial).join(", ") || "no printers";
    const li = document.createElement("li");
    li.textContent = a.email + ", printers: " + names + ", daemon " + (a.daemon.running ? "running" : "stopped") + " ";
    const b = document.createElement("button");
    b.textContent = "Remove";
    b.onclick = async () => { if (confirm("Remove " + a.email + "?")) handle(await call("api/accounts/" + a.id, "DELETE")); };
    li.append(b);
    ul.append(li);
  }
}
function handle([ok, d]) {
  $("message").textContent = ok ? "" : d.error;
  if (ok && d.state) {
    $("codeform").hidden = d.state === "done";
    $("codelabel").textContent = d.state === "need_tfa" ? "Two-factor code" : "Code from the email the cloud sent you";
    $("code").value = "";
  }
  refresh();
}
$("login").onsubmit = async e => { e.preventDefault();
  pendingEmail = $("email").value;
  handle(await call("api/login/start", "POST", {email: pendingEmail, password: $("password").value}));
  $("password").value = ""; };
$("codeform").onsubmit = async e => { e.preventDefault();
  handle(await call("api/login/code", "POST", {code: $("code").value, email: pendingEmail})); };
refresh();
</script></body></html>
"""


def get_page(h, m, body):
    h.send(200, PAGE, "text/html; charset=utf-8")


SER = r"([A-Za-z0-9_-]{1,64})"
# (method, regex) -> (handler, max body bytes)
ROUTES = [
    ("GET", r"/", get_page, 0),
    ("GET", r"/api/health", get_health, 0),
    ("GET", r"/api/printers", get_printers, 0),
    ("GET", r"/api/status/" + SER, lambda h, m, b: proxy(h, "/status/", m.group(1)), 0),
    ("GET", r"/api/ams/" + SER, lambda h, m, b: proxy(h, "/ams/", m.group(1)), 0),
    ("GET", r"/api/camera/" + SER + r"/url", lambda h, m, b: camera(h, "url", m.group(1)), 0),
    ("GET", r"/api/camera/" + SER + r"/live", lambda h, m, b: get_live(h, m.group(1)), 0),
    ("GET", r"/api/camera/" + SER + r"/snapshot", lambda h, m, b: camera(h, "snapshot", m.group(1)), 0),
    ("POST", r"/api/login/start", post_login_start, MAX_SMALL_BODY),
    ("POST", r"/api/login/code", post_login_code, MAX_SMALL_BODY),
    ("DELETE", r"/api/accounts/([a-z0-9_]{1,64})", delete_account, 0),
    ("POST", r"/api/print", post_print, MAX_PRINT_BODY),
    ("POST", r"/api/light/" + SER, post_light, MAX_SMALL_BODY),
    ("POST", r"/api/rfid/" + SER, post_rfid, MAX_SMALL_BODY),
    ("POST", r"/api/pause/" + SER, post_pause, MAX_SMALL_BODY),
]


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self, method):
        path = urlsplit(self.path).path
        for m, regex, fn, limit in ROUTES:
            match = m == method and re.fullmatch(regex, path)
            if not match:
                continue
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                self.close_connection = True  # the body stays unread
                return self.send(413, {"error": "Body too large (limit %d bytes)." % limit})
            try:
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    return self.send(400, {"error": "Body is not valid JSON."})
                return fn(self, match, body)
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            except Upstream as e:
                return self.send(502, {"error": str(e)})
        self.send(404, {"error": "not found"})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def log_message(self, fmt, *args):
        log("%s %s" % (self.command, urlsplit(self.path).path))  # path only, no query


def main():
    global plugin
    if env("DW_PLUGIN_LINK_DIR"):
        import plugin as plugin_mod
        plugin = plugin_mod.from_env()
    os.makedirs(ACCOUNTS, exist_ok=True)
    load_accounts()
    server = ThreadingHTTPServer((env("DW_HOST", "0.0.0.0"), int(env("DW_PORT", "8098"))), Handler)
    server.daemon_threads = True
    log("listening on port %d" % server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
