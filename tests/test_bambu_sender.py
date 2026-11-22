import base64
from datetime import timedelta

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import PRINTER, SERIAL, URL, mock_addon, setup_entry

SELECT = "select.my_h2s_print_file"
BUTTON = "button.my_h2s_start_cloud_print"
SENSOR = "sensor.my_h2s_cloud_sender"
TIMELAPSE = "switch.my_h2s_print_timelapse"
BED = "switch.my_h2s_print_bed_leveling"


def posts(mock):
    return [c for c in mock.mock_calls if c[0] == "POST"]


async def press(hass):
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)


async def choose(hass, option):
    await hass.services.async_call(
        "select", "select_option", {"entity_id": SELECT, "option": option}, blocking=True
    )


# ---- config flow
async def test_flow_ok(hass, addon):
    r = await hass.config_entries.flow.async_init("bambu_sender", context={"source": config_entries.SOURCE_USER})
    assert r["type"] == "form"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": URL + "/"})
    assert r["type"] == "create_entry" and r["data"] == {"url": URL}
    r2 = await hass.config_entries.flow.async_init("bambu_sender", context={"source": config_entries.SOURCE_USER})
    assert r2["type"] == "abort"  # single instance


async def test_flow_cannot_connect(hass, aioclient_mock):
    aioclient_mock.get(f"{URL}/api/health", exc=aiohttp.ClientError)
    r = await hass.config_entries.flow.async_init("bambu_sender", context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"url": URL})
    assert r["type"] == "form" and r["errors"] == {"base": "cannot_connect"}


# ---- setup / device attachment
async def test_entities_attach_to_existing_device(hass, addon, media, bambu_device):
    await setup_entry(hass)
    ents = er.async_entries_for_device(er.async_get(hass), bambu_device.id)
    assert {e.domain for e in ents if e.platform == "bambu_sender"} == {"select", "switch", "button", "sensor", "camera", "light"}
    assert len([e for e in ents if e.platform == "bambu_sender"]) == 11
    assert {e.device_id for e in ents} == {bambu_device.id}
    devs = list(dr.async_get(hass).devices)
    assert len(devs) == 1 and devs[0].name == "My H2S"  # no second device, no rename
    assert (media / "bambu_sender").is_dir()


async def test_creates_usable_device_without_bambu_lab(hass, addon, media):
    await setup_entry(hass)
    dev = next(iter(dr.async_get(hass).async_get_devices(identifiers={("bambu_lab", SERIAL)})), None)
    assert dev and dev.manufacturer == "Bambu Lab" and dev.model == "H2S" and dev.name == "H2S"


async def test_select_lists_files_and_new_printer_appears(hass, addon, media, bambu_device):
    await setup_entry(hass)
    st = hass.states.get(SELECT)
    assert st.attributes["options"] == ["benchy.gcode.3mf", "sub/cube.gcode.3mf"]
    (media / "bambu_sender" / "new.gcode.3mf").write_bytes(b"n")
    mock_addon(addon, [PRINTER, {**PRINTER, "serial": "NEW1", "name": "P1S"}])
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "new.gcode.3mf" in hass.states.get(SELECT).attributes["options"]
    assert len(er.async_entries_for_config_entry(er.async_get(hass), hass.config_entries.async_entries("bambu_sender")[0].entry_id)) == 22


