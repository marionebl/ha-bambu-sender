import re

import pytest

from queue_access import Access, Caller, Forbidden, Grants
from queue_routes import make_routes

PAST, FUTURE = "1970-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"
USERS = {"ha": {"name": "ha", "is_admin": True}, "u1": {"name": "u1", "is_admin": False},
         "u2": {"name": "u2", "is_admin": False}, "own": {"name": "own", "is_admin": False}}


def F(n):
    return "%s.gcode.3mf" % n


@pytest.fixture
def acc(make, fake, tmp_path):
    grants = Grants(make.registry, str(tmp_path), fake.get_state, lambda: fake.now)
    access = Access(make.registry, grants, USERS.get)
    make.job_allowed = access.job_allowed
    access.q, access.grants_, access.fake = make, grants, fake
    access.as_ = lambda uid: access.bind(make, Caller(uid, uid, uid.upper()))
    return access


def grant(acc, user, printer="S1", **kw):
    return acc.grants_.add(user, printer, **kw)


def titles(q):
    return [j["file"] for j in q.jobs]


def test_missing_identity_is_401_and_internal_flag_bypasses(make, acc):
    class H:
        headers = {}
        out = None

        def json(self):
            return {}

        def send(self, s, b):
            self.out = (s, b)
    route = [r for r in make_routes(make, acc) if r[0] == "GET" and r[1] == "/api/queue"][0]
    h = H()
    route[2](h, None, {})
    assert h.out[0] == 401
    h = H()
    make_routes(make, acc, lambda h: True)[0][2](h, None, {})
    assert h.out[0] == 200 and h.out[1]["printers"]  # internal sees every printer


def test_no_grant_cannot_write_and_sees_nothing(make, acc):
    make.add(F("x"), target="S1")
    with pytest.raises(Forbidden, match="active grant"):
        acc.as_("u1").add(F("a"), target="S1")
    assert acc.as_("u1").snapshot() == {"jobs": [], "history": [], "printers": []}


def test_user_adds_on_granted_printer_only(make, acc):
    grant(acc, "u1", "S1")
    job = acc.as_("u1").add(F("a"), target="S1")
    assert job["owner_user_id"] == "u1" and job["owner_name"] == "U1"
    with pytest.raises(Forbidden):
        acc.as_("u1").add(F("b"), target="S2")


def test_condition_entity_off_removes_write_access(acc, fake):
    grant(acc, "u1", condition_entity="input_boolean.owner_home")
    fake.states["input_boolean.owner_home"] = "on"
    acc.as_("u1").add(F("a"), target="S1")
    fake.states["input_boolean.owner_home"] = "off"
    with pytest.raises(Forbidden):
        acc.as_("u1").add(F("b"), target="S1")
    assert acc.as_("u1").snapshot()["jobs"] == []


def test_expired_grant_removes_access(acc):
    grant(acc, "u1", until=PAST)
    with pytest.raises(Forbidden):
        acc.as_("u1").add(F("a"), target="S1")
    grant(acc, "u2", until=FUTURE)
    acc.as_("u2").add(F("a"), target="S1")


def test_user_reorder_keeps_other_jobs_at_their_indices(make, acc):
    grant(acc, "u1")
    grant(acc, "u2")
    u1, u2 = acc.as_("u1"), acc.as_("u2")
    a = u1.add(F("a"), target="S1")
    b = u2.add(F("b"), target="S1")
    c = u1.add(F("c"), target="S1")
    d = u2.add(F("d"), target="S1")
    e = u1.add(F("e"), target="S1")
    u1.reorder(e["id"], 0)
    assert titles(make) == ["e.gcode.3mf", "b.gcode.3mf", "a.gcode.3mf", "d.gcode.3mf", "c.gcode.3mf"]
    assert [j["id"] for j in make.jobs if j["owner_user_id"] == "u2"] == [b["id"], d["id"]]


def test_user_cannot_get_ahead_of_another_users_job(make, acc):
    grant(acc, "u1")
    grant(acc, "u2")
    first = acc.as_("u2").add(F("first"), target="S1")
    acc.as_("u1").add(F("a"), target="S1")
    mine = acc.as_("u1").add(F("b"), target="S1")
    acc.as_("u1").reorder(mine["id"], 0)
    assert make.jobs[0]["id"] == first["id"]
    with pytest.raises(Forbidden, match="own jobs"):
        acc.as_("u1").reorder(first["id"], 5)


def test_user_cannot_cancel_others_but_can_cancel_own(make, acc):
    grant(acc, "u1")
    grant(acc, "u2")
    theirs = acc.as_("u2").add(F("t"), target="S1")
    mine = acc.as_("u1").add(F("m"), target="S1")
    with pytest.raises(Forbidden, match="own jobs"):
        acc.as_("u1").cancel(theirs["id"])
    acc.as_("u1").cancel(mine["id"])
    assert titles(make) == ["t.gcode.3mf"]


