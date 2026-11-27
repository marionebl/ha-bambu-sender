import io
import zipfile

from custom_components.druckwerk.externals import EXAMPLE_GCODE_MODEL as MODEL_H2S
from custom_components.druckwerk.gcode_guard import check_gcode_3mf

HEAD = f"; printer_model = {MODEL_H2S}\n; EXECUTABLE_BLOCK_START\n"
LOAD = "M1002 gcode_claim_action : 4\nM620 S0A\nT0\nM621 S0A\n"


def check(tmp_path, body, model="H2S", use_ams=True):
    p = tmp_path / "x.gcode.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Metadata/plate_1.gcode", HEAD + body)
    return check_gcode_3mf(str(p), model, use_ams)


def test_good(tmp_path):
    assert check(tmp_path, LOAD + "M109 S245\nG1 X1 E5\n") == []


def test_generic_start_with_absolute_purge_like_the_real_failure(tmp_path):
    # Real failure: M109 then an absolute-E purge line, no AMS load, no stage markers.
    problems = check(tmp_path, "G92 E0\nM109 S205\nG1 X10.1 Y200 E15\n")
    assert len(problems) == 2 and "AMS filament load" in problems[0] and "gcode_claim_action" in problems[1]


def test_cold_nozzle(tmp_path):
    (p,) = check(tmp_path, LOAD + "M104 S140\nM104 S0\nG1 X1 E5\n")
    assert "not heated" in p


def test_t1000_and_slot_255_are_not_a_load(tmp_path):
    body = "M1002 gcode_claim_action : 4\nM104 S200\nT1000\nM620 S255A\nT255\nM621 S255A\nG1 X1 E5\n"
    assert any("AMS filament load" in p for p in check(tmp_path, body))


def test_retraction_and_comments_are_not_extrusion(tmp_path):
    body = LOAD + "M104 S200\nG1 E-2\n; G1 X1 E5\n"
    assert check(tmp_path, body) == ["The G-code has no extruding move."]


def test_model_names_and_not_a_3mf(tmp_path):
    assert check(tmp_path, LOAD + "M104 S200\nG1 E5\n", model=MODEL_H2S) == []
    assert "P1S" in check(tmp_path, LOAD + "M104 S200\nG1 E5\n", model="P1S")[0]
    bad = tmp_path / "n.gcode.3mf"
    bad.write_bytes(b"nope")
    assert "Cannot read" in check_gcode_3mf(str(bad), "H2S")[0]
