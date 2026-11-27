"""Stand-in for the Supervisor ingress gateway, for end-to-end UI checks against the real hub.

Serves the hub under /api/hassio_ingress/<token>/ like HA does: strips the prefix and adds the X-Remote-User-* headers of
the identity chosen with ?as=admin|user (kept in a cookie). Anything outside the prefix is a 404, so an absolute URL in
the UI shows up as a failed request.
"""
import http.client
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PREFIX = "/api/hassio_ingress/E2ETOKEN/"
IDENTITIES = {  # fake HA users; the test HA reports u-admin as system-admin
    "admin": ("u-admin", "alex", "Alex Admin"),
    "admin-new": ("u-admin", "alex", "Alex Admin"),
    "user": ("u-user", "lena", "Lena Tester"),
}
HOP = {"connection", "keep-alive", "transfer-encoding", "server", "date"}


def start(upstream, port=0):
    host, up_port = upstream

    class H(BaseHTTPRequestHandler):
        def reply(self, status, body=b"", extra=()):
            self.send_response(status)
            for k, v in extra:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_any(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            ident = (q.get("as") or [cookies["as"].value if "as" in cookies else "admin"])[0]
            if ident not in IDENTITIES:
                return self.reply(400, b"unknown identity")
            if u.path in ("/", PREFIX.rstrip("/")):
                return self.reply(302, extra=[("Location", PREFIX), ("Set-Cookie", "as=%s; Path=/" % ident)])
            if not u.path.startswith(PREFIX):
                return self.reply(404, b"outside the ingress prefix")
            uid, name, display = IDENTITIES[ident]
            length = int(self.headers.get("Content-Length") or 0)
            headers = {k: v for k, v in self.headers.items() if k.lower() in ("content-type", "content-length")}
            headers.update({"X-Remote-User-Id": uid, "X-Remote-User-Name": name, "X-Remote-User-Display-Name": display})
            conn = http.client.HTTPConnection(host, up_port, timeout=300)
            try:
                conn.request(self.command, "/" + u.path[len(PREFIX):] + ("?" + u.query if u.query else ""),
                             self.rfile.read(length) if length else None, headers)
                r = conn.getresponse()
                body = r.read()
            except OSError as e:
                return self.reply(502, str(e).encode())
            finally:
                conn.close()
            self.reply(r.status, body, [(k, v) for k, v in r.getheaders() if k.lower() not in HOP | {"content-length"}])

        do_GET = do_POST = do_PUT = do_DELETE = handle_any

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv
