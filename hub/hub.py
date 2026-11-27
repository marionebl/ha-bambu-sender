#!/usr/bin/env python3
"""Hub app on the ingress port: the slicer server with the hub API (app.py) mounted on it.

HA ingress reaches one port only. It serves the web UI (hub/ui) at / and the API (app.py). The bridge keeps its own
unchanged routes on 8098 for the integration and stays reachable for HA admins under /api/bridge/.
"""
import json
import os
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I drops the script dir
import slicer_server as s
from app import App, log
from ha import HA

env = os.environ.get
BRIDGE = ("127.0.0.1", int(env("DW_PORT", "8098")))
SUPERVISOR = env("HUB_SUPERVISOR_URL", "http://supervisor")


def supervisor(path, body=None):
    req = urllib.request.Request(SUPERVISOR + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + env("SUPERVISOR_TOKEN", ""),
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def hostname():
    return env("HUB_HOSTNAME") or supervisor("/addons/self/info")["data"]["hostname"]


def main():
    ha = HA(s.CORE, env("HUB_HA_WS", "ws://supervisor/core/websocket"), env("SUPERVISOR_TOKEN", ""))
    app = App(ha, env("HUB_DATA", "/data"), s.SHARE, BRIDGE, s, hostname, int(env("SLICER_PORT", "8099")),
              ui_dir=env("HUB_UI", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")),
              trusted_ips=[i for i in env("HUB_INGRESS_IPS", "172.30.32.2").split(",") if i])
    app.install()

    stop = threading.Event()
    threading.Thread(target=app.run_ticker, args=(stop,), daemon=True).start()
    threading.Thread(target=ha.watch_state_changes, args=(app.on_state, stop), daemon=True).start()

    def announce():
        while not stop.is_set():
            try:
                supervisor("/discovery", app.discovery())
                return log("discovery sent")
            except Exception as e:  # noqa: BLE001 - Supervisor may not be ready yet
                log("discovery failed, retrying: %s" % e)
                stop.wait(30)
    threading.Thread(target=announce, daemon=True).start()
    s.main()


if __name__ == "__main__":
    main()
