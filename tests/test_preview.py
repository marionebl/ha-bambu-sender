"""Plate preview: slicer image first, G-code render when the slicer wrote none."""

import importlib.util
import io
import pathlib
import zipfile

from PIL import Image

_spec = importlib.util.spec_from_file_location(
    "preview", pathlib.Path(__file__).parent.parent / "custom_components/druckwerk/preview.py"
)
preview = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preview)

GCODE = """; HEADER
M83
G1 X10 Y10 F3000
; CHANGE_LAYER
; Z_HEIGHT: 0.2
G1 X10 Y10 E0
G1 X60 Y10 E2
G1 X60 Y40 E1
G2 X50 Y50 I-10 J0 E1
; CHANGE_LAYER
G1 X10 Y40 E2
"""


def _make(tmp_path, files):
    path = tmp_path / "p.gcode.3mf"
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return str(path)


def _png(colour):
    out = io.BytesIO()
    Image.new("RGB", (4, 4), colour).save(out, "PNG")
    return out.getvalue()


def test_slicer_image_wins(tmp_path):
    data = _png((1, 2, 3))
    path = _make(tmp_path, {"Metadata/plate_1.gcode": GCODE, "Metadata/plate_1.png": data})
    assert preview.plate_png(path) == data


def test_renders_gcode_without_image(tmp_path):
    path = _make(tmp_path, {"Metadata/plate_1.gcode": GCODE})
    png = preview.plate_png(path)
    img = Image.open(io.BytesIO(png))
    assert img.size == (640, 640)
    assert len(img.getcolors(maxcolors=100000)) > 1


def test_no_plate_or_bad_file(tmp_path):
    assert preview.plate_png(_make(tmp_path, {"Metadata/x.txt": "a"})) is None
    bad = tmp_path / "bad.3mf"
    bad.write_bytes(b"nope")
    assert preview.plate_png(str(bad)) is None
    assert preview.plate_png(str(tmp_path / "missing.3mf")) is None
