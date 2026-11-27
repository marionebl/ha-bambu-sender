"""Identity and role rules on the app routes."""
from hubfakes import ADMIN, OTHER, SERIAL, SERIAL2, USER


def test_me_without_identity_is_401(hub):
    assert hub.req("GET", "/api/me")[0] == 401
    assert hub.req("GET", "/api/printers")[0] == 401
    assert hub.req("GET", "/api/queue")[0] == 401


def test_identity_headers_from_other_peers_are_ignored(hub):
    hub.app.trusted_ips = {"172.30.32.2"}  # a request from anywhere else cannot vouch for a user
    assert hub.req("GET", "/api/me", user=ADMIN)[0] == 401


def test_me_shape_for_admin_and_user(hub):
    hub.setup_printers()
    hub.grant(USER, SERIAL, role="user")
    st, me = hub.req("GET", "/api/me", user=ADMIN)
    assert st == 200 and me["is_admin"] and me["setup_done"] and me["user_id"] == ADMIN
    assert {p["serial"]: p["role"] for p in me["printers"]} == {SERIAL: "admin", SERIAL2: "admin"}
    st, me = hub.req("GET", "/api/me", user=USER)
    assert not me["is_admin"] and me["printers"] == [{"serial": SERIAL, "role": "user"}]
    assert hub.req("GET", "/api/me", user=OTHER)[1]["printers"] == []


def test_setup_not_done_without_printers(hub):
    assert hub.req("GET", "/api/me", user=USER)[1]["setup_done"] is False


def test_user_gets_403_on_admin_routes(hub):
    hub.setup_printers()
    for method, path in [("GET", "/api/setup"), ("GET", "/api/integration"), ("GET", "/api/grants"), ("GET", "/api/ha/users"),
                         ("GET", "/api/ha/entities"), ("DELETE", "/api/setup/printers/" + SERIAL),
                         ("PUT", "/api/setup/printers/" + SERIAL), ("POST", "/api/setup/login/start"),
                         ("GET", "/api/bridge/api/health")]:
        st, body = hub.req(method, path, {} if method != "GET" else None, user=USER)
        assert st == 403 and body["error"].startswith("rule:"), (method, path, st, body)


def test_grants_and_ha_users_accept_internal_calls(hub):
    hub.setup_printers()
    assert hub.req("GET", "/api/grants", secret=True)[0] == 200
    st, users = hub.req("GET", "/api/ha/users", secret=True)
    assert st == 200 and {u["id"]: u["is_admin"] for u in users} == {ADMIN: True, USER: False, OTHER: False}  # system user hidden
    assert hub.req("GET", "/api/grants", headers={"X-Hub-Secret": "wrong"})[0] == 401


def test_grants_crud_by_admin(hub):
    hub.setup_printers()
    st, g = hub.req("POST", "/api/grants", {"user_id": USER, "printer": SERIAL, "role": "user"}, user=ADMIN)
    assert st == 200 and hub.req("GET", "/api/grants", user=ADMIN)[1][0]["id"] == g["id"]
    assert hub.req("DELETE", "/api/grants/" + g["id"], user=ADMIN)[0] == 200


def test_ha_entities_filter(hub):
    st, ents = hub.req("GET", "/api/ha/entities?domains=schedule,light", user=ADMIN)
    assert st == 200 and [e["entity_id"] for e in ents] == ["light.lamp", "schedule.workday"]
    assert ents[1] == {"entity_id": "schedule.workday", "name": "Workday", "state": "on"}
    assert all(e["entity_id"].split(".")[0] in ("schedule", "input_boolean", "binary_sensor", "person")
               for e in hub.req("GET", "/api/ha/entities", user=ADMIN)[1])


def test_me_works_while_ha_is_unreachable(hub):
    from ha import HAError

    def down(*a, **k):
        raise HAError("down")
    hub.ha.command = down
    st, me = hub.req("GET", "/api/me", user=ADMIN)
    assert st == 200 and me["is_admin"] is False  # unknown users never get admin rights


def test_grants_carry_user_name_and_active(hub):
    hub.setup_printers()
    hub.ha.s["schedule.workday"]["state"] = "off"
    g = hub.req("POST", "/api/grants", {"user_id": USER, "printer": SERIAL, "condition_entity": "schedule.workday"}, user=ADMIN)[1]
    assert (g["user_name"], g["active"]) == ("Ben", False)
    hub.ha.s["schedule.workday"]["state"] = "on"
    assert hub.req("GET", "/api/grants", user=ADMIN)[1][0]["active"] is True
