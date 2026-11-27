"""Printer registry: printers keyed by serial, stored as JSON in the data dir (no serials in code)."""
import json
import os
import tempfile
import threading

FIELDS = ("name", "model", "account", "prefix", "plate_type", "nozzle", "pause_entities", "electricity_kwh_price")


def write_json(path, data):
    # Write-then-rename so a crash never leaves a half-written queue file.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


class Registry:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "printers.json")
        self.lock = threading.RLock()
        os.makedirs(data_dir, exist_ok=True)
        self.printers = {}
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.printers = json.load(f)

    def put(self, serial, **fields):
        serial = str(serial or "").strip()
        if not serial:
            raise ValueError("serial is required")
        unknown = set(fields) - set(FIELDS)
        if unknown:
            raise ValueError("unknown printer fields: " + ", ".join(sorted(unknown)))
        for key in ("name", "model", "prefix"):
            if not str(fields.get(key) or "").strip():
                raise ValueError(key + " is required")
        pause = fields.get("pause_entities") or []
        if not isinstance(pause, list) or not all(isinstance(e, str) and "." in e for e in pause):
            raise ValueError("pause_entities must be a list of entity ids")
        entry = {k: fields.get(k) or "" for k in FIELDS}
        entry["pause_entities"] = pause
        with self.lock:
            self.printers[serial] = entry
            write_json(self.path, self.printers)
        return dict(entry, serial=serial)

    def remove(self, serial):
        with self.lock:
            self.printers.pop(serial, None)
            write_json(self.path, self.printers)

    def get(self, serial):
        entry = self.printers.get(serial)
        return dict(entry, serial=serial) if entry else None

    def all(self):
        return [dict(v, serial=k) for k, v in self.printers.items()]

    def entities(self, serial):
        """vendor-integration sensor ids for a printer, from its entity prefix."""
        p = self.printers[serial]["prefix"]
        return {key: "sensor.%s_%s" % (p, key) for key in
                ("print_status", "active_tray", "current_stage", "print_progress")}