# ---- button
async def test_button_sends_exact_bytes_and_options(hass, addon, media, bambu_device):
    await setup_entry(hass)
    addon.post(f"{URL}/api/print", json={"result": "sent", "return_code": -1, "print_result": -999})
    await choose(hass, "sub/cube.gcode.3mf")
    await hass.services.async_call("switch", "turn_on", {"entity_id": TIMELAPSE}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": BED}, blocking=True)
    await press(hass)
    (call,) = posts(addon)
    assert call[2] == {
        "serial": SERIAL, "filename": "cube.gcode.3mf",
        "file_b64": base64.b64encode(b"cube-bytes").decode(),
        "timelapse": True, "bed_leveling": False, "use_ams": True,
    }
    s = hass.states.get(SENSOR)
    assert s.state == "sent" and s.attributes["last_file"] == "cube.gcode.3mf"
    assert s.attributes["account_email"] == "me@example.com" and s.attributes["last_error"] is None


async def test_button_binary_roundtrip_defaults(hass, addon, media, bambu_device):
    await setup_entry(hass)
    addon.post(f"{URL}/api/print", json={"result": "success"})
    await choose(hass, "benchy.gcode.3mf")
    await press(hass)
    p = posts(addon)[0][2]
    assert base64.b64decode(p["file_b64"]) == b"PK\x00\x01binary\xff\xfe"
    assert p["timelapse"] is False and p["bed_leveling"] is True


@pytest.mark.parametrize("status", ["RUNNING", "PAUSE"])
async def test_running_guard_blocks_button_and_force_overrides(hass, addon, media, bambu_device, status):
    await setup_entry(hass)
    mock_addon(addon, [{**PRINTER, "print_status": status}])
    addon.post(f"{URL}/api/print", json={"result": "sent"})
    await choose(hass, "benchy.gcode.3mf")
    with pytest.raises(HomeAssistantError, match=status):
        await press(hass)
    assert posts(addon) == []
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("bambu_sender", "print_file",
            {"serial": SERIAL, "file": "benchy.gcode.3mf"}, blocking=True, return_response=True)
    assert posts(addon) == []
    res = await hass.services.async_call("bambu_sender", "print_file",
        {"serial": SERIAL, "file": "benchy.gcode.3mf", "force": True}, blocking=True, return_response=True)
    assert res["result"] == "sent" and len(posts(addon)) == 1


async def test_addon_error_message_raised(hass, addon, media, bambu_device):
    await setup_entry(hass)
    addon.post(f"{URL}/api/print", status=502, json={"error": "bridge exploded"})
    await choose(hass, "benchy.gcode.3mf")
    with pytest.raises(HomeAssistantError, match="bridge exploded"):
        await press(hass)
    s = hass.states.get(SENSOR)
    assert s.state == "error" and s.attributes["last_error"] == "bridge exploded"


async def test_button_needs_selection(hass, addon, media, bambu_device):
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError, match="Select"):
        await press(hass)


# ---- action
async def test_action_by_device_id_and_options(hass, addon, media, bambu_device):
    await setup_entry(hass)
    addon.post(f"{URL}/api/print", json={"result": "sent", "return_code": -1})
    res = await hass.services.async_call("bambu_sender", "print_file",
        {"device_id": bambu_device.id, "file": str(media / "bambu_sender" / "benchy.gcode.3mf"),
         "timelapse": True, "bed_leveling": False, "use_ams": False},
        blocking=True, return_response=True)
    assert res == {"result": "sent", "return_code": -1}
    p = posts(addon)[0][2]
    assert (p["serial"], p["timelapse"], p["bed_leveling"], p["use_ams"]) == (SERIAL, True, False, False)
    assert base64.b64decode(p["file_b64"]) == b"PK\x00\x01binary\xff\xfe"


@pytest.mark.parametrize("bad", [
    "../secret.gcode.3mf", "sub/../../secret.gcode.3mf", "/etc/passwd",
    "/root/x.gcode.3mf", "benchy.txt", "missing.gcode.3mf",
])
async def test_path_traversal_rejected(hass, addon, media, bambu_device, bad):
    await setup_entry(hass)
    addon.post(f"{URL}/api/print", json={"result": "sent"})
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("bambu_sender", "print_file",
            {"serial": SERIAL, "file": bad}, blocking=True)
    assert posts(addon) == []


async def test_symlink_escape_rejected(hass, addon, media, bambu_device):
    (media / "bambu_sender" / "link.gcode.3mf").symlink_to(media / "secret.gcode.3mf")
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("bambu_sender", "print_file",
            {"serial": SERIAL, "file": "link.gcode.3mf"}, blocking=True)


async def test_action_needs_device_or_serial(hass, addon, media, bambu_device):
    await setup_entry(hass)
    with pytest.raises(Exception):
        await hass.services.async_call("bambu_sender", "print_file", {"file": "benchy.gcode.3mf"}, blocking=True)


# ---- add-on down
async def test_addon_down_makes_entities_unavailable(hass, addon, media, bambu_device):
    await setup_entry(hass)
    assert hass.states.get(SELECT).state != STATE_UNAVAILABLE
    addon.clear_requests()
    addon.get(f"{URL}/api/health", exc=aiohttp.ClientError)
    addon.get(f"{URL}/api/printers", exc=aiohttp.ClientError)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done(wait_background_tasks=True)
    for e in (SELECT, BUTTON, SENSOR, TIMELAPSE, BED):
        assert hass.states.get(e).state == STATE_UNAVAILABLE
    mock_addon(addon)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(SENSOR).state == "idle"


async def test_setup_retries_when_addon_down(hass, aioclient_mock, media):
    aioclient_mock.get(f"{URL}/api/printers", exc=aiohttp.ClientError)
    aioclient_mock.get(f"{URL}/api/health", exc=aiohttp.ClientError)
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    e = MockConfigEntry(domain="bambu_sender", data={"url": URL}, unique_id="bambu_sender")
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    assert e.state is config_entries.ConfigEntryState.SETUP_RETRY


