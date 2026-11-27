#!/usr/bin/env python3
"""Walk every screen against the mock server as admin and user. Fails on any console error, page error,
failed request, or request outside the fake ingress prefix (absolute paths break behind HA ingress).

  python3 hub/ui/check_ui.py [--shots DIR] [--inject-absolute]
--inject-absolute fires one deliberate fetch('/api/me') to show the check catches it."""
import argparse
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = "/api/hassio_ingress/MOCKTOKEN/"
SIZES = {"390x844": (390, 844), "1440x900": (1440, 900)}
H2S = "FAKE-H2S-0001"


def walk(page, ident, base, shot, inject):
    def go(h, wait):
        page.evaluate("h => { location.hash = h }", h)
        page.wait_for_selector(wait, timeout=8000)
        page.wait_for_timeout(250)

    page.goto(f"{base}/?as={ident}")
    if ident == "admin-new":
        page.wait_for_selector(".wizard h1")
        for i, name in enumerate(["setup-1-account", "setup-2-printers", "setup-3-done"], 1):
            if i > 1:
                page.get_by_role("button", name="Next").first.click()
                page.wait_for_timeout(400)
            page.wait_for_selector(".wizard .card")
            shot(name)
        return
    page.wait_for_selector(".pcard")
    if inject:
        page.evaluate("fetch('/api/me')")
        page.wait_for_timeout(300)
    shot("printers")
    go("#/printers/" + H2S, ".cam.big")
    shot("printer-detail")
    if ident == "admin":
        go("#/printers/FAKE-P1S-0002", ".cam.big")
        btn = page.get_by_role("button", name="Start “")
        assert btn.is_disabled(), "start must be disabled without the checkbox"
        page.locator("#plate-clear").check()
        assert not btn.is_disabled()
        shot("printer-idle-start-armed")
    if ident == "admin":
        go("#/printers/FAKE-X1C-0003", ".hms")
        shot("printer-offline-hms")
    go("#/queue", ".job")
    shot("queue")
    page.get_by_role("button", name="History").click()
    page.wait_for_selector(".job")
    shot("queue-history")
    # new print: upload, pick printer + tray, slice 3 variants, pick
    go("#/new", ".drop")
    page.set_input_files("input[type=file]", files=[{"name": "demo_part.stl", "mimeType": "model/stl", "buffer": b"solid x\nendsolid x\n"}])
    page.get_by_role("button", name="Workshop H2S").click()
    page.locator(".chip", has_text="AMS 1·1").click()
    shot("new-print-ready")
    page.get_by_role("button", name="Slice good, better and best").click()
    page.wait_for_selector(".variant[aria-pressed=true]", timeout=15000)
    page.wait_for_selector("canvas.layer")
    page.wait_for_function("document.querySelector('canvas.layer ~ small, .stack small') && /Layer \\d+ of/.test(document.body.innerText)", timeout=8000)
    shot("new-print-compare")
    page.get_by_role("button", name="Add Better to queue").click()
    page.wait_for_selector(".job")
    shot("queue-after-add")
    go("#/filament", ".spool")
    shot("filament")
    if ident == "admin":
        page.get_by_role("button", name="Edit").first.click()
        page.wait_for_selector("dialog[open]")
        shot("filament-edit")
        page.keyboard.press("Escape")
        go("#/access", "table")
        shot("access")
        page.get_by_role("button", name="Add grant")  # form present
        go("#/setup", ".wizard .card")
        shot("setup-menu-reopen")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots")
    ap.add_argument("--inject-absolute", action="store_true")
    a = ap.parse_args()
    port = 18099
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_server.py"), str(port)], stdout=subprocess.DEVNULL)
    time.sleep(0.8)
    base, problems, count = f"http://127.0.0.1:{port}", [], 0
    if a.shots:
        os.makedirs(a.shots, exist_ok=True)
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            for ident in ("admin", "user", "admin-new"):
                for sz, (w, h) in SIZES.items():
                    ctx = br.new_context(viewport={"width": w, "height": h}, is_mobile=w < 500, has_touch=w < 500)
                    page = ctx.new_page()
                    where = f"{ident}@{sz}"
                    page.on("console", lambda m, wh=where: m.type == "error" and problems.append(f"console error [{wh}]: {m.text}"))
                    page.on("pageerror", lambda e, wh=where: problems.append(f"page error [{wh}]: {e}"))
                    page.on("requestfailed", lambda r, wh=where: problems.append(f"request failed [{wh}]: {r.url}"))

                    def on_req(r, wh=where):
                        nonlocal count
                        u = urlparse(r.url)
                        if u.scheme not in ("http", "https"):
                            return
                        count += 1
                        if u.path != "/" and not u.path.startswith(PREFIX):
                            problems.append(f"absolute path [{wh}]: {r.method} {u.path}")
                    page.on("request", on_req)
                    shot = lambda name, s=sz, i=ident, pg=page: a.shots and pg.screenshot(path=f"{a.shots}/{i}-{s}-{name}.png", full_page=True)
                    try:
                        walk(page, ident, base, shot, a.inject_absolute and ident == "admin" and sz == "390x844")
                    except Exception as e:
                        problems.append(f"walk failed [{where}]: {type(e).__name__}: {(str(e).splitlines() or [''])[0]}")
                    ctx.close()
            br.close()
    finally:
        srv.terminate()
    if problems:
        print(f"FAIL: {len(problems)} problem(s) in {count} requests")
        for x in dict.fromkeys(problems):
            print("  -", x)
        sys.exit(1)
    print(f"PASS: no console errors, no page errors, all {count} requests stayed under the ingress prefix")


main()
