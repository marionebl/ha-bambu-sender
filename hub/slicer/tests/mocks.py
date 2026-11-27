"""Bridge and Home Assistant core stand-ins on one port. POST bodies to the core are recorded."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
CALLS = []
PRINTERS = [{"dev_id": "H2S0001", "serial": "H2S0001", "name": "Tilman", "dev_product_name": "H2S", "live": {"ok": 1}},
            {"dev_id": "X1C0001", "serial": "X1C0001", "name": "Other", "dev_product_name": "X1C", "live": None}]
AMS = {"ams": {"ams": [{"id": "0", "tray": [
    {"id": "0", "tray_type": "PETG", "tray_color": "ADB1B2FF", "tray_info_idx": "GFG02", "tray_sub_brands": "PETG HF"},
    {"id": "1", "tray_type": "PLA", "tray_color": "BB3D43FF", "tray_info_idx": "GFA01", "tray_sub_brands": "PLA Matte"},
    {"id": "2"}]}]}, "vt_tray": {}}
class H(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/api/printers": return self.reply(200, PRINTERS)
        if self.path.startswith("/api/ams/"): return self.reply(200, AMS)
        if self.path == "/calls": return self.reply(200, CALLS)
        self.reply(404, {})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CALLS.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if body.get("serial") == "BUSY":
            return self.reply(500, {"message": "Printer is RUNNING"})
        self.reply(200, {"service_response": {"result": "sent"}})
    def log_message(self, *a): pass
if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