async def test_restore_selection_and_switches(hass, addon, media, bambu_device):
    from pytest_homeassistant_custom_component.common import mock_restore_cache
    from homeassistant.core import State
    mock_restore_cache(hass, [State(SELECT, "sub/cube.gcode.3mf"), State(TIMELAPSE, "on"), State(BED, "off"),
                              ])
    # entity ids only exist after first registration; register them first
    entry = await setup_entry(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    mock_restore_cache(hass, [State(SELECT, "sub/cube.gcode.3mf"), State(TIMELAPSE, "on"), State(BED, "off")])
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SELECT).state == "sub/cube.gcode.3mf"
    assert hass.states.get(TIMELAPSE).state == "on" and hass.states.get(BED).state == "off"


# ---- camera
CAMERA = "camera.my_h2s_cloud_camera"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


async def test_camera_serves_snapshot_and_caches(hass, addon, media, bambu_device):
    from homeassistant.components.camera import async_get_image
    addon.get(f"{URL}/api/camera/{SERIAL}/snapshot", content=JPEG, headers={"Content-Type": "image/jpeg"})
    await setup_entry(hass)
    img = await async_get_image(hass, CAMERA)
    assert img.content == JPEG
    await async_get_image(hass, CAMERA)
    snaps = [c for c in addon.mock_calls if str(c[1]).endswith("/snapshot")]
    assert len(snaps) == 1  # second view within CAMERA_MAX_AGE uses the cache


async def test_camera_can_stream_from_the_addon_live_route(hass, addon, media, bambu_device):
    from homeassistant.components.camera import CameraEntityFeature, get_camera_from_entity_id
    await setup_entry(hass)
    cam = get_camera_from_entity_id(hass, CAMERA)
    assert cam.supported_features & CameraEntityFeature.STREAM
    assert await cam.stream_source() == f"{URL}/api/camera/{SERIAL}/live"


async def test_camera_error_is_reported(hass, addon, media, bambu_device):
    from homeassistant.components.camera import async_get_image
    addon.get(f"{URL}/api/camera/{SERIAL}/snapshot", status=502, json={"error": "timeout after 20s"})
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, CAMERA)
    await hass.async_block_till_done()
    assert "timeout after 20s" in hass.states.get(CAMERA).attributes["last_error"]


async def test_camera_does_not_serve_stale_frame(hass, addon, media, bambu_device):
    from homeassistant.components.camera import async_get_image
    addon.get(f"{URL}/api/camera/{SERIAL}/snapshot", content=JPEG, headers={"Content-Type": "image/jpeg"})
    await setup_entry(hass)
    await async_get_image(hass, CAMERA)
    from homeassistant.components.camera import get_camera_from_entity_id
    get_camera_from_entity_id(hass, CAMERA)._fetched -= 120  # age the frame; patching time would freeze the loop
    addon.clear_requests()
    mock_addon(addon)
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
    import asyncio

    async def slow(method, url, data):  # a real request suspends; HA starts the fetch task eagerly
        await asyncio.sleep(0.01)
        return AiohttpClientMockResponse(method, url, response=JPEG + b"new", headers={"Content-Type": "image/jpeg"})

    addon.get(f"{URL}/api/camera/{SERIAL}/snapshot", side_effect=slow)
    img = await async_get_image(hass, CAMERA)
    assert img.content == JPEG + b"new"  # waited for the new frame instead of the 2-minute-old one


# ---- AMS re-read and chamber light go through the add-on (ha-bambulab's commands are blocked)
async def test_reread_slot_button_posts_rfid(hass, addon, media, bambu_device):
    addon.post(f"{URL}/api/rfid/{SERIAL}", json={"sent": 1})
    await setup_entry(hass)
    await hass.services.async_call("button", "press", {"entity_id": "button.my_h2s_re_read_slot_3"}, blocking=True)
    (call,) = [c for c in addon.mock_calls if str(c[1]).endswith("/api/rfid/" + SERIAL)]
    assert call[2] == {"ams_id": 0, "slot_id": 2}


async def test_reread_error_surfaces(hass, addon, media, bambu_device):
    addon.post(f"{URL}/api/rfid/{SERIAL}", status=502, json={"error": "send_message returned -2"})
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError, match="send_message returned -2"):
        await hass.services.async_call("button", "press", {"entity_id": "button.my_h2s_re_read_slot_1"}, blocking=True)


