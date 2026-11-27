"""Druckwerk app parts: discovery, migration, secret, queue todo, events, notifications."""

from datetime import timedelta

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from .conftest import HUB_SECRET, HUB_URL, PRINTER, SERIAL, URL, mock_addon

TODO = "todo.my_h2s_queue"
HUBHOST = "c9a833d4-druckwerk"
PERSON = "person.anna"
ANNA = "anna-user-id"


def job(jid, state="queued", target=SERIAL, owner="Anna", file="cube.gcode.3mf"):
    return {"id": jid, "file": file, "state": state, "target": target, "owner_name": owner, "owner_user_id": ANNA}


def mock_hub(mock, jobs=None, events=None, seq=1, grants=None, users=None):
    # The first matching mock wins, so drop the earlier hub mocks .
    mock._mocks = [m for m in mock._mocks if not (str(m.url).startswith(HUB_URL) and True)]
    mock.get(f"{HUB_URL}/api/events", json={"seq": seq, "events": events or []})
    mock.get(f"{HUB_URL}/api/queue", json={"jobs": jobs or [], "history": [], "printers": []})
    mock.get(f"{HUB_URL}/api/grants", json=grants if grants is not None else [])
    mock.get(f"{HUB_URL}/api/ha/users", json=users if users is not None else [{"id": ANNA, "name": "Anna", "is_admin": True}])
    mock.post(f"{HUB_URL}/api/queue/aaaaaaaaaaaa/cancel", json={})
    mock.post(f"{HUB_URL}/api/queue/aaaaaaaaaaaa/reorder", json={})
    mock.post(f"{HUB_URL}/api/printers/{SERIAL}/start-next", json={})


async def hub_setup(hass, options=None, **kw):
    mock = kw.pop("mock")
    mock_hub(mock, **kw)
    entry = MockConfigEntry(
        domain="druckwerk", unique_id="druckwerk", options=options or {},
        data={"url": URL, "hub_secret": HUB_SECRET},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def tick(hass):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()


def calls(mock, method, suffix):
    return [c for c in mock.mock_calls if c[0] == method and str(c[1]).endswith(suffix)]


# ---- discovery
async def test_flow_default_url_from_supervisor(hass, addon, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    addon.get("http://supervisor/addons", json={"data": {"addons": [
        {"slug": "core_ssh"}, {"slug": "c9a833d4_druckwerk"}]}})
    addon.get("http://supervisor/addons/c9a833d4_druckwerk/info", json={"data": {"hostname": HUBHOST}})
    r = await hass.config_entries.flow.async_init("druckwerk", context={"source": "user"})
    assert r["data_schema"]({}) == {"url": f"http://{HUBHOST}:8098"}


async def test_flow_default_without_supervisor_is_computed_hub(hass, addon, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    r = await hass.config_entries.flow.async_init("druckwerk", context={"source": "user"})
    assert r["data_schema"]({}) == {"url": f"http://{HUBHOST}:8098"}


# ---- options flow: hub secret
async def test_options_manual_secret_rejected_then_accepted(hass, addon, media, vendor_device):
    entry = MockConfigEntry(domain="druckwerk", unique_id="druckwerk", data={"url": URL})
    entry.add_to_hass(hass)
    addon.get(f"{HUB_URL}/api/events", status=401)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    bad = await hass.config_entries.options.async_configure(flow["flow_id"], {"hub_secret": "nope"})
    assert bad["errors"] == {"hub_secret": "invalid_hub_secret"}
    mock_hub(addon)
    ok = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"hub_secret": HUB_SECRET, "recipients": {PERSON: "notify.mobile_app_anna"}})
    assert ok["type"] == "create_entry"
    assert entry.data["hub_secret"] == HUB_SECRET and entry.options["recipients"] == {PERSON: "notify.mobile_app_anna"}


async def test_options_rejects_bad_recipients(hass, addon, media, vendor_device):
    entry = MockConfigEntry(domain="druckwerk", unique_id="druckwerk", data={"url": URL})
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(flow["flow_id"], {"recipients": {"light.x": "notify.y"}})
    assert r["errors"] == {"recipients": "bad_recipients"}


# ---- todo
async def test_todo_lists_waiting_jobs_for_this_printer(hass, addon, media, vendor_device):
    jobs = [job("aaaaaaaaaaaa"), job("bbbbbbbbbbbb", "pending_approval", owner="Bo"),
            job("cccccccccccc", "printing"), job("dddddddddddd", target="OTHER")]
    await hub_setup(hass, mock=addon, jobs=jobs)
    r = await hass.services.async_call("todo", "get_items", {"entity_id": TODO}, blocking=True, return_response=True)
    items = r[TODO]["items"]
    assert [i["uid"] for i in items] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]
    assert items[1]["description"].startswith("Owner: Bo")


