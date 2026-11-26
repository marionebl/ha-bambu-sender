"""Bambu Sender and Home Assistant core stand-ins on one port. POST bodies to the core are recorded."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
CALLS = []
PRINTERS = [{"dev_id": "H2S0001", "serial": "H2S0001", "name": "Tilman", "dev_product_name": "H2S", "live": {"ok": 1}},
            {"dev_id": "P1S0001", "serial": "P1S0001", "name": "Meno", "dev_product_name": "P1S", "live": {"ok": 1}},
            {"dev_id": "X1C0001", "serial": "X1C0001", "name": "Other", "dev_product_name": "X1C", "live": None}]
AMS = {"ams": {"ams": [{"id": "0", "tray": [
    {"id": "0", "tray_type": "PETG", "tray_color": "ADB1B2FF", "tray_info_idx": "GFG02", "tray_sub_brands": "PETG HF"},
    {"id": "1", "tray_type": "PLA", "tray_color": "BB3D43FF", "tray_info_idx": "GFA01", "tray_sub_brands": "PLA Matte"},
    {"id": "2"}]}]}, "vt_tray": {}}
# HA's template API answer for the H2S (ha-bambulab tray sensors); none for the P1S, so it falls back to AMS.
HA_TRAYS = {"H2S0001": [{"ams": "0", "id": "0", "type": "PETG", "idx": "GFG02", "name": "Bambu PETG HF", "colour": "#ADB1B2"},
                        {"ams": "0", "id": "1", "type": "PLA", "idx": "GFA01", "name": "Bambu PLA Matte", "colour": "#BB3D43"}]}
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
        if self.path == "/core/template":
            return self.reply(200, HA_TRAYS.get(body["variables"]["serial"], []))
        CALLS.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if body.get("serial") == "BUSY":
            return self.reply(500, {"message": "Printer is RUNNING"})
        self.reply(200, {"service_response": {"result": "sent"}})
    def log_message(self, *a): pass
if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
