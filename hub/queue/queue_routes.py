"""Routes for the app server: ROUTES entries are (method, regex, fn(handler, match, query)),
the same shape as slicer/server.py. Mount with `ROUTES += make_routes(queue, access, is_internal)`.
The server also needs a do_DELETE that dispatches "DELETE".

Without `access` the routes run unchecked (single-user/tests). With it, every call needs a Home
Assistant identity from the ingress headers; is_internal(handler) marks the HA integration path."""
from queue_access import Forbidden, Unauthorized, caller_from_headers
from queue_engine import NotFound, QueueError

ID = r"([0-9a-f]{12})"
GRANT_FIELDS = ("user_id", "printer", "role", "until", "condition_entity", "needs_approval")


def make_routes(q, access=None, is_internal=lambda h: False):
    def guarded(fn):
        def run(h, m, query):
            try:
                if access:
                    api = access.bind(q, caller_from_headers(h.headers, is_internal(h)))
                else:
                    api = q
                return h.send(200, fn(h, m, api))
            except Unauthorized as e:
                return h.send(401, {"error": str(e)})
            except Forbidden as e:
                return h.send(403, {"error": str(e)})
            except NotFound as e:
                return h.send(404, {"error": str(e)})
            except QueueError as e:
                return h.send(409, {"error": str(e)})
            # ValueError (bad input) is turned into 400 by the server's dispatcher.
        return run

    def get_queue(h, m, api):
        return api.snapshot()

    def post_queue(h, m, api):
        b = h.json()
        return api.add(b.get("file"), b.get("source", ""), b.get("target", "any"), b.get("model", ""),
                       b.get("filaments"), b.get("options"))

    def reorder(h, m, api):
        b = h.json()  # the body can be read only once
        pos = b.get("position")
        if not isinstance(pos, int):
            raise ValueError("position must be an integer")
        scope = b.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise ValueError("scope must be a printer serial")
        return api.reorder(m.group(1), pos, scope=scope)

    def move(h, m, api):
        b = h.json()
        return api.move(m.group(1), b.get("target"), b.get("model", ""))

    def start_next(h, m, api):
        return api.start(m.group(1), plate_clear=h.json().get("plate_clear"))

    def start_job(h, m, api):
        b = h.json()
        if not b.get("job_id"):
            raise ValueError("job_id is required")
        return api.start(m.group(1), plate_clear=b.get("plate_clear"), job_id=b["job_id"])

    def grants_add(h, m, api):
        b = h.json()
        return api.grants_add(**{k: b[k] for k in GRANT_FIELDS if k in b})

    def one(name):
        return lambda h, m, api: getattr(api, name)(m.group(1))

    return [
        ("GET", r"/api/queue", guarded(get_queue)),
        ("POST", r"/api/queue", guarded(post_queue)),
        ("POST", r"/api/queue/" + ID + r"/reorder", guarded(reorder)),
        ("POST", r"/api/queue/" + ID + r"/move", guarded(move)),
        ("POST", r"/api/queue/" + ID + r"/cancel", guarded(one("cancel"))),
        ("POST", r"/api/queue/" + ID + r"/retry", guarded(one("retry"))),
        ("POST", r"/api/queue/" + ID + r"/approve", guarded(one("approve"))),
        ("POST", r"/api/queue/" + ID + r"/reject", guarded(one("reject"))),
        ("POST", r"/api/queue/" + ID + r"/skip", guarded(one("skip"))),
        ("POST", r"/api/printers/([^/]+)/start-next", guarded(start_next)),
        ("POST", r"/api/printers/([^/]+)/start", guarded(start_job)),
        ("GET", r"/api/grants", guarded(lambda h, m, api: api.grants_list())),
        ("POST", r"/api/grants", guarded(grants_add)),
        ("DELETE", r"/api/grants/" + ID, guarded(one("grants_remove"))),
    ]
