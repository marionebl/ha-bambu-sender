"""Grams from a real slice_info.config, cost arithmetic, spools and the decrement when a job finishes."""
import os

import pytest

import filament
from hubfakes import ADMIN, OTHER, SERIAL, SERIAL2, USER, make_3mf


def test_grams_from_a_real_sliced_3mf(tmp_path):
    # fixtures/slice_info.config is the file the slicer 2.9.2 wrote for fixtures/test_plate.stl.
    use = filament.slice_usage(make_3mf(tmp_path / "s.gcode.3mf"))
    assert use == {"seconds": 7440, "grams": 75.22, "filaments": [{"type": "PETG", "colour": "ADB1B2", "grams": 75.22}]}


def test_grams_of_several_filaments(tmp_path):
    xml = ('<config><plate><metadata key="prediction" value="3600"/>'
           '<filament id="1" type="PLA" color="#FF0000" used_g="10.5"/><filament id="2" type="PETG" color="#00FF00" used_g="4.25"/>'
           '</plate></config>')
    use = filament.slice_usage(make_3mf(tmp_path / "m.gcode.3mf", xml))
    assert use["grams"] == 14.75 and [f["grams"] for f in use["filaments"]] == [10.5, 4.25]


def test_cost_arithmetic():
    # 75.22 g at 30/kg = 2.2566; 7440 s = 2.0667 h at 150 W (H2S) = 0.31 kWh at 0.40 = 0.124; total 2.3806
    assert filament.job_cost([75.22], [30], 7440, "H2S", 0.4) == 2.38
    assert filament.job_cost([10, 20], [20, 50], 0, "P1S", 0.4) == 1.2  # 0.2 + 1.0, no print time
    assert filament.job_cost([100], [0], 3600, "A1 mini", 1.0) == 0.08  # 80 W for one hour
    assert filament.job_cost([100], [0], 3600, "UNKNOWN", 1.0) == 0.1  # default watts
    assert filament.watts("p1s") == 105


def test_spool_validation_and_permissions(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL, role="owner")  # owner = admin of that printer
    body = {"brand": "B", "type": "PLA", "colour": "#ff0000", "grams_left": 800, "grams_total": 1000, "price_per_kg": 20,
            "printer": SERIAL, "unit": 0, "slot": 2}
    st, sp = hub.req("POST", "/api/spools", body, user=USER)
    assert st == 200 and sp["colour"] == "FF0000" and len(sp["id"]) == 12
    assert hub.req("POST", "/api/spools", dict(body, printer=SERIAL2), user=USER)[0] == 403
    assert hub.req("POST", "/api/spools", {"type": "PLA"}, user=USER)[0] == 403  # unassigned: HA admins only
    assert hub.req("POST", "/api/spools", dict(body, grams_left=-1), user=ADMIN)[0] == 400
    assert hub.req("POST", "/api/spools", dict(body, printer=SERIAL, slot=None), user=ADMIN)[0] == 400
    assert hub.req("PUT", "/api/spools/" + sp["id"], {"grams_left": 700}, user=USER)[1]["grams_left"] == 700
    assert hub.req("PUT", "/api/spools/" + sp["id"], {"grams_left": 5}, user=OTHER)[0] == 403
    assert [s["id"] for s in hub.req("GET", "/api/spools", user=USER)[1]] == [sp["id"]]
    assert hub.req("GET", "/api/spools", user=OTHER)[1] == []
    assert hub.req("DELETE", "/api/spools/" + sp["id"], user=USER)[0] == 200
    assert hub.req("DELETE", "/api/spools/" + sp["id"], user=ADMIN)[0] == 404


