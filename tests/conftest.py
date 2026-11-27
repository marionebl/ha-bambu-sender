import io
import zipfile

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"

# The plugin ships its own custom_components package that shadows ours; add our folder to it.
import pathlib
import custom_components

_ours = str(pathlib.Path(__file__).parents[1] / "custom_components")
if _ours not in list(custom_components.__path__):
    custom_components.__path__.append(_ours)

from custom_components.druckwerk.externals import (  # noqa: E402
    EXAMPLE_GCODE_MODEL as MODEL_H2S,
    VENDOR_DOMAIN,
    VENDOR_MANUFACTURER,
)

MODEL_P1S = MODEL_H2S.replace("H2S", "P1S")
URL = "http://local-druckwerk:8098"
SERIAL = "0000TEST0000001"
PRINTER = {
    "serial": SERIAL, "name": "H2S", "dev_product_name": "H2S", "dev_model_name": "O1S",
    "online": True, "print_status": "IDLE", "account": "a1", "email": "me@example.com", "live": {"cached": True},
}
HEALTH = {"server": "ok", "accounts": []}
HUB_URL = "http://local-druckwerk:8099"
HUB_SECRET = "s" * 32


def make_3mf(model=MODEL_H2S, ams=True, claims=True, png=None):
    """Tiny .gcode.3mf; ams=False/claims=False make it look like a generic (unsupported) start G-code."""
    start = ["M1002 gcode_claim_action : 4"] if claims else []
    start += ["M104 S140"]
    if ams:
        start += ["M620 S0A", "T0", "M621 S0A"]
    start += ["M109 S245", "M83", "G1 X10 Y10 E5"]
    gcode = "\n".join(
        ["; HEADER_BLOCK_START", "; HEADER_BLOCK_END", f"; printer_model = {model}", "; EXECUTABLE_BLOCK_START", *start]
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/plate_1.gcode", gcode)
        if png is not None:
            z.writestr("Metadata/plate_1.png", png)
    return buf.getvalue()


GOOD = make_3mf()


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations):
    yield


@pytest.fixture
def media(hass, tmp_path):
    """Temp media root with a druckwerk folder holding two files."""
    hass.config.media_dirs = {"local": str(tmp_path)}
    base = tmp_path / "druckwerk"
    (base / "sub").mkdir(parents=True)
    (base / "benchy.gcode.3mf").write_bytes(GOOD)
    (base / "sub" / "cube.gcode.3mf").write_bytes(GOOD)
    (base / "ignore.txt").write_text("x")
    (tmp_path / "secret.gcode.3mf").write_bytes(b"outside")
    return tmp_path


def mock_addon(mock, printers=None):
    mock.clear_requests()
    mock.get(f"{URL}/api/health", json=HEALTH)
    mock.get(f"{URL}/api/printers", json=[PRINTER] if printers is None else printers)


@pytest.fixture
def addon(aioclient_mock):
    mock_addon(aioclient_mock)
    return aioclient_mock


@pytest.fixture
def vendor_device(hass):
    """The device the vendor integration would have registered."""
    entry = MockConfigEntry(domain=VENDOR_DOMAIN)
    entry.add_to_hass(hass)
    from homeassistant.helpers import device_registry as dr
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(VENDOR_DOMAIN, SERIAL)},
        name="My H2S", manufacturer=VENDOR_MANUFACTURER, model="H2S",
    )


async def setup_entry(hass):
    entry = MockConfigEntry(domain="druckwerk", data={"url": URL}, unique_id="druckwerk")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
