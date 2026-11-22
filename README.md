# Bambu Sender

Home Assistant integration for the Bambu Sender add-on. It adds cloud controls to printers that
[ha-bambulab](https://github.com/greghesp/ha-bambulab) already shows. These printers have firmware that rejects unsigned commands.

Each printer gets these entities:

- Print file select, timelapse and bed leveling switches, and a start button (`bambu_sender.print_file`)
- Cloud camera (still image through the Bambu cloud tunnel)
- Chamber light (cloud): sends through the add-on and shows the state that ha-bambulab reports
- Re-read slot 1–4: the AMS reads the spool's RFID tag again, and you do not have to remove the spool
- Cloud sender status sensor

The integration needs the Bambu Sender add-on (`http://local-bambu-sender:8098`).

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