async def test_chamber_light_sends_and_mirrors_bambulab_state(hass, addon, media, bambu_device):
    from homeassistant.helpers import entity_registry as er
    bl = er.async_get(hass).async_get_or_create(
        "light", "bambu_lab", f"{SERIAL}_chamber_light", device_id=bambu_device.id,
        config_entry=hass.config_entries.async_entries("bambu_lab")[0])
    hass.states.async_set(bl.entity_id, "off")
    addon.post(f"{URL}/api/light/{SERIAL}", json={"sent": 2})
    await setup_entry(hass)
    ours = "light.my_h2s_chamber_light_cloud"
    assert hass.states.get(ours).state == "off"
    await hass.services.async_call("light", "turn_on", {"entity_id": ours}, blocking=True)
    (call,) = [c for c in addon.mock_calls if str(c[1]).endswith("/api/light/" + SERIAL)]
    assert call[2] == {"on": True}
    hass.states.async_set(bl.entity_id, "on")  # the printer reports the light on
    await hass.async_block_till_done()
    assert hass.states.get(ours).state == "on"


# ---- PUT /api/bambu_sender/upload/<name>: secret-guarded drop into the print file list
import io
import zipfile

SECRET = "s" * 32
UPLOAD = "/api/bambu_sender/upload/"


def sliced_3mf(gcode=b"G28\n"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/plate_1.gcode", gcode)
        z.writestr("3D/3dmodel.model", "<model/>")
    return buf.getvalue()


async def upload_setup(hass, hass_client_no_auth, secret=SECRET):
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(entry, options={"upload_secret": secret} if secret else {})
    return await hass_client_no_auth()


async def test_upload_stores_file_and_select_lists_it(hass, addon, media, bambu_device, hass_client_no_auth, tmp_path):
    import hashlib
    client = await upload_setup(hass, hass_client_no_auth)
    body = sliced_3mf()
    resp = await client.put(UPLOAD + "plate review.gcode.3mf", data=body, headers={"X-Bambu-Sender-Secret": SECRET})
    assert resp.status == 200, await resp.text()
    assert (await resp.json())["sha256"] == hashlib.sha256(body).hexdigest()
    assert (tmp_path / "bambu_sender" / "plate review.gcode.3mf").read_bytes() == body
    await hass.async_block_till_done()
    assert "plate review.gcode.3mf" in hass.states.get(SELECT).attributes["options"]
    # same name again needs overwrite=1
    resp = await client.put(UPLOAD + "plate review.gcode.3mf", data=body, headers={"X-Bambu-Sender-Secret": SECRET})
    assert resp.status == 409
    resp = await client.put(UPLOAD + "plate review.gcode.3mf?overwrite=1", data=body, headers={"X-Bambu-Sender-Secret": SECRET})
    assert resp.status == 200


async def test_upload_rejects_bad_secret_name_and_content(hass, addon, media, bambu_device, hass_client_no_auth, tmp_path):
    client = await upload_setup(hass, hass_client_no_auth)
    ok = {"X-Bambu-Sender-Secret": SECRET}
    assert (await client.put(UPLOAD + "a.gcode.3mf", data=sliced_3mf())).status == 401
    assert (await client.put(UPLOAD + "a.gcode.3mf", data=sliced_3mf(), headers={"X-Bambu-Sender-Secret": "x" * 32})).status == 401
    assert (await client.put(UPLOAD + "..%2Fescape.gcode.3mf", data=sliced_3mf(), headers=ok)).status in (400, 404)
    assert (await client.put(UPLOAD + "a.stl", data=sliced_3mf(), headers=ok)).status == 400
    assert (await client.put(UPLOAD + "a.gcode.3mf", data=b"not a zip", headers=ok)).status == 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")  # unsliced project
    assert (await client.put(UPLOAD + "a.gcode.3mf", data=buf.getvalue(), headers=ok)).status == 400
    assert not (tmp_path / "bambu_sender" / "a.gcode.3mf").exists()
    assert not [p for p in tmp_path.rglob("*.part")]  # temp files cleaned up


async def test_upload_off_without_secret(hass, addon, media, bambu_device, hass_client_no_auth):
    client = await upload_setup(hass, hass_client_no_auth, secret=None)
    resp = await client.put(UPLOAD + "a.gcode.3mf", data=sliced_3mf(), headers={"X-Bambu-Sender-Secret": ""})
    assert resp.status == 403


async def test_options_flow_sets_secret(hass, addon, media, bambu_device):
    entry = await setup_entry(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    short = await hass.config_entries.options.async_configure(flow["flow_id"], {"upload_secret": "short"})
    assert short["errors"] == {"upload_secret": "secret_too_short"}
    done = await hass.config_entries.options.async_configure(flow["flow_id"], {"upload_secret": SECRET})
    assert done["type"] == "create_entry"
    assert entry.options == {"upload_secret": SECRET}
