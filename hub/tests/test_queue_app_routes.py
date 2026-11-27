"""Queue routes through the real server: access, scoped reorder, start."""
import os

from hubfakes import ADMIN, OTHER, SERIAL, SERIAL2, USER


def add(hub, user, target=SERIAL, name="a.gcode.3mf"):
    return hub.req("POST", "/api/queue", {"file": name, "target": target}, user=user)


def test_adding_needs_an_active_grant(hub):
    hub.setup_printers()
    st, body = add(hub, USER)
    assert st == 403 and body["error"].startswith("rule:")
    hub.grant(USER, SERIAL)
    st, job = add(hub, USER)
    assert st == 200 and job["state"] == "queued" and job["owner_user_id"] == USER and job["owner_name"] == USER.upper()


def test_approval_flow(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL, needs_approval=True)
    job = add(hub, USER)[1]
    assert job["state"] == "pending_approval"
    assert hub.req("POST", "/api/queue/%s/approve" % job["id"], {}, user=USER)[0] == 403
    assert hub.req("POST", "/api/queue/%s/approve" % job["id"], {}, user=ADMIN)[1]["state"] == "queued"


def test_users_see_only_their_printers_jobs(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL)
    add(hub, ADMIN, SERIAL)
    add(hub, ADMIN, SERIAL2, "b.gcode.3mf")
    snap = hub.req("GET", "/api/queue", user=USER)[1]
    assert [j["file"] for j in snap["jobs"]] == ["a.gcode.3mf"] and [p["serial"] for p in snap["printers"]] == [SERIAL]


def test_reorder_with_scope(hub):
    hub.setup_printers()
    ids = [add(hub, ADMIN, t, "%s.gcode.3mf" % n)[1]["id"] for n, t in (("a1", SERIAL), ("b1", SERIAL2), ("a2", SERIAL), ("b2", SERIAL2), ("a3", SERIAL))]

    def order():
        return [j["file"][:2] for j in hub.req("GET", "/api/queue", user=ADMIN)[1]["jobs"]]
    assert order() == ["a1", "b1", "a2", "b2", "a3"]
    # position 0 among SERIAL's own waiting jobs: a3 goes first of the a-slots, the b jobs keep their slots
    assert hub.req("POST", "/api/queue/%s/reorder" % ids[4], {"position": 0, "scope": SERIAL}, user=ADMIN)[0] == 200
    assert order() == ["a3", "b1", "a1", "b2", "a2"]
    # without scope the position is global
    hub.req("POST", "/api/queue/%s/reorder" % ids[4], {"position": 4}, user=ADMIN)
    assert order()[-1] == "a3"
    assert hub.req("POST", "/api/queue/%s/reorder" % ids[4], {"position": 0, "scope": SERIAL2}, user=ADMIN)[0] == 409  # not in scope
    assert hub.req("POST", "/api/queue/%s/reorder" % ids[4], {"position": 0, "scope": 5}, user=ADMIN)[0] == 400


def test_user_reorder_stays_inside_own_slots_with_scope(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL)
    a = add(hub, USER, SERIAL, "u1.gcode.3mf")[1]["id"]
    add(hub, ADMIN, SERIAL, "x.gcode.3mf")
    b = add(hub, USER, SERIAL, "u2.gcode.3mf")[1]["id"]
    assert hub.req("POST", "/api/queue/%s/reorder" % b, {"position": 0, "scope": SERIAL}, user=USER)[0] == 200
    assert [j["file"][:2] for j in hub.req("GET", "/api/queue", user=ADMIN)[1]["jobs"]] == ["u2", "x.", "u1"]
    assert a and hub.req("POST", "/api/queue/%s/cancel" % a, {}, user=OTHER)[0] in (403, 404)


def test_start_needs_plate_clear_and_printer_admin(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL)
    add(hub, USER)
    hub.ha.s["sensor.lab_h2s_print_status"]["state"] = "idle"
    assert hub.req("POST", "/api/printers/%s/start-next" % SERIAL, {"plate_clear": True}, user=USER)[0] == 403
    assert hub.req("POST", "/api/printers/%s/start-next" % SERIAL, {}, user=ADMIN)[0] == 409
    st, job = hub.req("POST", "/api/printers/%s/start-next" % SERIAL, {"plate_clear": True}, user=ADMIN)
    assert st == 200 and job["state"] == "starting"
    domain, service, data = hub.ha.calls[-1]
    # The queue holds a bare name; the integration needs the path inside /share.
    assert (domain, service, data["file"], data["force"]) == ("druckwerk", "print_file", os.path.join(hub.s.SHARE, "a.gcode.3mf"), True)