async def test_todo_delete_calls_cancel_with_secret(hass, addon, media, vendor_device):
    await hub_setup(hass, mock=addon, jobs=[job("aaaaaaaaaaaa")])
    await hass.services.async_call("todo", "remove_item", {"entity_id": TODO, "item": "aaaaaaaaaaaa"}, blocking=True)
    c = calls(addon, "POST", "/api/queue/aaaaaaaaaaaa/cancel")
    assert len(c) == 1 and c[0][3]["X-Hub-Secret"] == HUB_SECRET


async def test_todo_move_calls_reorder(hass, addon, media, vendor_device):
    jobs = [job("bbbbbbbbbbbb"), job("cccccccccccc"), job("aaaaaaaaaaaa")]
    await hub_setup(hass, mock=addon, jobs=jobs)
    ent = hass.data["entity_components"]["todo"].get_entity(TODO)
    await ent.async_move_todo_item("aaaaaaaaaaaa", "bbbbbbbbbbbb")
    assert calls(addon, "POST", "/reorder")[0][2] == {"position": 1, "scope": SERIAL}
    await ent.async_move_todo_item("aaaaaaaaaaaa", None)
    assert calls(addon, "POST", "/reorder")[1][2] == {"position": 0, "scope": SERIAL}


async def test_todo_has_no_create(hass, addon, media, vendor_device):
    await hub_setup(hass, mock=addon)
    assert not hass.states.get(TODO).attributes["supported_features"] & 1


async def test_no_hub_secret_means_no_todo(hass, addon, media, vendor_device):
    from .conftest import setup_entry
    await setup_entry(hass)
    assert hass.states.get(TODO) is None


# ---- events and notifications
RECIP = {"person.anna": "notify.mobile_app_anna"}
DONE = {"seq": 2, "type": "job_done", "serial": SERIAL, "job": {"id": "zzzzzzzzzzzz", "file": "old.gcode.3mf"}}


async def test_events_fire_ha_events_and_backlog_is_not_replayed(hass, addon, media, vendor_device):
    seen = []
    hass.bus.async_listen("druckwerk_job_started", lambda e: seen.append(e.data))
    mock_hub(addon, events=[{"seq": 1, "type": "job_started", "serial": SERIAL, "job": {}}], seq=1)
    await hub_setup(hass, mock=addon, events=[{"seq": 1, "type": "job_started", "serial": SERIAL, "job": {}}])
    assert seen == []  # first poll only records the position
    ev = {"seq": 2, "type": "job_started", "serial": SERIAL, "job": {"id": "x"}}
    mock_hub(addon, events=[ev], seq=2)
    await tick(hass)
    assert seen and seen[0]["serial"] == SERIAL and seen[0]["job"] == {"id": "x"}
    assert any("since=1" in str(c[1]) for c in addon.mock_calls if c[0] == "GET")


async def test_job_done_notifies_admin_at_home_with_action(hass, addon, media, vendor_device):
    hass.states.async_set(PERSON, "home", {"user_id": ANNA})
    sent = []
    hass.services.async_register("notify", "mobile_app_anna", lambda c: sent.append(c.data))
    await hub_setup(hass, options={"recipients": RECIP}, mock=addon, jobs=[job("aaaaaaaaaaaa", file="next.gcode.3mf")])
    mock_hub(addon, jobs=[job("aaaaaaaaaaaa", file="next.gcode.3mf")], events=[DONE], seq=2)
    await tick(hass)
    assert len(sent) == 1
    assert sent[0]["message"] == "Plate clear? Start next: next.gcode.3mf"
    assert sent[0]["data"]["actions"][0]["action"] == f"DRUCKWERK_START_{SERIAL}"


async def test_notification_suppressed_when_person_not_home(hass, addon, media, vendor_device):
    hass.states.async_set(PERSON, "not_home", {"user_id": ANNA})
    sent = []
    hass.services.async_register("notify", "mobile_app_anna", lambda c: sent.append(c.data))
    await hub_setup(hass, options={"recipients": RECIP}, mock=addon)
    mock_hub(addon, jobs=[job("aaaaaaaaaaaa")], events=[DONE], seq=2)
    await tick(hass)
    assert sent == []


