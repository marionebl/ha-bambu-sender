"""Mock of the boocloud-bridge daemon: `python3 mock_daemon.py <credentials.toml> <port>`.
Serves the printers listed in that credentials file, reports it (and HOME) in /health, records /print bodies."""
import base64
import json
import os
import sys
import threading
import time
import tomllib
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AMS = {"ams": {"ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"}]}]}, "vt_tray": {}}


CLIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clip.h264")  # 3 s, 10 fps, keyframe every 10


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # chunked streaming needs it; every other reply sets Content-Length

    def stream(self):
        """Like the real daemon: endless chunked Annex B at the clip's own pace, until the client leaves."""
        s = self.server
        if self.path.split("/")[2] == "SERIAL002":
            return self.reply(502, {"error": "no tunnel", "stage": "mint", "code": -1})
        with open(CLIP, "rb") as f:
            clip = f.read()
        step = len(clip) // 30  # one tenth of a second of video
        with s.stream_lock:
            s.stream_total += 1
            s.stream_active += 1
        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/h264")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                for i in range(0, len(clip), step):
                    chunk = clip[i:i + step]
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                    time.sleep(0.1)
        except OSError:
            pass
        finally:
            with s.stream_lock:
                s.stream_active -= 1
            self.close_connection = True

    def reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        s = self.server
        if self.path == "/health":
            self.reply(200, {"status": "ok", "mqtt_connected": True, "cached_devices": len(s.serials),
                             "credentials": s.creds, "home": os.environ.get("HOME"), "pid": os.getpid()})
        elif self.path == "/printers":
            self.reply(200, {"printers": [{"name": "p", "serial": x, "gcode_state": "IDLE"} for x in s.serials[:1]]})
        elif self.path == "/streams":  # mock only: stream requests served so far and still open
            self.reply(200, {"total": s.stream_total, "active": s.stream_active})
        elif self.path.startswith("/camera/") and self.path.split("?")[0].endswith("/stream"):
            self.stream()
        elif self.path == "/commands":  # mock only: what /command received
            self.reply(200, s.commands)
        elif self.path == "/prints":  # mock only: what /print received
            self.reply(200, [{"file_b64": base64.b64encode(p["file"]).decode(), "params": p["params"]} for p in s.prints])
        elif self.path.startswith("/status/") and self.path[8:] in s.serials:
            # SERIALB01 (the H2S) answers like a new-protocol printer, the others like a P1S.
            np = {"cfg": "0", "fun": "0", "aux": "0", "stat": "0"} if self.path.endswith("SERIALB01") else {}
            self.reply(200, {"print": dict({"gcode_state": "IDLE", "nozzle_temper": 21.5}, **np), "served_by": s.creds})
        elif self.path.startswith("/camera/") and "/snapshot" in self.path:
            # Seen live: the plugin never calls back, and the daemon stops serving status until restarted.
            self.reply(502, {"error": "timeout after 20s waiting for camera URL callback", "stage": "mint"})
        elif self.path.startswith("/camera/") and "/url" in self.path:
            self.reply(200, {"ok": True, "path": self.path})
        elif self.path.startswith("/ams/") and self.path[5:] in s.serials:
            self.reply(200, dict(AMS, served_by=s.creds))
        else:
            self.reply(404, {"error": "unknown"})

    def do_POST(self):
        if self.path.startswith("/command/") and self.path[9:] in self.server.serials:
            self.server.commands.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            return self.reply(200, {"sent": True})
        if self.path != "/print":
            return self.reply(404, {"error": "unknown"})
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
        parts = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.iter_parts()}
        if "file" not in parts or "params" not in parts:
            return self.reply(400, {"error": "missing file or params"})
        params = json.loads(parts["params"])
        self.server.prints.append({"file": parts["file"], "params": params})
        if params["filename"] == "fail.gcode.3mf":
            return self.reply(502, {"result": "error", "return_code": -3140})
        self.reply(200, {"result": "sent", "return_code": -1, "print_result": -999,
                         "device_id": params["device_id"], "file": params["filename"]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    creds, port = sys.argv[1], int(sys.argv[2])
    with open(creds, "rb") as f:
        serials = [p["serial"] for p in tomllib.load(f).get("printers", {}).values()]
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.creds, server.serials, server.prints, server.commands = creds, serials, [], []
    server.stream_lock, server.stream_total, server.stream_active = threading.Lock(), 0, 0
    server.daemon_threads = True
    print("mock daemon up for " + creds, flush=True)
    server.serve_forever()
