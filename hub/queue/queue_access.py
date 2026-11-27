"""Access control on top of the queue: Home Assistant accounts, per-printer grants.

Identity comes from Supervisor ingress headers; Cloud logins never reach users. A grant is active
while `until` is in the future and its condition entity (any HA helper or binary_sensor) is "on",
so HA decides schedules and presence, not this app.
get_user(user_id) -> {"name": str, "is_admin": bool} | None
"""
import json
import os
import secrets
import threading
from datetime import datetime, timezone

from queue_engine import QueueError, NotFound
from queue_registry import write_json

ROLES = ("owner", "user")


class Unauthorized(Exception):
    """No identity on the request (401)."""


class Forbidden(QueueError):
    """Refused by an access rule (403); the text names the rule."""


class Caller:
    def __init__(self, user_id="", name="", display="", internal=False):
        self.user_id, self.name, self.display, self.internal = user_id, name, display or name, internal


def caller_from_headers(headers, internal=False):
    # `internal` is an injected flag (HA integration/agent path), never read from a header.
    uid = (headers.get("X-Remote-User-Id") or "").strip()
    if not uid and not internal:
        raise Unauthorized("missing X-Remote-User-Id: requests need a Home Assistant identity")
    return Caller(uid, headers.get("X-Remote-User-Name") or "",
                  headers.get("X-Remote-User-Display-Name") or "", internal)


def _parse_until(value):
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("until must be an ISO datetime")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Grants:
    def __init__(self, registry, data_dir, get_state, clock):
        self.registry, self.get_state, self.clock = registry, get_state, clock
        self.path = os.path.join(data_dir, "grants.json")
        self.lock = threading.RLock()
        self.items = []
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.items = json.load(f)

    def add(self, user_id, printer, role="user", until=None, condition_entity=None, needs_approval=False):
        if not str(user_id or "").strip():
            raise ValueError("user_id is required")
        if printer != "*" and not self.registry.get(printer):
            raise ValueError("printer must be a known serial or *")
        if role not in ROLES:
            raise ValueError("role must be owner or user")
        if until:
            _parse_until(until)
        if condition_entity and "." not in condition_entity:
            raise ValueError("condition_entity must be an entity id")
        if not isinstance(needs_approval, bool):
            raise ValueError("needs_approval must be a boolean")
        g = {"id": secrets.token_hex(6), "user_id": user_id, "printer": printer, "role": role}
        for k, v in (("until", until), ("condition_entity", condition_entity)):
            if v:
                g[k] = v
        if needs_approval:
            g["needs_approval"] = True
        with self.lock:
            self.items.append(g)
            write_json(self.path, self.items)
        return g

    def remove(self, grant_id):
        with self.lock:
            before = len(self.items)
            self.items = [g for g in self.items if g["id"] != grant_id]
            if len(self.items) == before:
                raise NotFound("no such grant")
            write_json(self.path, self.items)

    def is_active(self, g):
        if g.get("until") and not self.clock() < _parse_until(g["until"]).timestamp():
            return False
        return not g.get("condition_entity") or self.get_state(g["condition_entity"]) == "on"

    def active(self, user_id, serial):
        return [g for g in list(self.items) if g["user_id"] == user_id
                and g["printer"] in (serial, "*") and self.is_active(g)]


class Access:
    def __init__(self, registry, grants, get_user):
        self.registry, self.grants, self.get_user = registry, grants, get_user

    def is_global_admin(self, c):
        if c.internal:
            return True
        u = self.get_user(c.user_id)
        return bool(u and u.get("is_admin"))

    def role(self, c, serial):
        """'admin', 'user' or None for this caller on one printer."""
        if self.is_global_admin(c):
            return "admin"
        active = self.grants.active(c.user_id, serial)
        if any(g["role"] == "owner" for g in active):
            return "admin"  # owners administer their own printer
        return "user" if active else None

    def serials_of(self, job):
        """Printers a job can run on (or ran on)."""
        if job.get("printer"):
            return [job["printer"]]
        if job["target"] != "any":
            return [job["target"]]
        return [p["serial"] for p in self.registry.all() if p["model"].lower() == job["model"].lower()]

    def best(self, c, serials):
        roles = {self.role(c, s) for s in serials}
        return "admin" if "admin" in roles else "user" if "user" in roles else None

    def job_allowed(self, job, serial):
        """Start-time check, so an 'any' job never lands where its owner has no active grant."""
        uid = job.get("owner_user_id")
        return uid is None or self.role(Caller(uid), serial) is not None

    def bind(self, queue, caller):
        return Bound(self, queue, caller)


