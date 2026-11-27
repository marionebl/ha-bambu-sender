"""GET /api/events for the integration."""
from hubfakes import ADMIN, SERIAL, USER


def types(hub, since):
    return [(e["type"], e["serial"]) for e in hub.req("GET", "/api/events?since=%d" % since, secret=True)[1]["events"]]


def test_events_are_internal_only(hub):
    assert hub.req("GET", "/api/events", user=ADMIN)[0] == 403
    assert hub.req("GET", "/api/events")[0] == 401


def test_first_poll_has_no_backlog(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL)
    hub.req("POST", "/api/queue", {"file": "a.gcode.3mf", "target": SERIAL}, user=USER)
    st, body = hub.req("GET", "/api/events", secret=True)
    assert st == 200 and body["events"] == [] and body["seq"] == hub.app.seq
    assert hub.req("GET", "/api/events?since=x", secret=True)[0] == 400


def test_events_sequence_over_a_job_life(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL, needs_approval=True)
    start = hub.req("GET", "/api/events", secret=True)[1]["seq"]
    job = hub.req("POST", "/api/queue", {"file": "a.gcode.3mf", "target": SERIAL}, user=USER)[1]
    assert types(hub, start) == [("approval_needed", SERIAL), ("queue_changed", "")]
    ev = hub.req("GET", "/api/events?since=%d" % start, secret=True)[1]["events"][0]
    assert ev["job"]["id"] == job["id"] and ev["job"]["file"] == "a.gcode.3mf" and ev["job"]["owner_name"] == USER.upper()
    assert ev["job"]["target"] == SERIAL and ev["job"]["printer"] is None
    seq = hub.req("GET", "/api/events", secret=True)[1]["seq"]
    hub.req("POST", "/api/queue/%s/approve" % job["id"], {}, user=ADMIN)
    assert types(hub, seq) == [("queue_changed", "")]
    seq = hub.req("GET", "/api/events", secret=True)[1]["seq"]
    hub.ha.s["sensor.lab_h2s_print_status"]["state"] = "idle"
    hub.req("POST", "/api/printers/%s/start-next" % SERIAL, {"plate_clear": True}, user=ADMIN)
    assert types(hub, seq) == [("job_started", SERIAL), ("queue_changed", "")]
    seq = hub.req("GET", "/api/events", secret=True)[1]["seq"]
    hub.ha.s["sensor.lab_h2s_current_stage"]["state"] = "changing_filament"
    hub.app.tick_all()
    hub.ha.s["sensor.lab_h2s_print_status"]["state"] = "finish"
    hub.app.tick_all()
    assert types(hub, seq) == [("queue_changed", ""), ("job_done", SERIAL), ("queue_changed", "")]
    done = hub.req("GET", "/api/events?since=%d" % seq, secret=True)[1]["events"][1]["job"]
    assert done["state"] == "done" and done["printer"] == SERIAL


def test_failed_job_event_and_seq_grows(hub):
    hub.setup_printers()
    hub.req("POST", "/api/queue", {"file": "a.gcode.3mf", "target": SERIAL}, user=ADMIN)
    hub.ha.s["sensor.lab_h2s_print_status"]["state"] = "idle"
    hub.req("POST", "/api/printers/%s/start-next" % SERIAL, {"plate_clear": True}, user=ADMIN)
    seq = hub.req("GET", "/api/events", secret=True)[1]["seq"]
    hub.ha.s["sensor.lab_h2s_active_tray"]["state"] = "none"  # filament never loaded
    hub.ha.s["sensor.lab_h2s_current_stage"]["state"] = "idle"
    hub.now += 700  # past the start timeout
    hub.app.tick_all()
    assert types(hub, seq) == [("job_failed", SERIAL), ("queue_changed", "")]
    assert hub.req("GET", "/api/events?since=0", secret=True)[1]["seq"] > seq