async def test_job_done_without_next_job_sends_nothing(hass, addon, media, vendor_device):
    hass.states.async_set(PERSON, "home", {"user_id": ANNA})
    sent = []
    hass.services.async_register("notify", "mobile_app_anna", lambda c: sent.append(c.data))
    await hub_setup(hass, options={"recipients": RECIP}, mock=addon)
    mock_hub(addon, jobs=[], events=[DONE], seq=2)
    await tick(hass)
    assert sent == []


async def test_approval_needed_goes_to_printer_admins_only(hass, addon, media, vendor_device):
    hass.states.async_set(PERSON, "home", {"user_id": ANNA})
    hass.states.async_set("person.bo", "home", {"user_id": "bo-id"})
    sent = []
    hass.services.async_register("notify", "mobile_app_anna", lambda c: sent.append(("anna", c.data)))
    hass.services.async_register("notify", "mobile_app_bo", lambda c: sent.append(("bo", c.data)))
    opts = {"recipients": {**RECIP, "person.bo": "notify.mobile_app_bo"}}
    await hub_setup(hass, options=opts, mock=addon)
    ev = {"seq": 2, "type": "approval_needed", "serial": SERIAL, "job": job("aaaaaaaaaaaa", "pending_approval", owner="Bo")}
    mock_hub(addon, jobs=[], events=[ev], seq=2, grants=[], users=[{"id": ANNA, "is_admin": True}, {"id": "bo-id", "is_admin": False}])
    await tick(hass)
    assert [w for w, _ in sent] == ["anna"] and "Bo" in sent[0][1]["message"]


async def test_action_event_calls_start_next_with_plate_clear(hass, addon, media, vendor_device):
    await hub_setup(hass, mock=addon)
    hass.bus.async_fire("mobile_app_notification_action", {"action": f"DRUCKWERK_START_{SERIAL}"})
    await hass.async_block_till_done()
    c = calls(addon, "POST", f"/api/printers/{SERIAL}/start-next")
    assert len(c) == 1 and c[0][2] == {"plate_clear": True} and c[0][3]["X-Hub-Secret"] == HUB_SECRET


async def test_other_actions_are_ignored(hass, addon, media, vendor_device):
    await hub_setup(hass, mock=addon)
    hass.bus.async_fire("mobile_app_notification_action", {"action": "SOMETHING_ELSE"})
    await hass.async_block_till_done()
    assert calls(addon, "POST", "/start-next") == []


async def test_hub_down_does_not_break_printer_entities(hass, addon, media, vendor_device):
    import aiohttp
    addon.get(f"{HUB_URL}/api/events", exc=aiohttp.ClientError)
    entry = MockConfigEntry(domain="druckwerk", unique_id="druckwerk",
                            data={"url": URL, "hub_secret": HUB_SECRET})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.my_h2s_print_file").state != "unavailable"


# ---- Supervisor discovery
def hassio_info(host=HUBHOST, secret=HUB_SECRET):
    from homeassistant.helpers.service_info.hassio import HassioServiceInfo
    cfg = {"host": host, "bridge_port": 8098, "hub_port": 8099, "secret": secret}
    return HassioServiceInfo(config=cfg, name="Druckwerk", slug="0990873a_druckwerk", uuid="abc")


async def test_discovery_creates_entry(hass, addon):
    r = await hass.config_entries.flow.async_init("druckwerk", context={"source": "hassio"}, data=hassio_info())
    assert r["type"] == "form" and r["step_id"] == "hassio_confirm"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] == "create_entry"
    assert r["data"] == {"url": f"http://{HUBHOST}:8098", "hub_url": f"http://{HUBHOST}:8099", "hub_secret": HUB_SECRET}


async def test_discovery_updates_existing_entry_and_keeps_entity_ids(hass, addon, media, vendor_device):
    from .conftest import setup_entry
    entry = await setup_entry(hass)
    reg = er.async_get(hass)
    ids = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    new = f"http://{HUBHOST}:8098"
    addon.get(f"{new}/api/health", json={"server": "ok"})
    addon.get(f"{new}/api/printers", json=[PRINTER])
    hub = f"http://{HUBHOST}:8099"
    for path, body in (("/api/events", {"seq": 1, "events": []}), ("/api/queue", {"jobs": []})):
        addon.get(hub + path, json=body)
    r = await hass.config_entries.flow.async_init("druckwerk", context={"source": "hassio"}, data=hassio_info(secret="n" * 32))
    assert r["type"] == "abort" and r["reason"] == "already_configured"
    await hass.async_block_till_done()
    assert entry.data == {"url": new, "hub_url": hub, "hub_secret": "n" * 32}
    assert entry.runtime_data.url == new and entry.runtime_data.hub.secret == "n" * 32  # reloaded
    after = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id) if e.unique_id in ids}
    assert after == ids and ids
