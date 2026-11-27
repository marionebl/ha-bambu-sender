# Bambu Sender

Home Assistant integration for the Bambu Sender add-on. It adds cloud controls to printers that
[ha-bambulab](https://github.com/greghesp/ha-bambulab) already shows. These printers have firmware that rejects unsigned commands.

Each printer gets these entities:

- Print file select, timelapse and bed leveling switches, and a start button (`bambu_sender.print_file`)
- Cloud camera: a live stream and still images through the Bambu cloud tunnel (see [Camera](#camera))
- Chamber light (cloud): sends through the add-on and shows the state that ha-bambulab reports
- Re-read slot 1–4: the AMS reads the spool's RFID tag again, and you do not have to remove the spool
- Cloud sender status sensor

## Camera

The camera entity plays a live stream. HA's go2rtc integration serves it, so the browser gets WebRTC.

- The add-on opens one cloud tunnel per printer and shares it between all viewers.
- H.264 printers are repackaged to MPEG-TS with ffmpeg and no re-encoding. This needs a feed without B-frames.
- A new viewer starts at the last keyframe. The tunnel stays open for 30 s after the last viewer leaves, so a reopen starts fast.
- Printers that send JPEG frames (P1P, P1S, A1, A1 mini) are passed through unchanged. Their stream source is `ffmpeg:<url>#video=h264`, so go2rtc encodes H.264 for WebRTC.
- The stream URL is `<add-on>/api/camera/<serial>/live`.
- Still images come from the last keyframe of the live stream while it runs, because the bridge allows one tunnel per printer. Otherwise each still opens a new tunnel and takes 10 to 60 s.
- While the add-on has no MQTT status for the printer (printer offline), the camera has no stream and shows a "Printer offline" image. The `online` attribute shows this state, and `frame_time` shows when the last frame was fetched.

## Uploading print files

Agents and scripts can put a sliced `.gcode.3mf` into the "Print file" list without an HA account:

```
curl -fsS -X PUT --data-binary @plate.gcode.3mf \
  -H "X-Bambu-Sender-Secret: $BAMBU_SENDER_UPLOAD_SECRET" \
  "$BAMBU_SENDER_UPLOAD_URL/plate.gcode.3mf"
```

- `BAMBU_SENDER_UPLOAD_URL` is `https://<your HA address>/api/bambu_sender/upload`.
- Set the secret in Settings → Devices & services → Bambu Sender → Configure (at least 24 characters). An empty secret turns uploads off.
- A wrong secret counts as a failed login, so HA's IP ban applies if `ip_ban_enabled` is on.
- The file must be a sliced 3MF (it has `Metadata/plate_N.gcode`). The limit is 64 MB. An existing name needs `?overwrite=1`.
- The answer has the stored name, the size and the SHA-256.

The integration needs the Bambu Hub app (bridge API on port 8098; the old Bambu Sender add-on answered at `http://local-bambu-sender:8098`).

## Print guard

Before anything is sent, `bambu_sender.print_file` and the Start button check the first plate's G-code (`gcode_guard.py`) and refuse the file, listing every problem, if:

- `; printer_model` is not the target printer's model,
- there is no AMS filament load (`M620 S<n>A`, `T<n>`, `M621 S<n>A`, n < 255) before the first extruding move (skipped with `use_ams: false`),
- the start G-code has no `M1002 gcode_claim_action` stage markers (every Bambu start G-code has them),
- the nozzle is not heated before the first extruding move.

`force` only overrides the "printer is busy" check, not this guard. Agents can run it before upload: `python3 tools/review/bin/check_gcode.py FILE --printer H2S [--no-ams]`.

## Tests

```
pip install pytest-homeassistant-custom-component
pytest tests
```

## Bambu Hub app

`hub/` is the one app to install: it runs the Bambu cloud bridge (what `addon/` did), the Bambu Studio slicer (`slicer/`)
and the bridge build (`builder/`, now done in CI). Prebuilt images for aarch64 and amd64 are on ghcr.io, so nothing is built on the Pi.

1. In Home Assistant: Settings, Apps, App store, menu, Repositories, add `https://github.com/marionebl/ha-bambu-sender`.
2. Install "Bambu Hub" and start it. "Printers" appears in the sidebar: log in to your Bambu account(s) under "Bambu accounts", slice and print from the main page.
3. The integration reaches the bridge API at `http://<app hostname>:8098` (see below).

On amd64 the bridge and the Bambu plugin run natively, on aarch64 under `qemu-x86_64`.
Ingress serves the slicer UI; the bridge page and API are proxied under `api/bridge/`. The bridge also answers on port 8098 inside the Home Assistant network, with the same routes as the old add-on.
The app hostname is `<repository hash>-bambu-hub`. Supervisor derives the hash from the repository URL (`0990873a` for `https://github.com/marionebl/ha-bambu-sender`), and `ha apps info` or the Supervisor API (`/addons/<slug>/info`, field `hostname`) shows it.

Tests: `hub/tests/image_test.sh` builds the image and checks it (bridge health, a test plate sliced through the API). CI runs it on both architectures (`.github/workflows/hub.yml`) and pushes `ghcr.io/marionebl/ha-bambu-hub-<arch>:<version>` from main. Raise `version` in `hub/config.yaml` with every change.

## Old apps (deprecated)

`addon/` (Bambu Sender), `builder/` (Bambu Bridge Builder) and `slicer/` (Bambu Slicer) are deprecated, replaced by Bambu Hub. They stay in the repository because the hub image is built from their sources; do not install them next to the hub (they would all want the same bridge login). Their old images and local install steps are no longer maintained.

Tests for the bridge server: `cd test-server && pytest`.
