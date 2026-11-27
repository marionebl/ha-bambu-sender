"""The web UI files are served at / without identity; nothing else from hub/ui is."""
from hubfakes import ADMIN


def test_index_and_assets_load_without_identity(hub):
    st, body = hub.req("GET", "/")
    assert st == 200 and b"<title>Druckwerk</title>" in body and b'src="app.js"' in body
    for path in ("/index.html", "/style.css", "/app.js", "/api.js", "/util.js", "/views/queue.js", "/views/setup.js"):
        assert hub.req("GET", path)[0] == 200, path


def test_only_the_ui_files_are_served(hub):
    for path in ("/check_ui.py", "/mock_server.py", "/views/../app.py", "/views/x.py", "/..%2fapp.py"):
        assert hub.req("GET", path)[0] in (401, 404), path
    assert hub.req("GET", "/api/me")[0] == 401  # the API behind the page still needs identity
    assert hub.req("GET", "/api/me", user=ADMIN)[0] == 200
