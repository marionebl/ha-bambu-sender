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

The integration needs the Bambu Sender add-on (`http://local-bambu-sender:8098`).

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

## Add-on

`addon/` is the Bambu Sender app (add-on). It runs the boo-cloud bridge, an x86_64 binary, under
`qemu-x86_64` on the arm64 Pi. One bridge daemon runs per Bambu account. Install it as a local app in `/addons/bambu_sender`.

The bridge binary is not in git. To build it on the Pi:

1. Copy `builder/` to `/addons/bambu_bridge_builder`. Install the "Bambu Bridge Builder" app and start it.
   It clones boo-cloud v0.2.2, applies `builder/bridge.patch.xz` (camera, command route, device-cert retry),
   cross-compiles for x86_64 (about 20 minutes on a Pi 4) and copies the binary to `/share/bambu_build/boocloud-bridge`.
2. Copy that file to `/addons/bambu_sender/boocloud-bridge-camera`, raise the version in `addon/config.yaml`,
   and update the app.

Tests for the add-on server: `cd test-server && pytest`.
