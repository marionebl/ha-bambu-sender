"""Mock of the boocloud-bridge daemon: `python3 mock_daemon.py <credentials.toml> <port>`.
Serves the printers listed in that credentials file, reports it (and HOME) in /health, records /print bodies."""
import base64
import json
import os
import sys
import tomllib
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AMS = {"ams": {"ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"}]}]}, "vt_tray": {}}


class Handler(BaseHTTPRequestHandler):
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
    print("mock daemon up for " + creds, flush=True)
    server.serve_forever()