def test_user_cannot_skip_start_approve_move_retry(make, acc, fake):
    grant(acc, "u1")
    u = acc.as_("u1")
    job = u.add(F("a"), target="S1")
    for call in (lambda: u.skip(job["id"]), lambda: u.start("S1", True),
                 lambda: u.start("S1", True, job["id"]), lambda: u.approve(job["id"]),
                 lambda: u.reject(job["id"]), lambda: u.move(job["id"], "S3")):
        with pytest.raises(Forbidden, match="admin"):
            call()
    acc.as_("ha").cancel(job["id"])
    with pytest.raises(Forbidden, match="admin"):
        u.retry(job["id"])


def test_any_job_does_not_start_where_owners_grant_is_inactive(make, acc, fake):
    grant(acc, "u1", "S1", condition_entity="input_boolean.home")
    grant(acc, "u1", "S3")
    fake.states["input_boolean.home"] = "on"
    job = acc.as_("u1").add(F("a"), target="any", model="H2S")
    fake.states["input_boolean.home"] = "off"
    assert make.next_for("S1") is None
    assert make.next_for("S3")["id"] == job["id"]


def test_any_job_needs_a_grant_on_some_printer_of_the_model(acc):
    grant(acc, "u1", "S2")
    with pytest.raises(Forbidden):
        acc.as_("u1").add(F("a"), target="any", model="H2S")


def test_pending_approval_skipped_until_approved(make, acc):
    grant(acc, "u1", needs_approval=True)
    job = acc.as_("u1").add(F("a"), target="S1")
    assert job["state"] == "pending_approval"
    assert make.next_for("S1") is None
    acc.as_("ha").approve(job["id"])
    assert make.next_for("S1")["id"] == job["id"]


def test_pending_job_does_not_block_later_queued_job(make, acc):
    grant(acc, "u1", needs_approval=True)
    grant(acc, "u2")
    acc.as_("u1").add(F("a"), target="S1")
    ok = acc.as_("u2").add(F("b"), target="S1")
    assert make.next_for("S1")["id"] == ok["id"]


def test_reject_cancels_with_reason(make, acc):
    grant(acc, "u1", needs_approval=True)
    job = acc.as_("u1").add(F("a"), target="S1")
    acc.as_("ha").reject(job["id"])
    assert make.history[-1]["state"] == "cancelled" and make.history[-1]["error"] == "rejected"


def test_ha_admin_can_do_everything(make, acc, fake):
    ha = acc.as_("ha")
    a = ha.add(F("a"), target="S1")
    b = ha.add(F("b"), target="S1")
    c = ha.add(F("c"), target="S1")
    ha.reorder(c["id"], 0)
    ha.move(b["id"], "S3")
    ha.skip(c["id"])
    assert titles(make) == ["a.gcode.3mf", "b.gcode.3mf", "c.gcode.3mf"]
    ha.cancel(b["id"])
    ha.retry(b["id"])
    assert ha.start("S1", plate_clear=True, job_id=c["id"])["id"] == c["id"]
    assert len(ha.snapshot()["printers"]) == 3
    assert ha.grants_list() == []


def test_owner_is_admin_on_own_printer_only(make, acc):
    grant(acc, "own", "S1", role="owner")
    o = acc.as_("own")
    job = o.add(F("a"), target="S1")
    o.skip(job["id"])
    elsewhere = acc.as_("ha").add(F("b"), target="S2")
    with pytest.raises(Forbidden):
        o.cancel(elsewhere["id"])
    with pytest.raises(Forbidden, match="grants"):
        o.grants_list()


def test_start_next_is_admin_only_and_needs_plate_clear(make, acc):
    grant(acc, "u1")
    acc.as_("ha").add(F("a"), target="S1")
    with pytest.raises(Forbidden):
        acc.as_("u1").start("S1", plate_clear=True)
    assert acc.as_("ha").start("S1", plate_clear=True)["state"] == "starting"


def test_view_shows_owner_names_for_granted_printers(acc):
    grant(acc, "u1", "S1")
    acc.as_("u1").add(F("a"), target="S1")
    acc.as_("ha").add(F("b"), target="S2")
    snap = acc.as_("u1").snapshot()
    assert [j["owner_name"] for j in snap["jobs"]] == ["U1"] and [p["serial"] for p in snap["printers"]] == ["S1"]


def test_grants_routes_admin_only(make, acc):
    class H:
        def __init__(self, uid, body=None):
            self.headers, self.body, self.out = {"X-Remote-User-Id": uid}, body or {}, None

        def json(self):
            return self.body

        def send(self, s, b):
            self.out = (s, b)

    def call(uid, method, path, body=None):
        h = H(uid, body)
        for meth, rx, fn in make_routes(make, acc):
            m = re.fullmatch(rx, path)
            if meth == method and m:
                fn(h, m, {})
                return h.out
    assert call("u1", "GET", "/api/grants")[0] == 403
    st, g = call("ha", "POST", "/api/grants", {"user_id": "u1", "printer": "S1", "role": "user"})
    assert st == 200
    assert call("u1", "POST", "/api/grants", {"user_id": "u1", "printer": "S1"})[0] == 403
    assert call("u1", "DELETE", "/api/grants/" + g["id"])[0] == 403
    assert call("ha", "DELETE", "/api/grants/" + g["id"])[0] == 200
    assert call("ha", "GET", "/api/grants")[1] == []
