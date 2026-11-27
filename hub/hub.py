#!/usr/bin/env python3
"""Hub UI on the ingress port: the slicer server, plus the Sender's page and API under /api/bridge/.

HA ingress reaches one port only. The slicer is the main UI, and the bridge keeps its own unchanged routes on 8098 for
the integration, so this proxies instead of touching either server. Relative URLs in both pages work under /api/bridge/.
"""
import http.client
import os
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I drops the script dir
import slicer_server as s

BRIDGE = ("127.0.0.1", int(os.environ.get("BAMBU_PORT", "8098")))
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "server", "date"}
LINK = '<h1>Bambu Hub</h1>\n  <p><a href="api/bridge/">Bambu accounts</a></p>'


def page(h, m, q):
    with open(os.path.join(s.HERE, "index.html"), encoding="utf-8") as f:
        html = f.read()
    assert "<h1>Bambu Slicer</h1>" in html
    h.send(200, html.replace("<h1>Bambu Slicer</h1>", LINK).encode(), "text/html; charset=utf-8")


def bridge(h, m, q):
    path = "/" + m.group(1)
    query = urlsplit(h.path).query
    length = int(h.headers.get("Content-Length") or 0)
    conn = http.client.HTTPConnection(*BRIDGE, timeout=300)
    try:
        conn.request(h.command, path + ("?" + query if query else ""), h.rfile.read(length) if length else None,
                     {k: v for k, v in h.headers.items() if k.lower() in ("content-type", "content-length")})
        r = conn.getresponse()
    except OSError as e:
        return h.send(502, {"error": "bridge is not reachable: %s" % e})
    h.send_response(r.status)
    for k, v in r.getheaders():
        if k.lower() not in HOP:
            h.send_header(k, v)
    h.close_connection = True  # the camera stream has no length; HTTP/1.0 ends it by closing
    h.end_headers()
    try:
        while chunk := r.read(65536):
            h.wfile.write(chunk)
    except OSError:
        pass  # viewer left
    finally:
        conn.close()


s.ROUTES[:] = [r for r in s.ROUTES if r[:2] != ("GET", "/")] + [
    ("GET", "/", page),
    ("GET", r"/api/bridge/(.*)", bridge),
    ("POST", r"/api/bridge/(.*)", bridge),
    ("DELETE", r"/api/bridge/(.*)", bridge),
]
s.Handler.do_DELETE = lambda self: self.dispatch("DELETE")

if __name__ == "__main__":
    s.main()
