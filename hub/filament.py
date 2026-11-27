"""Filament grams from a slice, cost arithmetic and the spool store."""
import json
import os
import re
import secrets
import threading
import xml.etree.ElementTree as ET
import zipfile

from queue_registry import write_json

# Average printing watts, PLA column of the vendor wiki's "Supply voltage" page;
# H2S has no published figure, 150 is our assumption between A1 (95) and H2D (197).
WATTS = {"H2S": 150, "X1C": 105, "X1": 105, "X1E": 105, "P1S": 105, "P1P": 110, "A1": 95, "A1 MINI": 80}
DEFAULT_WATTS = 105


def slice_usage(path):
    """{"seconds", "filaments": [{"type", "colour", "grams"}], "grams"} from Metadata/slice_info.config of a .gcode.3mf."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("Metadata/slice_info.config"))
    plate = root.find("plate")
    meta = {m.get("key"): m.get("value") for m in plate.iter("metadata")}
    fils = [{"type": f.get("type") or "", "colour": re.sub(r"[^0-9A-Fa-f]", "", f.get("color") or "")[:6].upper(),
             "grams": float(f.get("used_g") or 0)} for f in plate.iter("filament")]
    return {"seconds": int(float(meta.get("prediction") or 0)), "filaments": fils,
            "grams": round(sum(f["grams"] for f in fils), 2)}


def watts(model):
    return WATTS.get(str(model or "").upper(), DEFAULT_WATTS)


def job_cost(filament_grams, price_per_kg, seconds, model, kwh_price):
    """Filament (grams x price per kg / 1000, per filament) plus electricity (hours x watts / 1000 x kWh price)."""
    material = sum(g * p / 1000 for g, p in zip(filament_grams, price_per_kg))
    power = seconds / 3600 * watts(model) / 1000 * (kwh_price or 0)
    return round(material + power, 2)


def _colour(c):
    return re.sub(r"[^0-9a-f]", "", str(c or "").lower())[:6]


FIELDS = ("brand", "type", "colour", "name", "grams_left", "grams_total", "price_per_kg", "printer", "unit", "slot")
NUMBERS = ("grams_left", "grams_total", "price_per_kg")


class Spools:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "spools.json")
        self.lock = threading.RLock()
        self.items = []
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.items = json.load(f)

    def _clean(self, fields, base=None):
        unknown = set(fields) - set(FIELDS)
        if unknown:
            raise ValueError("unknown spool fields: " + ", ".join(sorted(unknown)))
        s = dict(base or {"brand": "", "type": "", "colour": "", "name": "", "grams_left": 0, "grams_total": 1000,
                          "price_per_kg": 0, "printer": None, "unit": None, "slot": None})
        s.update(fields)
        if not str(s["type"]).strip():
            raise ValueError("type is required")
        for k in NUMBERS:
            if k == "price_per_kg" and s[k] is None:
                continue  # "no price" is allowed; cost counts it as 0
            if isinstance(s[k], bool) or not isinstance(s[k], (int, float)) or s[k] < 0:
                raise ValueError(k + " must be a number >= 0")
        for k in ("unit", "slot"):
            if s[k] is not None and (isinstance(s[k], bool) or not isinstance(s[k], int)):
                raise ValueError(k + " must be an integer")
        if s["printer"] and (s["unit"] is None or s["slot"] is None):
            raise ValueError("an assigned spool needs unit and slot")
        s["colour"] = _colour(s["colour"]).upper()
        return s

    def add(self, **fields):
        s = dict(self._clean(fields), id=secrets.token_hex(6))
        with self.lock:
            self.items.append(s)
            write_json(self.path, self.items)
        return s

    def get(self, spool_id):
        return next((s for s in self.items if s["id"] == spool_id), None)

    def update(self, spool_id, **fields):
        with self.lock:
            s = self.get(spool_id)
            if not s:
                return None
            s.update(self._clean(fields, {k: v for k, v in s.items() if k != "id"}))
            write_json(self.path, self.items)
            return s

    def remove(self, spool_id):
        with self.lock:
            before = len(self.items)
            self.items = [s for s in self.items if s["id"] != spool_id]
            if len(self.items) == before:
                return False
            write_json(self.path, self.items)
            return True

    def in_tray(self, serial, unit, slot):
        return next((s for s in self.items if s["printer"] == serial and s["unit"] == unit and s["slot"] == slot), None)

    def match(self, serial, ftype, colour, tray_hint=None):
        """The spool in the tray the slice was made for, else a loaded spool of that type and colour."""
        loaded = [s for s in self.items if s["printer"] == serial]
        if tray_hint:
            hit = self.in_tray(serial, *tray_hint)
            if hit and hit["type"].lower() == ftype.lower():
                return hit
        return next((s for s in loaded if s["type"].lower() == ftype.lower()
                     and (not colour or _colour(s["colour"]) == _colour(colour))), None)

    def consume(self, spool, grams):
        with self.lock:
            spool["grams_left"] = round(max(0.0, spool["grams_left"] - grams), 2)
            write_json(self.path, self.items)
