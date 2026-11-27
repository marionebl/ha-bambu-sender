"""Fake cloud login endpoints. Accounts: direct@ (password only), tfa@ (password then tfa), anything else
(password, then email code). Password "pw", email code "123456", tfa code "654321".
Every login issues new tokens (server.tokens: latest per email, server.issued: all) and each email has its own printers."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import externals  # noqa: E402

DEVICES = {
    "a@example.com": [
        {"dev_id": "SERIAL001", "name": "Office P1S", "dev_product_name": "P1S", "dev_access_code": "ACCESS-1234",
         "online": True},
        {"dev_id": "SERIAL002", "name": "Office P1S", "dev_product_name": "A1", "dev_access_code": "ACCESS-5678"}],
    "b@example.com": [
        {"dev_id": "SERIALB01", "name": "Garage H2S", "dev_product_name": "H2S", "dev_access_code": "ACCESS-9999"}],
}


def devices(email):
    return DEVICES.get(email, [{"dev_id": "SER-" + re.sub(r"\W", "", email), "name": "Default"}])


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def issue(self, email):
        srv = self.server
        n, tag = 1 + sum(1 for e, _, _ in srv.issued if e == email), re.sub(r"\W", "", email)
        token, refresh = "TOKEN-SECRET-%s-%d" % (tag, n), "REFRESH-SECRET-%s-%d" % (tag, n)
        srv.issued.append((email, token, refresh))
        srv.tokens[email] = (token, refresh)
        srv.owner[token] = email
        self.reply(200, {"accessToken": token, "refreshToken": refresh})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.server.calls.append((self.path, body, self.headers.get(externals.CLOUD_CLIENT_HEADER)))
        if self.path == "/v1/user-service/user/login":
            acct = body.get("account", "")
            if "password" in body:
                if body["password"] != "pw":
                    return self.reply(400, {"error": "bad password"})
                if acct.startswith("direct@"):
                    return self.issue(acct)
                if acct.startswith("tfa@"):
                    return self.reply(200, {"tfaKey": "tfa-key-" + acct})
                return self.reply(200, {"loginType": "verifyCode"})
            return self.issue(acct) if body.get("code") == "123456" else self.reply(400, {"error": "bad code"})
        if self.path == "/v1/user-service/user/sendemail/code":
            # The real cloud answers 200 with an empty body here (seen live on 2026-11-21).
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/v1/user-service/user/tfa":
            key = body.get("tfaKey", "")
            if key.startswith("tfa-key-") and body.get("tfaCode") == "654321":
                return self.issue(key[8:])
            return self.reply(400, {"error": "bad tfa"})
        self.reply(404, {})

    def do_GET(self):
        email = self.server.owner.get(self.headers.get("Authorization", "")[7:])
        if not email:
            return self.reply(401, {})
        if self.path == "/v1/design-user-service/my/preference":
            return self.reply(200, {"uid": 4242, "name": "Tester"})
        if self.path == "/v1/iot-service/api/user/bind":
            return self.reply(200, {"devices": devices(email)})
        self.reply(404, {})

    def log_message(self, *a):
        pass


def start():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.calls, server.issued, server.tokens, server.owner = [], [], {}, {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
