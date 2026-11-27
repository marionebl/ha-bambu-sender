"""Setup wizard routes: bridge proxying, prefix suggestion from the device registry, printer config, integration secret."""
from hubfakes import ADMIN, SERIAL, SERIAL2, FakeBridge


def test_setup_lists_accounts_and_suggests_prefix_from_device_registry(hub):
    st, body = hub.req("GET", "/api/setup", user=ADMIN)
    assert st == 200
    assert body["accounts"] == [{"id": "a_x", "email": "a@example.invalid", "logged_in": True, "daemon_running": True}]
    by = {p["serial"]: p for p in body["printers"]}
    # The serial's device has an entity registered as sensor.lab_h2s_print_status: the prefix is read from it, not guessed.
    assert by[SERIAL]["suggested_prefix"] == "lab_h2s"
    assert by[SERIAL2]["suggested_prefix"] is None
    assert by[SERIAL]["model"] == "H2S" and by[SERIAL]["device_name"] == "Lab" and by[SERIAL]["configured"] is False


def test_suggested_prefix_falls_back_to_device_name(hub):
    hub.ha.entities = []
    prefix = hub.req("GET", "/api/setup", user=ADMIN)[1]["printers"][0]["suggested_prefix"]
    assert prefix == "lab_h2s"  # name_by_user "Lab H2S" slugged


def test_login_is_proxied_to_the_bridge(hub):
    st, body = hub.req("POST", "/api/setup/login/start", {"email": "a@example.invalid", "password": "pw"}, user=ADMIN)
    assert (st, body["state"]) == (200, "need_code")
    assert hub.req("POST", "/api/setup/login/code", {"email": "a@example.invalid", "code": "123456"}, user=ADMIN)[1]["state"] == "done"
    assert FakeBridge.log[0] == ("/api/login/start", {"email": "a@example.invalid", "password": "pw"})
    assert hub.req("DELETE", "/api/setup/accounts/a_x", user=ADMIN)[1] == {"deleted": "a_x"}


def test_put_and_delete_printer(hub):
    body = {"name": "Lab", "ha_prefix": "lab_h2s", "plate": "High Temp Plate", "nozzle": "0.4",
            "pause_entities": ["automation.snap"], "electricity_kwh_price": 0.3}
    st, p = hub.req("PUT", "/api/setup/printers/" + SERIAL, body, user=ADMIN)
    assert st == 200 and p["model"] == "H2S" and p["ha_prefix"] == "lab_h2s" and p["account"] == "a_x"
    assert hub.app.registry.get(SERIAL)["electricity_kwh_price"] == 0.3
    st, setup = hub.req("GET", "/api/setup", user=ADMIN)
    assert setup["printers"][0]["configured"] and setup["printers"][0]["config"]["plate"] == "High Temp Plate"
    assert hub.req("DELETE", "/api/setup/printers/" + SERIAL, user=ADMIN)[0] == 200
    assert hub.req("DELETE", "/api/setup/printers/" + SERIAL, user=ADMIN)[0] == 404


def test_put_printer_rejects_unknown_prefix_and_serial(hub):
    st, body = hub.req("PUT", "/api/setup/printers/" + SERIAL, {"name": "x", "ha_prefix": "nope"}, user=ADMIN)
    assert st == 400 and "sensor.nope_print_status" in body["error"]
    assert hub.req("PUT", "/api/setup/printers/" + SERIAL, {"name": "x"}, user=ADMIN)[0] == 400
    assert hub.req("PUT", "/api/setup/printers/NOPE", {"name": "x", "ha_prefix": "lab_h2s"}, user=ADMIN)[0] == 404


def test_integration_secret_and_discovery(hub):
    st, body = hub.req("GET", "/api/integration", user=ADMIN)
    assert st == 200 and body["url"] == "http://hub-host:8099" and len(body["secret"]) >= 32
    assert body["secret"] == hub.app.secret
    d = hub.app.discovery()
    assert d == {"service": "druckwerk", "config": {"host": "hub-host", "bridge_port": hub.bridge.server_address[1],
                                                       "hub_port": 8099, "secret": body["secret"]}}
