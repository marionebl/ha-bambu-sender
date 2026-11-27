# Druckwerk

Slicing, print queue and cloud control for 3D printers (H2S, P1S and similar) in Home Assistant. Druckwerk is an independent project and is not affiliated with any printer vendor. This repository holds the Druckwerk app (`hub/`) and its Home Assistant integration (`custom_components/druckwerk`). The integration adds cloud controls to printers that the vendor's own Home Assistant integration already shows (its domain is named in `custom_components/druckwerk/externals.py`). These printers have firmware that rejects unsigned commands.

Each printer gets these entities:

- Print file select, timelapse and bed leveling switches, and a start button (`druckwerk.print_file`)
- Cloud camera: a live stream and still images through the cloud tunnel (see [Camera](#camera))
- Chamber light (cloud): sends through the app and shows the state the vendor integration reports
- Re-read slot 1–4: the AMS reads the spool's RFID tag again, and you do not have to remove the spool
- Cloud sender status sensor

## Camera

The camera entity plays a live stream. HA's go2rtc integration serves it, so the browser gets WebRTC.

- The app opens one cloud tunnel per printer and shares it between all viewers.
- H.264 printers are repackaged to MPEG-TS with ffmpeg and no re-encoding. This needs a feed without B-frames.
- A new viewer starts at the last keyframe. The tunnel stays open for 30 s after the last viewer leaves, so a reopen starts fast.
- Printers that send JPEG frames (P1P, P1S, A1, A1 mini) are passed through unchanged. Their stream source is `ffmpeg:<url>#video=h264`, so go2rtc encodes H.264 for WebRTC.
- The stream URL is `<app>/api/camera/<serial>/live`.
- Still images come from the last keyframe of the live stream while it runs, because the bridge allows one tunnel per printer. Otherwise each still opens a new tunnel and takes 10 to 60 s.
- While the app has no MQTT status for the printer (printer offline), the camera has no stream and shows a "Printer offline" image. The `online` attribute shows this state, and `frame_time` shows when the last frame was fetched.

## Uploading print files

Agents and scripts can put a sliced `.gcode.3mf` into the "Print file" list without an HA account:

```
curl -fsS -X PUT --data-binary @plate.gcode.3mf \
  -H "X-Druckwerk-Secret: $DRUCKWERK_UPLOAD_SECRET" \
  "$DRUCKWERK_UPLOAD_URL/plate.gcode.3mf"
```

- `DRUCKWERK_UPLOAD_URL` is `https://<your HA address>/api/druckwerk/upload`.
- Set the secret in Settings → Devices & services → Druckwerk → Configure (at least 24 characters). An empty secret turns uploads off.
- A wrong secret counts as a failed login, so HA's IP ban applies if `ip_ban_enabled` is on.
- The file must be a sliced 3MF (it has `Metadata/plate_N.gcode`). The limit is 64 MB. An existing name needs `?overwrite=1`.
- The answer has the stored name, the size and the SHA-256.

The integration needs the Druckwerk app (bridge API on port 8098).

## Print guard

Before anything is sent, `druckwerk.print_file` and the Start button check the first plate's G-code (`gcode_guard.py`) and refuse the file, listing every problem, if:

- `; printer_model` is not the target printer's model,
- there is no AMS filament load (`M620 S<n>A`, `T<n>`, `M621 S<n>A`, n < 255) before the first extruding move (skipped with `use_ams: false`),
- the start G-code has no `M1002 gcode_claim_action` stage markers (every start G-code of these printers has them),
- the nozzle is not heated before the first extruding move.

`force` only overrides the "printer is busy" check, not this guard.

## Druckwerk integration (v0.10)

- Setup finds the app through the Supervisor (a slug ending in `_druckwerk`); the URL can be set by hand in the options.
- Options: hub secret (normally delivered by the app through Supervisor discovery, which also keeps URLs and secret current; paste it by hand otherwise), notification recipients as a map `person.<name>: notify.<service>`.
- Todo list `Queue` per printer: queued and pending jobs (owner in the description). Deleting an item cancels the job, moving it reorders; adding is not supported (a job needs a sliced file).
- Events: the hub is polled every 5 s and fires `druckwerk_job_done`, `_job_failed`, `_job_started`, `_approval_needed`, `_queue_changed` with the hub's payload.
- Notifications go to the printer's admins, and only to people whose `person` is `home`. After `job_done` with a next job queued they get "Plate clear? Start next: <job>"; the action starts it (`start-next` with `plate_clear`). `approval_needed` notifies the admins.

## Tests

```
pip install pytest-homeassistant-custom-component
pytest tests                      # integration (Python 3.14)
pytest hub/slicer                 # slicer server
(cd hub/bridge/tests && pytest)   # bridge server and plugin download
pytest -p no:homeassistant -p no:socket hub/queue/tests hub/tests
```

## Druckwerk app

`hub/` is the one app to install: it runs the cloud bridge (what `addon/` did), the slicer (`hub/slicer/`)
and the bridge build (done in the image build, `hub/bridge/`). Prebuilt images for aarch64 and amd64 are on ghcr.io, so nothing is built on the Pi.

1. In Home Assistant: Settings, Apps, App store, menu, Repositories, add `https://github.com/marionebl/druckwerk`.
2. Install "Druckwerk" and start it. "Druckwerk" appears in the sidebar: log in to your printer cloud account(s) under setup, slice and print from the main page.
3. The integration reaches the bridge API at `http://<app hostname>:8098` (see below).

On amd64 the bridge and the vendor plugin run natively, on aarch64 under `qemu-x86_64`.

The image does not contain the printer vendor's proprietary network plugin. At app start `hub/bridge/plugin.py` downloads it from the vendor's CDN to your device (`/data/plugin/<version>/`), checks its SHA-256 against the value pinned in the repository and installs only the five libraries the bridge loads. Until that has succeeded, `GET /api/health` on the bridge reports `plugin.state` `downloading` or `failed` (with the error and the next retry; retries back off up to 10 minutes) and no login daemon is started. By using the app you download that plugin from the vendor under its terms. The bridge is built from [boo-cloud](https://github.com/estampo/boo-cloud) (MIT, notice in `/usr/share/licenses/boo-cloud/` in the image) plus a patch (`hub/bridge/bridge.patch.xz`); the printer certificate comes from the vendor's AGPL slicer repository. All vendor-owned names, URLs and identifiers are collected in `hub/externals.py`. `hub/tests/no_vendor_libs.sh` (run by `hub/tests/image_test.sh` and CI) fails if a vendor library is found in the image.
Ingress serves the slicer UI; the bridge page and API are proxied under `api/bridge/`. The bridge also answers on port 8098 inside the Home Assistant network.
The app hostname is `<repository hash>-druckwerk`. Supervisor derives the hash from the repository URL (`c9a833d4` for `https://github.com/marionebl/druckwerk`), and `ha apps info` or the Supervisor API (`/addons/<slug>/info`, field `hostname`) shows it.

Tests: `hub/tests/image_test.sh` builds the image and checks it (no vendor libs, plugin download from a local fake CDN with a right and a wrong hash, bridge health, a test plate sliced through the API). CI runs it on both architectures (`.github/workflows/hub.yml`) and pushes `ghcr.io/marionebl/druckwerk-<arch>:<version>` from main. Raise `version` in `hub/config.yaml` with every change.
