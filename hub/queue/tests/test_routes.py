import re

from queue_routes import make_routes


class H:
    def __init__(self, body=None):
        self.body, self.out = body or {}, None

    def json(self):
        return self.body

    def send(self, status, body):
        self.out = (status, body)


def call(q, method, path, body=None):
    h = H(body)
    for meth, rx, fn in make_routes(q):
        m = re.fullmatch(rx, path)
        if meth == method and m:
            fn(h, m, {})
            return h.out
    raise AssertionError("no route " + path)


def test_add_list_start_flow(make):
    st, job = call(make, "POST", "/api/queue", {"file": "a.gcode.3mf", "target": "S1"})
    assert st == 200
    assert call(make, "GET", "/api/queue")[1]["jobs"][0]["id"] == job["id"]
    assert call(make, "POST", "/api/printers/S1/start-next", {})[0] == 409
    assert call(make, "POST", "/api/printers/S1/start-next", {"plate_clear": True})[1]["state"] == "starting"
    assert call(make, "POST", "/api/queue/" + "0" * 12 + "/cancel")[0] == 404
