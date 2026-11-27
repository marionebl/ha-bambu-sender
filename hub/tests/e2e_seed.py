#!/usr/bin/env python3
"""Write the hub's data dir for the e2e run: three printers, two grants for the test user, spools, a queue and history.
  python3 e2e_seed.py DATA_DIR       (an empty DATA_DIR is the "not set up yet" state)"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_mock import H2S, P1S, X1C, NAMES, PREFIX

data, now = sys.argv[1], time.time()
os.makedirs(data, exist_ok=True)


def dump(name, obj):
    with open(os.path.join(data, name), "w") as f:
        json.dump(obj, f, indent=1)


dump("printers.json", {s: {"name": NAMES[s][0], "model": NAMES[s][1], "account": "acc1", "prefix": PREFIX[s], "plate_type": "Textured PEI Plate",
                           "nozzle": "0.4", "pause_entities": [], "electricity_kwh_price": 0.32} for s in (H2S, P1S, X1C)})
dump("grants.json", [{"id": "a00000000001", "user_id": "u-user", "printer": H2S, "role": "user"},
                     {"id": "a00000000002", "user_id": "u-user", "printer": P1S, "role": "user"}])
spool = lambda i, t, c, n, left, price, pr, u, s: {"id": "5b%010d" % i, "brand": n.split()[0], "type": t, "colour": c, "name": n, "grams_left": left,
                                                    "grams_total": 1000, "price_per_kg": price, "printer": pr, "unit": u, "slot": s}
dump("spools.json", [spool(1, "PLA", "F2F2F2", "Acme PLA White", 880, 21.0, H2S, 0, 0), spool(2, "PETG", "1F6FEB", "Prusament PETG Blue", 610, 26.0, H2S, 0, 1),
                     spool(3, "PLA", "E4572E", "Sunlu PLA Red", 140, 15.0, H2S, 0, 2), spool(4, "TPU", "222222", "TPU 95A Black", 730, 30.0, H2S, 1, 0),
                     spool(5, "PLA", "2E2E2E", "Generic PLA Black", 950, 18.0, None, None, None)])
job = lambda i, state, target, owner, name, age, **kw: dict({"id": "b%011d" % i, "file": name + ".gcode.3mf", "source": name + ".stl", "target": target,
                                                              "model": "H2S" if target == "any" else "", "filaments": [], "error": None, "state": state,
                                                              "options": {"timelapse": False, "bed_leveling": True, "use_ams": True},
                                                              "owner_user_id": owner[0], "owner_name": owner[1], "created_at": now - age}, **kw)
alex, lena = ("u-admin", "Alex Admin"), ("u-user", "Lena Tester")
dump("queue.json", {"jobs": [
    job(1, "printing", H2S, alex, "gridfinity_bin_4x2", 9000, printer=H2S, started_at=now - 3300, grams=41.0),
    job(2, "queued", H2S, lena, "phone_stand", 3000, grams=22.5),
    job(3, "pending_approval", H2S, lena, "cable_clip", 1200, grams=3.1),
    job(4, "queued", H2S, alex, "spool_holder", 900, grams=64.0),
    job(5, "queued", P1S, alex, "desk_organiser", 600, grams=38.2),
    job(6, "queued", "any", lena, "hook", 300, grams=6.0)],
    "history": [
        job(7, "done", H2S, lena, "benchy", 90000, printer=H2S, started_at=now - 86000, finished_at=now - 80000, grams=14.2, cost=0.47),
        job(8, "failed", P1S, alex, "vase", 80000, printer=P1S, started_at=now - 79000, finished_at=now - 78000, error="printer went idle before finishing"),
        job(9, "cancelled", H2S, lena, "lid", 70000, finished_at=now - 69000)]})