class Bound:
    """The Queue API for one caller; every call checks the rule that governs it."""

    def __init__(self, access, queue, caller):
        self.a, self.q, self.c = access, queue, caller

    def _need_admin(self, serials, rule):
        if self.a.best(self.c, serials) != "admin":
            raise Forbidden("rule: " + rule)

    def _job(self, job_id):
        return self.q._find(job_id)

    def add(self, file, source="", target="any", model="", filaments=None, options=None):
        if target == "any":
            serials = [p["serial"] for p in self.a.registry.all() if p["model"].lower() == str(model).lower()]
        else:
            serials = [target]
        role = self.a.best(self.c, serials)
        if not role:
            raise Forbidden("rule: adding a job needs an active grant on the target printer")
        pending = False
        if role == "user":
            grants = [g for s in serials for g in self.a.grants.active(self.c.user_id, s)]
            pending = all(g.get("needs_approval") for g in grants)
        return self.q.add(file, source, target, model, filaments, options,
                          owner_user_id=self.c.user_id or None, owner_name=self.c.display, pending=pending)

    def reorder(self, job_id, position, scope=None):
        job = self._job(job_id)
        serials = self.a.serials_of(job)
        if self.a.best(self.c, serials) == "admin":
            return self.q.reorder(job_id, position, scope=scope)
        if self.a.best(self.c, serials) == "user" and job.get("owner_user_id") == self.c.user_id:
            return self.q.reorder(job_id, position, owner=self.c.user_id, scope=scope)
        raise Forbidden("rule: users may only reorder their own jobs; admins reorder any job")

    def move(self, job_id, target, model=""):
        job = self._job(job_id)
        self._need_admin(self.a.serials_of(job), "only an admin of the job's printer can move it")
        dest = self.a.serials_of({"target": target, "model": model or job["model"]})
        self._need_admin(dest, "moving a job needs admin on the destination printer too")
        return self.q.move(job_id, target, model)

    def cancel(self, job_id):
        job = self._job(job_id)
        serials = self.a.serials_of(job)
        role = self.a.best(self.c, serials)
        if role == "admin" or (role == "user" and job.get("owner_user_id") == self.c.user_id):
            return self.q.cancel(job_id)
        raise Forbidden("rule: users may only cancel their own jobs; admins cancel any job")

    def retry(self, job_id):
        job = next((j for j in self.q.history if j["id"] == job_id), None)
        if not job:
            raise NotFound("no such finished job")
        self._need_admin(self.a.serials_of(job), "only an admin of the job's printer can retry it")
        return self.q.retry(job_id)

    def approve(self, job_id):
        self._need_admin(self.a.serials_of(self._job(job_id)), "only an admin of the job's printer can approve")
        return self.q.approve(job_id)

    def reject(self, job_id):
        self._need_admin(self.a.serials_of(self._job(job_id)), "only an admin of the job's printer can reject")
        return self.q.reject(job_id)

    def skip(self, job_id):
        self._need_admin(self.a.serials_of(self._job(job_id)), "only an admin of the job's printer can skip a job")
        return self.q.skip(job_id)

    def start(self, serial, plate_clear=False, job_id=None):
        self._need_admin([serial], "only an admin of the printer can start a print")
        return self.q.start(serial, plate_clear=plate_clear, job_id=job_id)

    def snapshot(self):
        snap = self.q.snapshot()
        seen = {p["serial"] for p in snap["printers"] if self.a.role(self.c, p["serial"])}

        def visible(j):
            return any(s in seen for s in self.a.serials_of(j))
        return {"jobs": [j for j in snap["jobs"] if visible(j)],
                "history": [j for j in snap["history"] if visible(j)],
                "printers": [p for p in snap["printers"] if p["serial"] in seen]}

    # Grants are managed by HA admins only; owners do not delegate, to keep the model small.
    def _need_ha_admin(self):
        if not self.a.is_global_admin(self.c):
            raise Forbidden("rule: only Home Assistant admins manage grants")

    def grants_list(self):
        self._need_ha_admin()
        return list(self.a.grants.items)

    def grants_add(self, **fields):
        self._need_ha_admin()
        return self.a.grants.add(**fields)

    def grants_remove(self, grant_id):
        self._need_ha_admin()
        self.a.grants.remove(grant_id)
        return {"removed": grant_id}
