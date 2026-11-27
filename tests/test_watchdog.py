"""Air-print watchdog: replays of the two real prints from 2026-11-22 (state sequences from HA history)."""

from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import SERIAL, URL, VENDOR_DOMAIN, setup_entry

FILE = "benchy.gcode.3mf"
IDS = {k: f"sensor.my_h2s_{k}" for k in ("print_status", "stage", "active_tray")}


async def setup(hass, addon, media, vendor_device):
    """Integration set up, with the vendor integration's three sensors registered (unique_id format: <serial>_<key>)."""
    reg = er.async_get(hass)
    for key, entity_id in IDS.items():
        reg.async_get_or_create(
            "sensor", VENDOR_DOMAIN, f"{SERIAL}_{key}", suggested_object_id=entity_id.split(".")[1],
            device_id=vendor_device.id, config_entry=hass.config_entries.async_entries(VENDOR_DOMAIN)[0])
    addon.post(f"{URL}/api/pause/{SERIAL}", json={"sent": 1})
    entry = await setup_entry(hass)
    set_states(hass, "idle", "idle", "none")
    return entry.runtime_data.watchdog


def set_states(hass, status=None, stage=None, tray=None):
    for key, value in (("print_status", status), ("stage", stage), ("active_tray", tray)):
        if value is not None:
            hass.states.async_set(IDS[key], value)


async def replay(hass, freezer, seconds, timeline=()):
    """Advance time in 1 s steps; timeline is [(second, status, stage, tray)] applied when reached."""
    pending = sorted(timeline, key=lambda e: e[0])
    for sec in range(1, seconds + 1):
        while pending and pending[0][0] <= sec:
            set_states(hass, *pending.pop(0)[1:])
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()


def pauses(addon):
    return [c for c in addon.mock_calls if c[0] == "POST" and str(c[1]).endswith("/api/pause/" + SERIAL)]


def notes(hass):
    from homeassistant.components.persistent_notification import _async_get_or_create_notifications

    return list(_async_get_or_create_notifications(hass).values())


# What the bad print looked like: running 0.5 s after prepare, stage idle and tray none for the whole hour.
BAD = [(0, "prepare", "idle", "none"), (1, "running")]


async def test_bad_print_is_paused_once_after_120s_with_notification(hass, addon, media, vendor_device, freezer):
    wd = await setup(hass, addon, media, vendor_device)
    wd.started(SERIAL, FILE)
    await replay(hass, freezer, 100, BAD)
    assert pauses(addon) == [] and notes(hass) == []  # still inside the 120 s grace
    await replay(hass, freezer, 30)
    assert len(pauses(addon)) == 1
    (note,) = notes(hass)
    assert "H2S" in note["message"] and FILE in note["message"] and "filament" in note["message"]
    set_states(hass, "pause")  # the printer now reports pause; nothing more may be sent
    await replay(hass, freezer, 600)
    assert len(pauses(addon)) == 1 and len(notes(hass)) == 1


async def test_good_print_is_not_paused(hass, addon, media, vendor_device, freezer):
    wd = await setup(hass, addon, media, vendor_device)
    wd.started(SERIAL, FILE)
    good = [
        (0, "prepare", "idle", "none"), (1, "running"),
        (20, None, "cooling_chamber"), (40, None, "homing_toolhead"),
        (60, None, "heatbed_surface_foreign_object_detection"),
        (110, None, "changing_filament"), (111, None, None, "Acme PLA Matte"),
        (140, None, "calibrating_extrusion"), (170, None, "cleaning_nozzle_tip"),
        (200, None, "auto_bed_leveling"), (260, None, "sweeping_xy_mech_mode"), (290, None, "printing"),
    ]
    await replay(hass, freezer, 900, good)
    assert pauses(addon) == [] and notes(hass) == []


async def test_slow_filament_load_still_idle_before_changing_filament_is_not_paused(hass, addon, media, vendor_device, freezer):
    wd = await setup(hass, addon, media, vendor_device)
    wd.started(SERIAL, FILE)
    # idle for 100 s, then the filament change; the tray appears 2 s later, then a long print
    await replay(hass, freezer, 700, BAD + [(100, None, "changing_filament"), (102, None, None, "Acme PLA Matte"),
                                            (103, None, "printing")])
    assert pauses(addon) == []


async def test_print_not_started_by_sender_is_not_paused(hass, addon, media, vendor_device, freezer):
    await setup(hass, addon, media, vendor_device)  # no started(): somebody printed from their own app
    await replay(hass, freezer, 600, BAD)
    assert pauses(addon) == [] and notes(hass) == []


async def test_watch_ends_when_print_leaves_running(hass, addon, media, vendor_device, freezer):
    wd = await setup(hass, addon, media, vendor_device)
    wd.started(SERIAL, FILE)
    await replay(hass, freezer, 60, BAD + [(60, "finish")])
    await replay(hass, freezer, 30, [(0, "running")])  # the next print is someone else's
    await replay(hass, freezer, 300)
    assert pauses(addon) == []


async def test_unavailable_tray_is_not_enough(hass, addon, media, vendor_device, freezer):
    wd = await setup(hass, addon, media, vendor_device)
    wd.started(SERIAL, FILE)
    await replay(hass, freezer, 400, [(0, "running", "printing", "unavailable")])
    assert pauses(addon) == []
