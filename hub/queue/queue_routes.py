"""Routes for the app server: ROUTES entries are (method, regex, fn(handler, match, query)),
the same shape as slicer/server.py. Mount with `ROUTES += make_routes(queue)`."""
from queue_engine import NotFound, QueueError

ID = r"([0-9a-f]{12})"


def make_routes(q):
    def guarded(fn):
        def run(h, m, query):
            try:
                return h.send(200, fn(h, m, query))
            except NotFound as e:
                return h.send(404, {"error": str(e)})
            except QueueError as e:
                return h.send(409, {"error": str(e)})
            # ValueError (bad input) is turned into 400 by the server's dispatcher.
        return run

    def get_queue(h, m, query):
        return q.snapshot()

    def post_queue(h, m, query):
        b = h.json()
        return q.add(b.get("file"), b.get("source", ""), b.get("target", "any"), b.get("model", ""),
                     b.get("filaments"), b.get("options"))

    def reorder(h, m, query):
        pos = h.json().get("position")
        if not isinstance(pos, int):
            raise ValueError("position must be an integer")
        return q.reorder(m.group(1), pos)

    def move(h, m, query):
        b = h.json()
        return q.move(m.group(1), b.get("target"), b.get("model", ""))

    def cancel(h, m, query):
        return q.cancel(m.group(1))

    def retry(h, m, query):
        return q.retry(m.group(1))

    def start_next(h, m, query):
        return q.start(m.group(1), plate_clear=h.json().get("plate_clear"))

    return [
        ("GET", r"/api/queue", guarded(get_queue)),
        ("POST", r"/api/queue", guarded(post_queue)),
        ("POST", r"/api/queue/" + ID + r"/reorder", guarded(reorder)),
        ("POST", r"/api/queue/" + ID + r"/move", guarded(move)),
        ("POST", r"/api/queue/" + ID + r"/cancel", guarded(cancel)),
        ("POST", r"/api/queue/" + ID + r"/retry", guarded(retry)),
        ("POST", r"/api/printers/([^/]+)/start-next", guarded(start_next)),
    ]
