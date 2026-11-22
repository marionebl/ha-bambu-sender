"""End-to-end tests: server.py as a subprocess against the mock cloud and mock daemon.
SERVER_PY overrides the file under test (used to prove the tests can fail)."""
import base64
import http.client
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mock_cloud  # noqa: E402
import mock_daemon  # noqa: E402

MOCK = "%s -I %s" % (sys.executable, os.path.join(HERE, "mock_daemon.py"))
SERVER_PY = os.environ.get("SERVER_PY", os.path.join(HERE, "..", "addon", "server.py"))


def read(path):
    with open(path) as f:
        return f.read()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def free_range(n=4):
    """A port whose next n-1 neighbours are free too, since daemons take consecutive ports."""
    while True:
        base, socks = free_port(), []
        try:
            for p in range(base, base + n):
                socks.append(socket.socket())
                socks[-1].bind(("127.0.0.1", p))
            return base
        except OSError:
            pass
        finally:
            for k in socks:
                k.close()


class Server:
    """One server.py process with its own data dir and log file."""

    def __init__(self, cloud, daemon_cmd=MOCK, setup=None, extra_env=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        self.cert = os.path.join(self.tmp.name, "slicer_base64.cer")
        with open(self.cert, "w") as f:
            f.write("CERT")
        if setup:
            setup(self.data)
        self.port = free_port()
        self.base = free_range()  # first daemon port; the next ones are base+1, ...
        self.logpath = os.path.join(self.tmp.name, "server.log")
        self.bodies = []  # every HTTP response body we received
        env = dict(os.environ, BAMBU_DATA=self.data, BAMBU_PORT=str(self.port), BAMBU_HOST="127.0.0.1",
                   BAMBU_API_BASE="http://127.0.0.1:%d" % cloud.server_port, BAMBU_DAEMON_PORT_BASE=str(self.base),
                   BAMBU_CERT=self.cert, BAMBU_DAEMON_CMD=daemon_cmd, BAMBU_BACKOFF_MIN="0.2", BAMBU_BACKOFF_MAX="0.8", **(extra_env or {}))
        self.log = open(self.logpath, "w")
        self.proc = subprocess.Popen([sys.executable, "-I", SERVER_PY], env=env, stdout=self.log, stderr=self.log,
                                     start_new_session=True)  # so close() can kill the daemons too
        for _ in range(50):
            try:
                self.req("GET", "/api/health")
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def req(self, method, path, body=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request(method, path, raw if raw is not None else (json.dumps(body) if body is not None else None))
        r = c.getresponse()
        data = r.read()
        c.close()
        self.bodies.append(data)
        return r.status, data

    def json(self, method, path, body=None):
        status, data = self.req(method, path, body)
        return status, json.loads(data)

    def acct(self, id, name=""):
        return os.path.join(self.data, "accounts", id, name)

    def daemon(self, port, path):
        """Ask a mock daemon directly, not through the server."""
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", path)
        data = json.loads(c.getresponse().read())
        c.close()
        return data

    def logtext(self):
        self.log.flush()
        with open(self.logpath) as f:
            return f.read()

    def close(self):
        os.killpg(self.proc.pid, signal.SIGKILL)  # the server and the daemons it started
        self.proc.wait(10)
        self.log.close()
        self.tmp.cleanup()


def wait_for(fn, seconds=10):
    for _ in range(int(seconds * 10)):
        try:
            value = fn()
            if value:
                return value
        except OSError:
            pass
        time.sleep(0.1)
    return fn()


def daemon_up(s, port):
    return wait_for(lambda: s.daemon(port, "/health"))


def login(s, email, code=True):
    st, d = s.json("POST", "/api/login/start", {"email": email, "password": "pw"})
    if code and d.get("state") == "need_code":
        st, d = s.json("POST", "/api/login/code", {"code": "123456", "email": email})
    return d


def latest_tokens(cloud):
    return [t for _, t, r in cloud.issued] + [r for _, t, r in cloud.issued]


class FlowTest(unittest.TestCase):
    """Tests share one server and run in order: login first."""

    @classmethod
    def setUpClass(cls):
        cls.cloud = mock_cloud.start()
        cls.s = Server(cls.cloud)

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def test_01_not_logged_in_at_start(self):
        self.assertEqual(self.s.json("GET", "/api/health")[1], {"server": "ok", "accounts": []})  # ADAPTED

    def test_02_login_with_code_writes_credentials(self):
        st, d = self.s.json("POST", "/api/login/start", {"email": "a@example.com", "password": "pw"})
        self.assertEqual((st, d), (200, {"state": "need_code", "account": "a_example_com"}))  # ADAPTED
        self.assertIn("/v1/user-service/user/sendemail/code", [c[0] for c in self.cloud.calls])
        st, d = self.s.json("POST", "/api/login/code", {"code": "000000"})
        self.assertNotEqual(st, 200)  # a wrong code does not log in
        st, d = self.s.json("POST", "/api/login/code", {"code": "123456"})
        self.assertEqual((st, d), (200, {"state": "done", "account": "a_example_com"}))  # ADAPTED
        with open(self.s.acct("a_example_com", "credentials.toml"), "rb") as f:
            t = tomllib.load(f)
        token, refresh = self.cloud.tokens["a@example.com"]
        self.assertEqual(t["cloud"], {"token": token, "refresh_token": refresh, "uid": "4242", "email": "a@example.com"})
        self.assertEqual([p["serial"] for p in t["printers"].values()], ["SERIAL001", "SERIAL002"])
        self.assertEqual(len(t["printers"]), 2)  # same name twice still gives two sections
        for name in ("credentials.toml", "printers.json"):
            self.assertEqual(stat.S_IMODE(os.stat(self.s.acct("a_example_com", name)).st_mode), 0o600, name)
        self.assertEqual({c[2] for c in self.cloud.calls}, {"OrcaSlicer"})

    def test_03_health_logged_in_and_daemon_started(self):
        daemon_up(self.s, self.s.base)
        _, h = self.s.json("GET", "/api/health")
        (a,) = h["accounts"]  # ADAPTED
        self.assertEqual((a["id"], a["email"], a["logged_in"]), ("a_example_com", "a@example.com", True))
        self.assertTrue(a["daemon"]["running"])
        self.assertEqual(a["daemon"]["health"]["status"], "ok")
        self.assertEqual(a["daemon"]["health"]["credentials"], self.s.acct("a_example_com", "credentials.toml"))

    def test_04_printers_merged(self):
        _, p = self.s.json("GET", "/api/printers")
        self.assertEqual([x["serial"] for x in p], ["SERIAL001", "SERIAL002"])
        self.assertEqual(p[0]["dev_product_name"], "P1S")
        self.assertEqual((p[0]["account"], p[0]["email"]), ("a_example_com", "a@example.com"))  # ADAPTED
        self.assertEqual(p[0]["live"]["gcode_state"], "IDLE")
        self.assertIsNone(p[1]["live"])
        self.assertNotIn("ACCESS-1234", json.dumps(p))

    def test_05_status_and_ams_proxy(self):
        st, a = self.s.json("GET", "/api/ams/SERIAL001")
        self.assertEqual(st, 200)
        self.assertEqual(a["ams"], mock_daemon.AMS["ams"])  # ADAPTED: the mock adds served_by
        _, s = self.s.json("GET", "/api/status/SERIAL001")
        self.assertEqual(s["print"]["nozzle_temper"], 21.5)
        self.assertEqual(self.s.req("GET", "/api/ams/NOPE")[0], 404)

    def test_05b_camera_protocols_pass_through_and_mint_timeout_restarts_daemon(self):
        _, u = self.s.json("GET", "/api/camera/SERIAL001/url?protocols=agora")
        self.assertEqual(u["path"], "/camera/SERIAL001/url?protocols=agora")
        _, u = self.s.json("GET", "/api/camera/SERIAL001/url?protocols=x%22y")
        self.assertEqual(u["path"], "/camera/SERIAL001/url")  # bad value dropped
        pid = lambda: self.s.json("GET", "/api/health")[1]["accounts"][0]["daemon"]["health"]["pid"]
        before = pid()
        st, body = self.s.json("GET", "/api/camera/SERIAL001/snapshot")
        self.assertEqual(st, 502)
        self.assertIn("camera URL callback", body["error"])
        for _ in range(50):
            time.sleep(0.1)
            try:
                if pid() not in (before, None):
                    break
            except (KeyError, TypeError):
                pass
        self.assertNotEqual(pid(), before)

    def test_05c_light_and_rfid_commands(self):
        st, _ = self.s.json("POST", "/api/light/SERIAL001", {"on": True})
        self.assertEqual(st, 200)
        st, _ = self.s.json("POST", "/api/rfid/SERIAL001", {"ams_id": 1, "slot_id": 2})
        self.assertEqual(st, 200)
        cmds = self.s.daemon(self.s.base, "/commands")
        self.assertEqual([c["system"]["led_node"] for c in cmds[:2]], ["chamber_light", "chamber_light2"])
        self.assertEqual(cmds[0]["system"]["led_mode"], "on")
        self.assertEqual(cmds[2]["print"], {"command": "gcode_line", "sequence_id": "0", "param": "M620 R6 \n"})
        self.assertEqual(self.s.json("POST", "/api/light/SERIAL001", {"on": "yes"})[0], 400)
        self.assertEqual(self.s.json("POST", "/api/rfid/SERIAL001", {"ams_id": 0, "slot_id": 4})[0], 400)
        self.assertEqual(self.s.json("POST", "/api/light/NOPE", {"on": True})[0], 404)
        self.assertEqual(len(self.s.daemon(self.s.base, "/commands")), 3)  # rejected requests sent nothing

    def test_06_print_delivers_exact_bytes(self):
        blob = bytes(range(256)) * 40 + b"\r\n--boundary\r\n\x00\xff" + os.urandom(2000)
        st, d = self.s.json("POST", "/api/print", {"serial": "SERIAL001", "filename": "cube.gcode.3mf",
                                                   "file_b64": base64.b64encode(blob).decode(), "timelapse": True})
        self.assertEqual(st, 200)
        self.assertEqual(d["result"], "sent")
        self.assertEqual(d["return_code"], -1)
        got = self.s.daemon(self.s.base, "/prints")[-1]  # ADAPTED: read from the daemon process
        self.assertEqual(base64.b64decode(got["file_b64"]), blob)
        self.assertEqual(got["params"], {
            "device_id": "SERIAL001", "filename": "cube.gcode.3mf", "project_name": "cube", "bed_leveling": True,
            "flow_cali": True, "vibration_cali": True, "timelapse": True, "use_ams": True})

    def test_07_print_error_status_passes_through(self):
        st, d = self.s.json("POST", "/api/print", {"serial": "SERIAL001", "filename": "fail.gcode.3mf",
                                                   "file_b64": base64.b64encode(b"x").decode()})
        self.assertEqual((st, d["result"]), (502, "error"))

    def test_08_print_rejects_bad_input_and_big_bodies(self):
        self.assertEqual(self.s.req("POST", "/api/print", {"serial": "SERIAL001", "filename": "a.3mf",
                                                           "file_b64": "!!notbase64"})[0], 400)
        c = http.client.HTTPConnection("127.0.0.1", self.s.port, timeout=10)
        c.putrequest("POST", "/api/print")
        c.putheader("Content-Length", str(50 * 1024 * 1024 + 1))
        c.endheaders()  # headers only: the server must answer without waiting for the body
        r = c.getresponse()
        self.assertEqual(r.status, 413)
        self.s.bodies.append(r.read())
        c.close()

    def test_09_page_uses_relative_urls(self):
        st, page = self.s.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertNotIn(b'fetch("/', page)
        self.assertIn(b'"api/health"', page)
        self.assertIn(b'"api/accounts/"', page)

    def test_99_token_never_leaks(self):
        for secret in latest_tokens(self.cloud):
            for body in self.s.bodies:
                self.assertNotIn(secret.encode(), body)
            self.assertNotIn(secret, self.s.logtext())
        self.assertGreater(len(self.s.bodies), 8)


class FlowVariantsTest(unittest.TestCase):
    def test_tfa_and_direct_login(self):
        cloud = mock_cloud.start()
        s = Server(cloud)
        try:
            self.assertEqual(s.json("POST", "/api/login/start", {"email": "tfa@x.org", "password": "pw"})[1],
                             {"state": "need_tfa", "account": "tfa_x_org"})  # ADAPTED
            self.assertEqual(s.json("POST", "/api/login/code", {"code": "654321"})[1],
                             {"state": "done", "account": "tfa_x_org"})  # ADAPTED
            self.assertEqual(s.json("POST", "/api/login/start", {"email": "direct@x.org", "password": "pw"})[1],
                             {"state": "done", "account": "direct_x_org"})  # ADAPTED
            self.assertEqual(s.req("POST", "/api/login/start", {"email": "a@x.org"})[0], 400)
        finally:
            s.close()


class SupervisorTest(unittest.TestCase):
    def test_restarts_exiting_daemon_and_masks_token_in_its_output(self):
        cloud = mock_cloud.start()
        marker = tempfile.NamedTemporaryFile(delete=False).name
        # A "daemon" that records each start, prints the credentials (token included), then fails.
        script = 'echo started >> %s; echo daemon-says-hi; cat "$1"; exit 1' % marker  # ADAPTED: path is an argument
        s = Server(cloud, "sh -c '%s' sh" % script)
        try:
            time.sleep(0.6)
            self.assertEqual(read(marker), "")  # not started before login
            s.json("POST", "/api/login/start", {"email": "direct@x.org", "password": "pw"})
            for _ in range(60):
                if read(marker).count("started") >= 3:
                    break
                time.sleep(0.1)
            self.assertGreaterEqual(read(marker).count("started"), 3)
            self.assertGreaterEqual(s.json("GET", "/api/health")[1]["accounts"][0]["daemon"]["restarts"], 2)  # ADAPTED
            log = s.logtext()
            self.assertIn("daemon-says-hi", log)
            self.assertIn("restarting in", log)
            self.assertIn("uid = ", log)  # the daemon output reached the log ...
            for secret in latest_tokens(cloud):
                self.assertNotIn(secret, log)  # ... with the token masked
        finally:
            s.close()
            os.unlink(marker)

    def test_fatal_line_kills_a_hanging_daemon(self):
        cloud = mock_cloud.start()
        marker = tempfile.NamedTemporaryFile(delete=False).name
        s = Server(cloud, "sh -c 'echo started >> %s; echo \"terminate called x\"; exec sleep 600' sh" % marker)
        try:
            s.json("POST", "/api/login/start", {"email": "direct@x.org", "password": "pw"})
            self.assertTrue(wait_for(lambda: read(marker).count("started") >= 2))  # killed and restarted
        finally:
            s.close()
            os.unlink(marker)


class MultiAccountTest(unittest.TestCase):
    """Two accounts on one server, in order."""

    @classmethod
    def setUpClass(cls):
        cls.cloud = mock_cloud.start()
        cls.s = Server(cls.cloud)

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def test_01_two_logins_two_daemons_on_two_ports(self):
        s = self.s
        self.assertEqual(login(s, "a@example.com"), {"state": "done", "account": "a_example_com"})
        self.assertEqual(login(s, "b@example.com"), {"state": "done", "account": "b_example_com"})
        a, b = daemon_up(s, s.base), daemon_up(s, s.base + 1)
        for acc, h, port in (("a_example_com", a, s.base), ("b_example_com", b, s.base + 1)):
            self.assertEqual(h["credentials"], s.acct(acc, "credentials.toml"))
            self.assertEqual(h["home"], s.acct(acc, "home"))
            with open(s.acct(acc, "port")) as f:
                self.assertEqual(int(f.read()), port)
            with open(os.path.join(h["home"], ".local/share/bambox/cert/slicer_base64.cer")) as f:
                self.assertEqual(f.read(), "CERT")
        self.assertNotEqual(a["pid"], b["pid"])
        self.assertNotEqual(a["home"], b["home"])
        _, h = s.json("GET", "/api/health")
        self.assertEqual([x["id"] for x in h["accounts"]], ["a_example_com", "b_example_com"])
        self.assertTrue(all(x["daemon"]["running"] and x["daemon"]["health"] for x in h["accounts"]))

    def test_02_routing_by_serial(self):
        s = self.s
        _, p = s.json("GET", "/api/printers")
        self.assertEqual([(x["serial"], x["account"], x["email"]) for x in p], [
            ("SERIAL001", "a_example_com", "a@example.com"), ("SERIAL002", "a_example_com", "a@example.com"),
            ("SERIALB01", "b_example_com", "b@example.com")])
        self.assertEqual(p[2]["live"]["serial"], "SERIALB01")
        self.assertNotIn("access_code", json.dumps(p))
        creds_b = s.acct("b_example_com", "credentials.toml")
        self.assertEqual(s.json("GET", "/api/status/SERIALB01")[1]["served_by"], creds_b)
        self.assertEqual(s.json("GET", "/api/ams/SERIALB01")[1]["served_by"], creds_b)
        self.assertEqual(s.json("GET", "/api/status/SERIAL001")[1]["served_by"], s.acct("a_example_com", "credentials.toml"))
        blob = os.urandom(3000) + b"\r\n--x\r\n\x00"
        st, d = s.json("POST", "/api/print", {"serial": "SERIALB01", "filename": "b.gcode.3mf",
                                              "file_b64": base64.b64encode(blob).decode()})
        self.assertEqual((st, d["device_id"]), (200, "SERIALB01"))
        (got,) = s.daemon(s.base + 1, "/prints")
        self.assertEqual(base64.b64decode(got["file_b64"]), blob)
        self.assertEqual(s.daemon(s.base, "/prints"), [])  # account A's daemon saw nothing
        self.assertEqual(s.json("POST", "/api/rfid/SERIALB01", {"ams_id": 0, "slot_id": 3})[0], 200)
        (cmd,) = s.daemon(s.base + 1, "/commands")
        self.assertEqual(cmd["print"], {"command": "ams_get_rfid", "sequence_id": "0", "ams_id": 0, "slot_id": 3, "mode": 0})
        self.assertEqual(s.req("GET", "/api/status/NOPE")[0], 404)
        self.assertEqual(s.req("POST", "/api/print", {"serial": "NOPE", "filename": "a.3mf", "file_b64": "eA=="})[0], 404)

    def test_03_relogin_replaces_token(self):
        s = self.s
        old = self.cloud.tokens["b@example.com"]
        pid = daemon_up(s, s.base + 1)["pid"]
        self.assertEqual(login(s, "b@example.com")["state"], "done")
        new = self.cloud.tokens["b@example.com"]
        self.assertNotEqual(old, new)
        with open(s.acct("b_example_com", "credentials.toml")) as f:
            text = f.read()
        self.assertIn(new[0], text)
        self.assertNotIn(old[0], text)
        self.assertEqual(len(s.json("GET", "/api/health")[1]["accounts"]), 2)  # replaced, not added
        self.assertTrue(wait_for(lambda: daemon_up(s, s.base + 1)["pid"] != pid))  # daemon restarted on the same port

    def test_04_delete_stops_daemon_and_removes_dir(self):
        s = self.s
        self.assertEqual(s.json("DELETE", "/api/accounts/b_example_com")[1], {"deleted": "b_example_com"})
        self.assertFalse(os.path.exists(s.acct("b_example_com")))
        with self.assertRaises(OSError):
            s.daemon(s.base + 1, "/health")
        self.assertEqual([x["id"] for x in s.json("GET", "/api/health")[1]["accounts"]], ["a_example_com"])
        self.assertEqual(s.req("GET", "/api/status/SERIALB01")[0], 404)
        self.assertEqual(s.req("DELETE", "/api/accounts/b_example_com")[0], 404)
        self.assertTrue(daemon_up(s, s.base))  # account A is untouched
        time.sleep(1.0)  # the supervisor must not bring the deleted daemon back
        with self.assertRaises(OSError):
            s.daemon(s.base + 1, "/health")

    def test_05_login_code_needs_email_when_two_are_pending(self):
        s = self.s
        s.json("POST", "/api/login/start", {"email": "c@example.com", "password": "pw"})
        s.json("POST", "/api/login/start", {"email": "d@example.com", "password": "pw"})
        self.assertEqual(s.req("POST", "/api/login/code", {"code": "123456"})[0], 400)
        self.assertEqual(s.json("POST", "/api/login/code", {"code": "123456", "email": "d@example.com"})[1],
                         {"state": "done", "account": "d_example_com"})

    def test_99_no_token_in_responses_or_log(self):
        for secret in latest_tokens(self.cloud):
            for body in self.s.bodies:
                self.assertNotIn(secret.encode(), body)
            self.assertNotIn(secret, self.s.logtext())


class TwoDaemonsLogTest(unittest.TestCase):
    def test_token_masked_in_log_output_of_both_daemons(self):
        cloud = mock_cloud.start()
        s = Server(cloud, "sh -c 'echo up; cat \"$1\"; sleep 600' sh")
        try:
            login(s, "a@example.com")
            login(s, "b@example.com")
            self.assertTrue(wait_for(lambda: s.logtext().count("uid = ") >= 2))
            log = s.logtext()
            self.assertIn("[a_example_com] daemon: ", log)
            self.assertIn("[b_example_com] daemon: ", log)
            for secret in latest_tokens(cloud):
                self.assertNotIn(secret, log)
        finally:
            s.close()


class MigrationTest(unittest.TestCase):
    OLD_TOKEN = "TOKEN-OLD-V01-SECRET"

    @classmethod
    def v01(cls, data):
        os.makedirs(data)
        with open(os.path.join(data, "credentials.toml"), "w") as f:
            f.write('[cloud]\ntoken = "%s"\nrefresh_token = "REFRESH-OLD-V01"\nuid = "1"\nemail = "Tilman@Example.com"\n'
                    '\n[printers.h2s]\nserial = "H2S-SERIAL"\n' % cls.OLD_TOKEN)
        with open(os.path.join(data, "printers.json"), "w") as f:
            json.dump([{"dev_id": "H2S-SERIAL", "name": "H2S", "dev_access_code": "ACCESS-OLD"}], f)
        os.chmod(os.path.join(data, "credentials.toml"), 0o644)  # v0.1 files are 600; prove we enforce it anyway

    def test_v01_layout_becomes_one_working_account_without_login(self):
        cloud = mock_cloud.start()
        s = Server(cloud, setup=self.v01)
        try:
            id = "tilman_example_com"
            self.assertFalse(os.path.exists(os.path.join(s.data, "credentials.toml")))
            self.assertFalse(os.path.exists(os.path.join(s.data, "printers.json")))
            self.assertEqual(os.listdir(os.path.join(s.data, "accounts")), [id])
            for name in ("credentials.toml", "printers.json"):
                self.assertEqual(stat.S_IMODE(os.stat(s.acct(id, name)).st_mode), 0o600)
            self.assertTrue(os.path.isdir(s.acct(id, "home")))
            h = daemon_up(s, s.base)
            self.assertEqual(h["credentials"], s.acct(id, "credentials.toml"))
            self.assertEqual(h["home"], s.acct(id, "home"))
            _, p = s.json("GET", "/api/printers")
            self.assertEqual([(x["serial"], x["account"], x["email"]) for x in p],
                             [("H2S-SERIAL", id, "Tilman@Example.com")])
            self.assertEqual(s.json("GET", "/api/status/H2S-SERIAL")[1]["served_by"], s.acct(id, "credentials.toml"))
            self.assertEqual(cloud.calls, [])  # no login happened
            for body in s.bodies:
                self.assertNotIn(self.OLD_TOKEN.encode(), body)
            self.assertNotIn(self.OLD_TOKEN, s.logtext())
            self.assertNotIn(b"ACCESS-OLD", b"".join(s.bodies))
        finally:
            s.close()


def read_part(r):
    """One part of a multipart/x-mixed-replace body: (headers, bytes)."""
    assert r.readline() == b"--frame\r\n"
    headers = {}
    while (line := r.readline()) != b"\r\n":
        k, v = line.decode().strip().split(":", 1)
        headers[k.lower()] = v.strip()
    body = r.read(int(headers["content-length"]))
    assert r.readline() == b"\r\n"
    return headers, body


def open_stream(s, serial="SERIAL001"):
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=30)
    c.request("GET", "/api/camera/%s/stream.mjpeg" % serial)
    return c, c.getresponse()


def close_stream(c, r):
    r.close()  # the response keeps its own handle on the socket
    c.close()


def ffmpeg_children(pid):
    out = []
    for d in os.listdir("/proc"):
        try:
            with open("/proc/%s/stat" % d) as f:
                stat_ = f.read()
            with open("/proc/%s/cmdline" % d, "rb") as f:
                cmd = f.read()
        except OSError:
            continue
        if d.isdigit() and int(stat_.rsplit(")", 1)[1].split()[1]) == pid and b"ffmpeg" in cmd:
            out.append(int(d))
    return out


class StreamTest(unittest.TestCase):
    """Live MJPEG: one bridge stream and one ffmpeg per printer, shared, stopped after a grace period."""

    @classmethod
    def setUpClass(cls):
        cls.cloud = mock_cloud.start()
        cls.s = Server(cls.cloud, extra_env={"BAMBU_STREAM_GRACE": "1"})
        login(cls.s, "a@example.com")
        daemon_up(cls.s, cls.s.base)

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def stats(self):
        return self.s.daemon(self.s.base, "/streams")

    def test_01_yields_jpeg_parts(self):
        began = time.time()
        c, r = open_stream(self.s)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "multipart/x-mixed-replace; boundary=frame")
        for _ in range(10):
            headers, body = read_part(r)
            self.assertEqual(headers["content-type"], "image/jpeg")
            self.assertTrue(body.startswith(b"\xff\xd8") and body.endswith(b"\xff\xd9"))
        self.assertLess(time.time() - began, 5)  # first frame plus nine more at the clip's 10 fps
        close_stream(c, r)
        wait_for(lambda: self.stats()["active"] == 0, 8)

    def test_02_two_viewers_share_one_session_and_other_routes_answer(self):
        wait_for(lambda: self.stats()["active"] == 0, 6)  # the previous test's grace period
        total = self.stats()["total"]
        (c1, r1), (c2, r2) = open_stream(self.s), open_stream(self.s)
        read_part(r1), read_part(r2)
        self.assertEqual(self.stats(), {"total": total + 1, "active": 1})
        self.assertEqual(len(ffmpeg_children(self.s.proc.pid)), 1)
        began = time.time()
        st, p = self.s.json("GET", "/api/printers")  # a long response must not block the others
        self.assertEqual(st, 200)
        self.assertLess(time.time() - began, 3)
        st, body = self.s.req("GET", "/api/camera/SERIAL001/snapshot")  # the mock's own snapshot is a 502
        self.assertEqual(st, 200)
        self.assertTrue(body.startswith(b"\xff\xd8") and body.endswith(b"\xff\xd9"))
        close_stream(c1, r1)
        time.sleep(1.8)  # more than the grace: the second viewer keeps it alive
        self.assertEqual(self.stats()["active"], 1)
        read_part(r2)
        close_stream(c2, r2)

    def test_03_stops_after_grace(self):
        wait_for(lambda: self.stats()["active"] == 0 and not ffmpeg_children(self.s.proc.pid), 6)
        self.assertEqual(self.stats()["active"], 0)
        self.assertEqual(ffmpeg_children(self.s.proc.pid), [])
        total = self.stats()["total"]
        c, r = open_stream(self.s)  # a new viewer starts a new session
        read_part(r)
        self.assertEqual(self.stats()["total"], total + 1)
        close_stream(c, r)

    def test_04_upstream_error_maps_to_json(self):
        wait_for(lambda: not ffmpeg_children(self.s.proc.pid), 6)
        st, d = self.s.json("GET", "/api/camera/SERIAL002/stream.mjpeg")
        self.assertEqual((st, d["stage"], d["code"]), (502, "mint", -1))
        self.assertEqual(self.s.req("GET", "/api/camera/NOPE/stream.mjpeg")[0], 404)
        self.assertEqual(ffmpeg_children(self.s.proc.pid), [])

    def test_05_snapshot_falls_back_without_stream(self):
        wait_for(lambda: self.stats()["active"] == 0, 6)
        st, d = self.s.json("GET", "/api/camera/SERIAL001/snapshot")  # the mock's tunnel route answers 502
        self.assertEqual((st, d["stage"]), (502, "mint"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
