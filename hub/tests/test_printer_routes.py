"""GET /api/printers, camera and cover proxies."""
from hubfakes import ADMIN, OTHER, SERIAL, SERIAL2, USER


def test_printer_view_from_ha_entities(hub):
    hub.setup_printers()
    st, plist = hub.req("GET", "/api/printers", user=ADMIN)
    assert st == 200 and [p["serial"] for p in plist] == [SERIAL, SERIAL2]
    p = plist[0]
    assert (p["name"], p["model"], p["role"], p["online"], p["status"], p["stage"]) == ("Lab", "H2S", "admin", True, "running", "printing")
    assert (p["progress"], p["remaining_min"], p["layer"], p["total_layers"]) == (42.0, 73, 10, 50)
    assert (p["nozzle_temp"], p["nozzle_target"], p["bed_temp"], p["bed_target"], p["chamber_temp"]) == (220.5, 220.0, 60.0, 60.0, 30.0)
    assert p["task_name"] == "cube" and p["end_time"].startswith("2026-11-27")
    assert p["active_tray"] == {"unit": 0, "slot": 1}
    assert p["hms"] == [{"code": "HMS_0300_0100_0001_0007", "severity": "serious", "text": "Bed sensor", "url": "https://wiki.example/x"}]
    assert p["camera_url"] == "api/printers/%s/camera" % SERIAL and p["cover_url"] == "api/printers/%s/cover" % SERIAL
    assert p["ams"] == [{"unit": 0, "humidity_index": 4, "temp": None, "trays": [
        {"slot": 0, "type": "PETG", "colour": "ADB1B2", "name": "PETG HF", "remain_pct": 80, "spool_id": None},
        {"slot": 1, "type": "PLA", "colour": "BB3D43", "name": "PLA Basic", "remain_pct": 55, "spool_id": None}]}]
    assert p["current_job"] is None and p["queue_length"] == 0 and p["external"] is None


def test_tray_gets_spool_id(hub):
    hub.setup_printers()
    spool = hub.app.spools.add(type="PETG", colour="ADB1B2", grams_left=900, printer=SERIAL, unit=0, slot=0)
    tray = hub.req("GET", "/api/printers", user=ADMIN)[1][0]["ams"][0]["trays"][0]
    assert tray["spool_id"] == spool["id"]


def test_printers_filtered_by_active_grant(hub):
    hub.setup_printers()
    assert hub.req("GET", "/api/printers", user=USER)[1] == []
    hub.grant(USER, SERIAL)
    st, plist = hub.req("GET", "/api/printers", user=USER)
    assert [(p["serial"], p["role"]) for p in plist] == [(SERIAL, "user")]
    hub.grant(OTHER, SERIAL, condition_entity="schedule.workday")
    assert len(hub.req("GET", "/api/printers", user=OTHER)[1]) == 1
    hub.ha.s["schedule.workday"]["state"] = "off"  # HA decides: the grant lapses
    assert hub.req("GET", "/api/printers", user=OTHER)[1] == []


def test_bridge_is_the_fallback_without_ha_entities(hub):
    hub.setup_printers()
    hub.app.registry.put("FAKE0003", name="New", model="P1S", account="a_x", prefix="nothing_yet")
    p = next(p for p in hub.req("GET", "/api/printers", user=ADMIN)[1] if p["serial"] == "FAKE0003")
    assert (p["status"], p["progress"], p["remaining_min"], p["layer"], p["task_name"]) == ("running", 7.0, 5, 2, "from bridge")
    assert p["cover_url"] is None and p["ams"] == []


def test_camera_and_cover_proxies_need_a_grant(hub):
    hub.setup_printers()
    assert hub.req("GET", "/api/printers/%s/camera" % SERIAL, user=USER)[0] == 403
    hub.grant(USER, SERIAL)
    st, body = hub.req("GET", "/api/printers/%s/camera?protocols=rtsp" % SERIAL, user=USER)
    assert st == 200 and body == b"FRAME:/api/camera/%s/live?protocols=rtsp" % SERIAL.encode()
    assert hub.req("GET", "/api/printers/%s/camera?mode=snapshot" % SERIAL, user=USER)[1].endswith(b"/snapshot")
    assert hub.req("GET", "/api/printers/%s/cover" % SERIAL, user=USER) == (200, b"JPEGDATA")
    assert hub.req("GET", "/api/printers/NOPE/camera", user=USER)[0] == 404
