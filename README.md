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