def finish_a_print(hub):
    """Queue the real slice on SERIAL, start it, and let the printer run through to finish."""
    hub.setup_printers()
    hub.grant(USER, SERIAL)
    jid = hub.sliced(owner=USER)
    job = hub.req("POST", "/api/jobs/%s/queue" % jid, {"target": SERIAL}, user=USER)[1]
    hub.ha.s["sensor.lab_h2s_print_status"]["state"] = "idle"
    assert hub.req("POST", "/api/printers/%s/start-next" % SERIAL, {"plate_clear": True}, user=ADMIN)[1]["state"] == "starting"
    hub.ha.s["sensor.lab_h2s_current_stage"]["state"] = "changing_filament"
    hub.app.tick_all()
    assert hub.req("GET", "/api/queue", user=ADMIN)[1]["jobs"][0]["state"] == "printing"
    hub.ha.s["sensor.lab_h2s_print_status"]["state"] = "finish"
    hub.app.tick_all()
    return job["id"]


def test_done_takes_grams_off_the_spool_and_prices_the_job(hub):
    spool = hub.app.spools.add(type="PETG", colour="ADB1B2", grams_left=500, grams_total=1000, price_per_kg=30,
                               printer=SERIAL, unit=0, slot=0)
    other = hub.app.spools.add(type="PETG", colour="ADB1B2", grams_left=900, printer=SERIAL, unit=0, slot=3)
    jid = finish_a_print(hub)
    done = hub.req("GET", "/api/queue", user=ADMIN)[1]["history"][0]
    assert done["id"] == jid and done["state"] == "done"
    assert done["cost"] == 2.38 and done["spools_used"] == [{"spool_id": spool["id"], "grams": 75.22}]
    assert hub.app.spools.get(spool["id"])["grams_left"] == 424.78
    assert hub.app.spools.get(other["id"])["grams_left"] == 900  # the slice named tray 0:0
    assert not os.path.exists(os.path.join(hub.s.SHARE, done["file"]))
    hub.app.tick_all()  # a later tick must not take the grams off again
    assert hub.app.spools.get(spool["id"])["grams_left"] == 424.78


def test_done_without_a_matching_spool_still_finishes(hub):
    finish_a_print(hub)
    done = hub.req("GET", "/api/queue", user=ADMIN)[1]["history"][0]
    assert done["state"] == "done" and done["spools_used"] == [] and done["cost"] == 0.12  # electricity only: 0.124 at 0.40
    spool = hub.app.spools.add(type="PETG", colour="ADB1B2", grams_left=10, printer=SERIAL, unit=0, slot=0)
    hub.app.settle(dict(done, file="gone.gcode.3mf"))
    assert hub.app.spools.get(spool["id"])["grams_left"] == 0  # never below zero


def test_failed_print_keeps_the_spool(hub):
    spool = hub.app.spools.add(type="PETG", colour="ADB1B2", grams_left=500, printer=SERIAL, unit=0, slot=0)
    hub.setup_printers()
    hub.grant(USER, SERIAL)
    jid = hub.sliced(owner=USER)
    hub.req("POST", "/api/jobs/%s/queue" % jid, {"target": SERIAL}, user=USER)
    hub.ha.s["sensor.lab_h2s_print_status"]["state"] = "idle"
    hub.req("POST", "/api/printers/%s/start-next" % SERIAL, {"plate_clear": True}, user=ADMIN)
    hub.ha.s["sensor.lab_h2s_current_stage"]["state"] = "changing_filament"
    hub.app.tick_all()
    hub.ha.s["sensor.lab_h2s_print_status"]["state"] = "failed"
    hub.app.tick_all()
    assert hub.app.spools.get(spool["id"])["grams_left"] == 500


def test_spool_without_price_is_allowed_and_costs_nothing(hub):
    hub.setup_printers()
    sp = hub.req("POST", "/api/spools", {"type": "PLA", "price_per_kg": None}, user=ADMIN)
    assert sp[0] == 200 and sp[1]["price_per_kg"] is None
    assert hub.req("PUT", "/api/spools/" + sp[1]["id"], {"price_per_kg": 12.5}, user=ADMIN)[1]["price_per_kg"] == 12.5
    assert hub.req("PUT", "/api/spools/" + sp[1]["id"], {"price_per_kg": None, "printer": None, "unit": None, "slot": None}, user=ADMIN)[0] == 200
    assert hub.req("PUT", "/api/spools/" + sp[1]["id"], {"price_per_kg": "x"}, user=ADMIN)[0] == 400
