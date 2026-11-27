"""plugin.py against a local HTTP server: hash check, extraction of only the five libs, retry, placement."""
import hashlib
import http.server
import io
import os
import sys
import threading
import time
import zipfile

import pytest

for _d in ("..", "../.."):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), _d))
import plugin  # noqa: E402


def make_zip(names=plugin.LIBS, extra=("readme.txt", "sub/other.so")):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in (*names, *extra):
            z.writestr(n, ("content of " + n).encode())
    return buf.getvalue()


@pytest.fixture
def cdn():
    state = {"body": make_zip(), "hits": 0, "status": 200}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"] += 1
            self.send_response(state["status"])
            self.end_headers()
            if state["status"] == 200:
                self.wfile.write(state["body"])

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = "http://127.0.0.1:%d/p.zip" % srv.server_address[1]
    yield state
    srv.shutdown()


def start(request, tmp_path, monkeypatch, cdn, sha=None, **kw):
    monkeypatch.setattr(plugin, "URL", cdn["url"])
    monkeypatch.setattr(plugin, "SHA256", sha or hashlib.sha256(cdn["body"]).hexdigest())
    p = plugin.Plugin(str(tmp_path / "data"), str(tmp_path / "lib"), backoff_min=0.05, backoff_max=0.2, **kw)
    request.addfinalizer(p.stop)
    return p


def wait(p, state, t=5):
    end = time.time() + t
    while p.state != state and time.time() < end:
        time.sleep(0.02)
    assert p.state == state, p.status()


def test_installs_only_the_five_libs(request, tmp_path, monkeypatch, cdn):
    p = start(request, tmp_path, monkeypatch, cdn)
    assert p.ready.wait(5)
    assert sorted(os.listdir(p.dir)) == sorted(plugin.LIBS)
    assert sorted(os.listdir(tmp_path / "lib")) == sorted(plugin.LIBS)
    assert (tmp_path / "lib" / "liblive555.so").read_bytes() == b"content of liblive555.so"
    assert p.status()["state"] == "ready" and p.status()["error"] is None


def test_wrong_hash_installs_nothing_and_keeps_retrying(request, tmp_path, monkeypatch, cdn):
    p = start(request, tmp_path, monkeypatch, cdn, sha="0" * 64)
    wait(p, "failed")
    time.sleep(0.5)
    assert "SHA-256 mismatch" in p.status()["error"] and p.attempt >= 2 and cdn["hits"] >= 2
    assert not p.ready.is_set()
    assert not os.path.exists(p.dir) and not (tmp_path / "lib").exists()
    assert [n for n in os.listdir(tmp_path / "data" / "plugin") if n.startswith(".part")] == []


def test_recovers_when_the_cdn_comes_back(request, tmp_path, monkeypatch, cdn):
    cdn["status"] = 503
    p = start(request, tmp_path, monkeypatch, cdn)
    wait(p, "failed")
    assert "503" in p.status()["error"]
    cdn["status"] = 200
    assert p.ready.wait(5)


def test_zip_missing_a_lib_is_rejected(request, tmp_path, monkeypatch, cdn):
    cdn["body"] = make_zip(names=plugin.LIBS[:4])
    p = start(request, tmp_path, monkeypatch, cdn)
    wait(p, "failed")
    assert "liblive555.so" in p.status()["error"] and not os.path.exists(p.dir)


def test_existing_install_is_not_downloaded_again(request, tmp_path, monkeypatch, cdn):
    assert start(request, tmp_path, monkeypatch, cdn).ready.wait(5)
    hits = cdn["hits"]
    again = start(request, tmp_path, monkeypatch, cdn)
    assert again.ready.wait(5) and cdn["hits"] == hits


def test_placement_is_repaired_from_data(request, tmp_path, monkeypatch, cdn):
    assert start(request, tmp_path, monkeypatch, cdn).ready.wait(5)
    (tmp_path / "lib" / plugin.LIBS[1]).write_bytes(b"stale")
    again = start(request, tmp_path, monkeypatch, cdn)
    assert again.ready.wait(5)
    assert (tmp_path / "lib" / plugin.LIBS[1]).read_bytes() == b"content of " + plugin.LIBS[1].encode()


def test_host_links(request, tmp_path, monkeypatch, cdn):
    (tmp_path / "host").mkdir()
    p = start(request, tmp_path, monkeypatch, cdn, host_link_dir=str(tmp_path / "host"))
    assert p.ready.wait(5)
    assert sorted(os.listdir(tmp_path / "host")) == sorted(plugin.HOST_LINKED)
    assert os.path.realpath(tmp_path / "host" / plugin.HOST_LINKED[0]) == os.path.realpath(tmp_path / "lib" / plugin.HOST_LINKED[0])


def test_pinned_hash_looks_like_sha256():
    assert len(plugin.SHA256) == 64 and int(plugin.SHA256, 16) >= 0
