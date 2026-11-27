"""End-to-end test of the slicer app's HTTP API with a fake slicer, Bambu Sender and HA core."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config><plate><metadata key="index" value="1"/><metadata key="prediction" value="844"/><metadata key="weight" value="3.72"/>
<object identify_id="1" name="cube20" skipped="false"/>
<filament id="1" type="PETG" color="#ADB1B2" used_m="1.2" used_g="3.72"/></plate></config>"""
GCODE = "; CHANGE_LAYER\n; Z_HEIGHT: 0.2\n; FEATURE: Outer wall\nG1 X10 Y10\nG1 X30 Y10 E1\nG1 X30 Y30 E1\n"


def minimal_slice(path):
    """A .gcode.3mf with only the parts the app reads, like the cube20 slice of Bambu Studio 2.9.2."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Metadata/slice_info.config", SLICE_INFO)
        z.writestr("Metadata/plate_1.gcode", GCODE)
        z.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\n")
    return str(path)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base, method, path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture
def app(tmp_path):
    result = os.environ.get("FAKE_RESULT") or minimal_slice(tmp_path / "result.gcode.3mf")
    engine = tmp_path / "engine"
    (engine / "presets").mkdir(parents=True)
    (engine / "v1").mkdir()
    (engine / "version.json").write_text('{"tag": "v1"}')
    (engine / "v1" / "engine-st.wasm").write_bytes(b"\0asm\1\0\0\0")
    (engine / "v1" / "host.js").write_text("// host")
    (engine / "presets" / "H2S.json").write_text(json.dumps({
        "model": "H2S", "machine": {"name": "Bambu Lab H2S 0.4 nozzle", "printer_model": "Bambu Lab H2S"},
        "process": {q: {"name": "proc " + q} for q in ("good", "better", "best")},
        "filaments": [{"name": "Bambu PETG HF @BBL H2S", "id": "GFG02", "type": "PETG", "n": 1, "config": {"name": "Bambu PETG HF @BBL H2S"}}]}))
    mport, sport = free_port(), free_port()
    mocks = subprocess.Popen([sys.executable, os.path.join(HERE, "mocks.py"), str(mport)])
    env = dict(os.environ, SLICER_JOBS=str(tmp_path / "jobs"), SLICER_SHARE=str(tmp_path / "share"),
               SLICER_SENDER_URL="http://127.0.0.1:%d" % mport, SLICER_CORE_URL="http://127.0.0.1:%d/core" % mport,
               SLICER_SLICE_CMD=os.path.join(HERE, "fake_slice.sh"), SLICER_BS=os.path.join(HERE, "fake_bs.py"),
               SLICER_PORT=str(sport), SLICER_ENGINE=str(engine), SUPERVISOR_TOKEN="tok", FAKE_RESULT=result)
    server = subprocess.Popen([sys.executable, os.path.join(APP, "server.py")], env=env)
    base = "http://127.0.0.1:%d" % sport
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/api/printers", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield base, "http://127.0.0.1:%d" % mport, tmp_path, result
    server.kill()
    mocks.kill()


def wait_done(base, jid):
    for _ in range(100):
        job = json.loads(call(base, "GET", "/api/jobs/" + jid)[1])
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("slice did not finish")


def test_printers_lists_supported_models_with_loaded_trays(app):
    base = app[0]
    status, raw = call(base, "GET", "/api/printers")
    data = json.loads(raw)
    assert status == 200
    assert [p["model"] for p in data["printers"]] == ["H2S", "P1S"]  # X1C has no profile table
    h2s, p1s = data["printers"]
    assert [t["name"] for t in h2s["trays"]] == ["Bambu PETG HF", "Bambu PLA Matte"]  # from HA's tray sensors
    assert [t["name"] for t in p1s["trays"]] == ["PETG HF", "PLA Matte"]  # Bambu Sender AMS; empty slot 3 left out
    assert p1s["trays"][0]["colour"] == "#ADB1B2"


def test_upload_slice_preview_print(app):
    base, mock, tmp, result = app
    status, raw = call(base, "POST", "/api/jobs?name=Fan%20test.stl", raw=b"solid x\nendsolid x\n")
    assert status == 200, raw
    jid = json.loads(raw)["id"]
    status, raw = call(base, "POST", "/api/jobs/%s/slice" % jid, {"serial": "H2S0001", "quality": "best", "tray": "0:0"})
    assert status == 202, raw
    job = wait_done(base, jid)
    assert job["state"] == "done", job
    assert job["info"]["seconds"] > 0 and job["info"]["grams"] > 0
    got = open(tmp / "jobs" / jid / "slice-env.txt").read().split("\n")
    assert got[:5] == ["PRINTER=Bambu Lab H2S 0.4 nozzle", "PROCESS=0.16mm High Quality @BBL H2S",
                       "BED=High Temp Plate", "COLOURS=#ADB1B2", "FILAMENT=Bambu PETG HF @BBL H2S"]
    status, gcode = call(base, "GET", "/api/jobs/%s/plate.gcode" % jid)
    assert status == 200 and b"G1 " in gcode
    # No print without the confirm flag.
    status, raw = call(base, "POST", "/api/jobs/%s/print" % jid, {})
    assert status == 400
    assert json.loads(call(mock, "GET", "/calls")[1]) == []
    status, raw = call(base, "POST", "/api/jobs/%s/print" % jid, {"confirm": True})
    assert status == 200, raw
    calls = json.loads(call(mock, "GET", "/calls")[1])
    assert len(calls) == 1
    assert calls[0]["path"] == "/core/services/bambu_sender/print_file?return_response"
    assert calls[0]["auth"] == "Bearer tok"
    sent = calls[0]["body"]
    assert sent["serial"] == "H2S0001" and sent["force"] is False and sent["use_ams"] is True
    assert sent["file"] == str(tmp / "share" / "Fan test_best_h2s.gcode.3mf")
    assert os.path.getsize(sent["file"]) == os.path.getsize(result)


def test_slice_rejects_unknown_tray_and_quality(app):
    base = app[0]
    jid = json.loads(call(base, "POST", "/api/jobs?name=a.stl", raw=b"x")[1])["id"]
    assert call(base, "POST", "/api/jobs/%s/slice" % jid, {"serial": "H2S0001", "quality": "better", "tray": "0:2"})[0] == 400
    assert call(base, "POST", "/api/jobs/%s/slice" % jid, {"serial": "H2S0001", "quality": "ultra", "tray": "0:0"})[0] == 400
    assert call(base, "POST", "/api/jobs?name=a.exe", raw=b"x")[0] == 400


def test_engine_files_have_types_cache_and_isolation_headers(app):
    base = app[0]
    for path, ctype, cache in [("/engine/v1/engine-st.wasm", "application/wasm", "public, max-age=31536000, immutable"),
                               ("/engine/v1/host.js", "text/javascript", "public, max-age=31536000, immutable"),
                               ("/engine/version.json", "application/json", "no-cache"),
                               ("/engine/presets/H2S.json", "application/json", "no-cache"), ("/", "text/html; charset=utf-8", "no-store")]:
        with urllib.request.urlopen(base + path) as r:
            assert r.headers["Content-Type"] == ctype, path
            assert r.headers["Cache-Control"] == cache, path
            assert r.headers["Cross-Origin-Opener-Policy"] == "same-origin"
            assert r.headers["Cross-Origin-Embedder-Policy"] == "require-corp"
            assert r.headers["Document-Isolation-Policy"] == "isolate-and-require-corp"
    assert call(base, "GET", "/engine/../server.py")[0] == 404
    assert call(base, "GET", "/engine/v1/missing")[0] == 404
    assert call(base, "GET", "/api/printers")[0] == 200


def test_browser_gcode_becomes_a_normal_job(app):
    base, mock, tmp, _ = app
    jid = json.loads(call(base, "POST", "/api/jobs?name=Fan.stl", raw=b"x")[1])["id"]
    q = "serial=H2S0001&quality=better&tray=0:0&bed=High+Temp+Plate&filament=Bambu+PETG+HF+%40BBL+H2S&seconds=844&grams=3.7&metres=1.2&engine=mt&slice_s=9.9"
    status, raw = call(base, "POST", "/api/jobs/%s/gcode?%s" % (jid, q), raw=GCODE.encode())
    assert status == 200, raw
    job = json.loads(raw)
    assert job["state"] == "done" and job["info"]["seconds"] == 844 and job["info"]["grams"] == 3.7
    assert job["settings"]["filament"] == "Bambu PETG HF @BBL H2S" and "mt engine" in job["log"]
    status, gcode = call(base, "GET", "/api/jobs/%s/plate.gcode" % jid)
    assert status == 200 and gcode == GCODE.encode()
    with zipfile.ZipFile(tmp / "jobs" / jid / "out" / "Fan.gcode.3mf") as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
        assert cfg["curr_bed_type"] == "High Temp Plate" and cfg["filament_settings_id"] == ["Bambu PETG HF @BBL H2S"]
        assert 'printer_model_id" value="O1S"' in z.read("Metadata/slice_info.config").decode()
    assert call(base, "POST", "/api/jobs/%s/print" % jid, {})[0] == 400  # printing still needs the confirm flag
    assert json.loads(call(mock, "GET", "/calls")[1]) == []


def test_browser_gcode_rejects_unknown_filament_and_tray(app):
    base = app[0]
    jid = json.loads(call(base, "POST", "/api/jobs?name=a.stl", raw=b"x")[1])["id"]
    q = "serial=H2S0001&quality=better&tray=0:0&seconds=1&grams=1&filament="
    assert call(base, "POST", "/api/jobs/%s/gcode?%snope" % (jid, q), raw=b"G1 X1")[0] == 400
    assert call(base, "POST", "/api/jobs/%s/gcode?%sx&tray=0:2" % (jid, q.replace("tray=0:0", "tray=0:2")), raw=b"G1")[0] == 400
