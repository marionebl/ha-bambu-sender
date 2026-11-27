"""Multi-printer print queue. Home Assistant I/O is injected so tests can use fakes.

get_state(entity_id) -> str | None          fresh state of the vendor integration's sensor
call_service(domain, service, data) -> any  raises on refusal; the message is kept on the job
get_trays(serial) -> [{"type": "PLA", "colour": "#RRGGBB"}]   loaded AMS trays
"""
import re
import secrets
import threading
import time

from queue_registry import write_json
import json
import os

HISTORY = 200
STARTABLE = {"idle", "finish", "failed"}  # print status values that allow a new job
NOT_A_TRAY = {"none", "unknown", "unavailable", ""}
START_TIMEOUT = 600
OPTIONS = {"timelapse": False, "bed_leveling": True, "use_ams": True}
WAITING = ("queued", "pending_approval")  # not yet sent to a printer


class QueueError(Exception):
    """A request the queue refuses; the text is shown to the person."""


class NotFound(QueueError):
    pass


def _colour(c):
    return re.sub(r"[^0-9a-f]", "", str(c or "").lower())[:6]


def _tray_ok(v):
    # Tray "0" is a real slot; only these words mean no filament.
    return v is not None and str(v).strip().lower() not in NOT_A_TRAY


class Queue:
    def __init__(self, registry, data_dir, get_state, call_service, get_trays=None,
                 clock=time.time, start_timeout=START_TIMEOUT, job_allowed=None):
        self.registry = registry
        self.path = os.path.join(data_dir, "queue.json")
        self.get_state, self.call_service = get_state, call_service
        self.get_trays = get_trays or (lambda serial: [])
        self.clock, self.start_timeout = clock, start_timeout
        # job_allowed(job, serial) lets the access layer veto a job at start time (grant lapsed).
        self.job_allowed = job_allowed
        self.lock = threading.RLock()
        self.jobs, self.history = [], []
        # changing_filament lasts ~1 s, shorter than a poll; observe() latches it per serial.
        self.seen_change = {}
        os.makedirs(data_dir, exist_ok=True)
        if os.path.exists(self.path):
            with open(self.path) as f:
                saved = json.load(f)
            self.jobs, self.history = saved["jobs"], saved["history"]

    # -- storage --
    def _save(self):
        del self.history[:-HISTORY]
        write_json(self.path, {"jobs": self.jobs, "history": self.history})

    def _find(self, job_id):
        for j in self.jobs:
            if j["id"] == job_id:
                return j
        raise NotFound("no such queued job")

    def _finish(self, job, state, error=None):
        job.update(state=state, error=error, finished_at=self.clock())
        self.jobs.remove(job)
        self.history.append(job)

    # -- jobs --
    def add(self, file, source="", target="any", model="", filaments=None, options=None,
            owner_user_id=None, owner_name="", pending=False):
        if not str(file or "").endswith(".gcode.3mf") or "/" in file or ".." in file:
            raise ValueError("file must be a .gcode.3mf name in the share")
        target = self._check_target(target, model)
        opts = dict(OPTIONS)
        for k, v in (options or {}).items():
            if k not in OPTIONS or not isinstance(v, bool):
                raise ValueError("bad option: " + str(k))
            opts[k] = v
        fil = []
        for f in filaments or []:
            if not f.get("type"):
                raise ValueError("each filament needs a type")
            fil.append({"type": str(f["type"]), "colour": f.get("colour") or ""})
        job = {"id": secrets.token_hex(6), "file": file, "source": source or file,
               "target": target, "model": model if target == "any" else "",
               "filaments": fil, "options": opts, "error": None,
               "state": "pending_approval" if pending else "queued",
               "owner_user_id": owner_user_id, "owner_name": owner_name,
               "created_at": self.clock()}
        with self.lock:
            self.jobs.append(job)
            self._save()
        return job

    def _check_target(self, target, model):
        if target == "any":
            if not str(model or "").strip():
                raise ValueError("model is required for target any")
        elif not self.registry.get(target):
            raise ValueError("unknown printer")
        return target

    def reorder(self, job_id, position, owner=None, scope=None):
        """position is 0-based among the waiting jobs, or among the owner's when owner is given.
        The jobs are permuted within the slots they already hold, so nobody else moves.
        scope (a serial) narrows the pool to waiting jobs whose target or printer is that serial."""
        with self.lock:
            job = self._find(job_id)
            if job["state"] not in WAITING:
                raise QueueError("only waiting jobs can be reordered")
            slots = [i for i, j in enumerate(self.jobs)
                     if j["state"] in WAITING and (owner is None or j.get("owner_user_id") == owner)
                     and (scope is None or scope in (j["target"], j.get("printer")))]
            pool = [self.jobs[i] for i in slots]
            if job not in pool:
                raise QueueError("job is not in this reorder scope")
            pool.remove(job)
            pool.insert(max(0, min(int(position), len(pool))), job)
            for i, j in zip(slots, pool):
                self.jobs[i] = j
            self._save()
            return job

    def move(self, job_id, target, model=""):
        with self.lock:
            job = self._find(job_id)
            if job["state"] not in WAITING:
                raise QueueError("only waiting jobs can be moved")
            job["target"] = self._check_target(target, model or job["model"])
            job["model"] = (model or job["model"]) if target == "any" else ""
            self._save()
            return job

    def cancel(self, job_id):
        with self.lock:
            job = self._find(job_id)
            if job["state"] not in WAITING:
                raise QueueError("a started job runs on the printer; stop it there")
            self._finish(job, "cancelled")
            self._save()
            return job

    def retry(self, job_id):
        with self.lock:
            job = next((j for j in self.history if j["id"] == job_id), None)
            if not job:
                raise NotFound("no such finished job")
            if job["state"] not in ("failed", "cancelled"):
                raise QueueError("only failed or cancelled jobs can be retried")
            self.history.remove(job)
            for k in ("finished_at", "started_at"):
                job.pop(k, None)
            job.update(state="queued", error=None, printer=None)
            self.jobs.append(job)
            self._save()
            return job

    def approve(self, job_id):
        with self.lock:
            job = self._find(job_id)
            if job["state"] != "pending_approval":
                raise QueueError("job is not waiting for approval")
            job["state"] = "queued"
            self._save()
            return job

    def reject(self, job_id):
        with self.lock:
            job = self._find(job_id)
            if job["state"] != "pending_approval":
                raise QueueError("job is not waiting for approval")
            self._finish(job, "cancelled", "rejected")
            self._save()
            return job

    def skip(self, job_id):
        """Send a waiting job to the end of the queue."""
        with self.lock:
            job = self._find(job_id)
            if job["state"] not in WAITING:
                raise QueueError("only waiting jobs can be skipped")
            self.jobs.remove(job)
            self.jobs.append(job)
            self._save()
            return job

    # -- matching --
    def _filaments_fit(self, job, serial):
        # Each wanted filament needs its own tray of the same type and colour.
        free = list(self.get_trays(serial) or [])
        for want in job["filaments"]:
            hit = next((t for t in free if t.get("type", "").lower() == want["type"].lower()
                        and (not want["colour"] or _colour(t.get("colour")) == _colour(want["colour"]))), None)
            if not hit:
                return False
            free.remove(hit)
        return True

    def _fits(self, job, serial, printer):
        if job["target"] == serial:
            return True
        return (job["target"] == "any" and job["model"].lower() == printer["model"].lower()
                and self._filaments_fit(job, serial))

    def next_for(self, serial):
        printer = self.registry.get(serial)
        if not printer:
            return None
        for job in self.jobs:
            # pending_approval is skipped here: only an admin's approval makes a job startable.
            if job["state"] == "queued" and self._fits(job, serial, printer) \
                    and (not self.job_allowed or self.job_allowed(job, serial)):
                return job
        return None

    def _busy(self, serial):
        return next((j for j in self.jobs if j["state"] in ("starting", "printing")
                     and j.get("printer") == serial), None)

    # -- start --
    def start(self, serial, plate_clear=False, job_id=None):
        # Only a person starts a print, after confirming the plate is clear.
        if plate_clear is not True:
            raise QueueError("confirm that the plate is clear")
        with self.lock:
            printer = self.registry.get(serial)
            if not printer:
                raise NotFound("unknown printer")
            if self._busy(serial):
                raise QueueError("this printer already has a job starting or printing")
            ent = self.registry.entities(serial)
            status = self.get_state(ent["print_status"])
            if status not in STARTABLE:
                raise QueueError("printer is not ready (status: %s)" % status)
            if job_id:
                # Explicit pick by an admin: skips the queue order and the owner's grant check.
                job = self._find(job_id)
                if job["state"] != "queued" or not self._fits(job, serial, printer):
                    raise QueueError("that job is not queued or does not fit this printer")
            else:
                job = self.next_for(serial)
            if not job:
                raise QueueError("no queued job fits this printer")
            err = self._send(printer, serial, job)
            if err:
                job["error"] = err  # stays first in the queue
                self._save()
                raise QueueError(err)
            job.update(state="starting", error=None, printer=serial, started_at=self.clock())
            self.seen_change[serial] = False
            self._save()
            return job

    def _send(self, printer, serial, job):
        """Returns an error text, or None. Paused entities come back even when the send fails."""
        paused = [e for e in printer["pause_entities"] if self.get_state(e) == "on"]
        try:
            for e in paused:
                self.call_service("homeassistant", "turn_off", {"entity_id": e})
            o = job["options"]
            # force: the bridge status can be hours old; the fresh sensor was checked above.
            self.call_service("druckwerk", "print_file", {
                "serial": serial, "file": job["file"], "timelapse": o["timelapse"],
                "bed_leveling": o["bed_leveling"], "use_ams": o["use_ams"], "force": True})
            return None
        except Exception as e:  # the guard's refusal text is the useful part
            return str(e) or e.__class__.__name__
        finally:
            for e in paused:
                try:
                    self.call_service("homeassistant", "turn_on", {"entity_id": e})
                except Exception:
                    pass

    # -- progress --
    def observe(self, serial, stage):
        """Feed stage changes from state events so a short changing_filament is not missed."""
        if stage == "changing_filament" and self._starting(serial):
            self.seen_change[serial] = True

    def _starting(self, serial):
        j = self._busy(serial)
        return j if j and j["state"] == "starting" else None

    def tick(self, serial):
        with self.lock:
            job = self._busy(serial)
            if not job:
                return None
            ent = self.registry.entities(serial)
            status, stage, tray = (self.get_state(ent[k]) for k in ("print_status", "current_stage", "active_tray"))
            if job["state"] == "starting":
                self._tick_starting(job, serial, ent, status, stage, tray)
            else:
                self._tick_printing(job, status)
            self._save()
            return job

    def _tick_starting(self, job, serial, ent, status, stage, tray):
        if stage == "changing_filament":
            self.seen_change[serial] = True
        loaded = _tray_ok(tray)
        started = loaded and (stage == "changing_filament" or self.seen_change.get(serial))
        if not started and loaded and stage == "printing":
            try:
                started = float(self.get_state(ent["print_progress"])) > 0
            except (TypeError, ValueError):
                started = False
        # "running" alone proves nothing: a print once ran an hour with stage idle and no tray.
        if started:
            job["state"] = "printing"
        elif self.clock() - job["started_at"] > self.start_timeout:
            self._finish(job, "failed", "did not start within %d s (status %s, stage %s, tray %s)"
                         % (self.start_timeout, status, stage, tray))

    def _tick_printing(self, job, status):
        if status == "finish":
            self._finish(job, "done")
        elif status == "failed":
            self._finish(job, "failed", "printer reported a failed print")
        elif status == "idle":
            self._finish(job, "failed", "printer went idle before finishing")

    def snapshot(self):
        with self.lock:
            return {"jobs": list(self.jobs), "history": self.history[-HISTORY:][::-1],
                    "printers": self.registry.all()}
