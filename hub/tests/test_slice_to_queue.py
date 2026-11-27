"""Slicer routes behind identity, and POST /api/jobs/{id}/queue taking grams and the plate image from the slice."""
import os

from hubfakes import ADMIN, OTHER, SERIAL, USER


def test_slicer_routes_need_identity_and_ownership(hub):
    jid = hub.sliced(owner=USER)
    assert hub.req("GET", "/api/jobs/" + jid)[0] == 401
    assert hub.req("GET", "/api/jobs/" + jid, user=USER)[1]["id"] == jid
    assert hub.req("GET", "/api/jobs/" + jid, user=OTHER)[0] == 403
    assert hub.req("GET", "/api/jobs/" + jid, user=ADMIN)[0] == 200
    assert hub.req("GET", "/api/jobs/%s/plate.png" % jid, user=USER)[1].startswith(b"\x89PNG")


def test_upload_records_the_owner(hub):
    st, job = hub.req("POST", "/api/jobs?name=cube.stl", raw=b"solid x", user=USER)
    assert st == 200 and job["owner_user_id"] == USER
    assert hub.s.jobs[job["id"]]["owner_user_id"] == USER
    assert hub.req("POST", "/api/jobs?name=cube.stl", raw=b"solid x")[0] == 401


def test_slice_needs_a_grant_on_the_printer(hub):
    hub.setup_printers()
    jid = hub.sliced(owner=USER)
    st, body = hub.req("POST", "/api/jobs/%s/slice" % jid, {"serial": SERIAL, "quality": "good", "tray": "0:0"}, user=USER)
    assert st == 403 and body["error"].startswith("rule:")


def test_direct_print_is_for_printer_admins(hub):
    hub.setup_printers()
    jid = hub.sliced(owner=USER)
    hub.grant(USER, SERIAL)
    assert hub.req("POST", "/api/jobs/%s/print" % jid, {"confirm": True}, user=USER)[0] == 403


def test_queue_from_slice(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL)
    jid = hub.sliced(owner=USER)
    st, job = hub.req("POST", "/api/jobs/%s/queue" % jid, {"target": SERIAL}, user=USER)
    assert st == 200, job
    assert job["state"] == "queued" and job["target"] == SERIAL and job["grams"] == 75.22 and job["filament_grams"] == [75.22]
    assert job["filaments"] == [{"type": "PETG", "colour": "ADB1B2"}] and job["print_seconds"] == 7440
    assert job["file"].endswith(".gcode.3mf") and os.path.isfile(os.path.join(hub.s.SHARE, job["file"]))
    assert job["plate_png"] == "api/queue/%s/plate.png" % job["id"]
    assert hub.req("GET", "/" + job["plate_png"], user=USER)[1].startswith(b"\x89PNG")
    assert hub.req("GET", "/" + job["plate_png"], user=OTHER)[0] == 404
    # the queue copy is its own file: two queue entries from one slice do not share a name
    other = hub.req("POST", "/api/jobs/%s/queue" % jid, {"target": SERIAL}, user=USER)[1]
    assert other["file"] != job["file"]


def test_queue_from_slice_refused_leaves_no_file(hub):
    hub.setup_printers()
    jid = hub.sliced(owner=USER)
    assert hub.req("POST", "/api/jobs/%s/queue" % jid, {"target": SERIAL}, user=USER)[0] == 403  # no grant
    assert not os.path.exists(hub.s.SHARE) or os.listdir(hub.s.SHARE) == []
    hub.grant(USER, SERIAL)
    assert hub.req("POST", "/api/jobs/%s/queue" % jid, {}, user=USER)[0] == 400
