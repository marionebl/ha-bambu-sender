> Deprecated: replaced by the Bambu Hub app in `../hub`. Kept because the hub image is built from this source.

# Bambu Sender add-on

This Home Assistant add-on lets other programs (Claude, through Home Assistant) read a Bambu Lab printer and its AMS filaments and start a cloud print.
The official Bambu network plugin runs only on x86_64. The add-on runs it under qemu as a local daemon and puts a small HTTP API in front of it.

## Install

1. Copy the `addon` folder to the `addons` share of Home Assistant, or add this repository as a local add-on source.
2. Install and start the add-on.
3. Open "Bambu Sender" in the Home Assistant sidebar and log in (see below).

## Login

Each Bambu account gets its own daemon (the Bambu plugin holds one login per process), on its own port starting at 8765.

1. Under "Add account", enter the email and password of a Bambu account and click "Log in".
2. If Bambu asks for a code, it is sent to your email (or, with two-factor login, comes from your authenticator). Enter it and click "Send code".
3. The account appears in the list with its printers and daemon state. Repeat for further accounts. Logging in again with the same email replaces that account's token. "Remove" stops the daemon and deletes the account.

Each account lives in `/data/accounts/<id>/` (`<id>` is the email in lower case with other characters turned into `_`): `credentials.toml` and `printers.json` (mode 600), `port`, and `home/` (the daemon's data dir, with the Bambu certificate).
The password is not stored. After a restart the add-on starts the daemons of all accounts by itself.
If a token expires, that daemon exits and the add-on retries with growing pauses (5 s up to 60 s). Log in again then.
An update from 0.1 moves `/data/credentials.toml` and `/data/printers.json` into an account dir on the first start; no new login is needed.

## API

All routes answer JSON, except `/`. Call them through the ingress path.

| Route | Answer |
| --- | --- |
| `GET /api/health` | `{server, accounts: [{id, email, logged_in, daemon: {running, restarts, health}}]}`. |
| `POST /api/login/start` | Body `{"email", "password"}`. Answer `{"state": "done" \| "need_code" \| "need_tfa", "account": id}`. |
| `POST /api/login/code` | Body `{"code", "email"?}`. Same answer. Use it for the email code and the two-factor code. `email` may be left out when only one login is pending. |
| `DELETE /api/accounts/{id}` | Stops the account's daemon and deletes its data. Answer `{"deleted": id}`, 404 for an unknown id. |
| `GET /api/printers` | The bound printers of all accounts (all fields Bambu gives, except the LAN access code), each with `serial`, `account`, `email` and `live` (the daemon's state, or null). |
| `GET /api/status/{serial}` | The printer's raw MQTT status, `{"print": {...}}`. |
| `GET /api/ams/{serial}` | `{"ams": ..., "vt_tray": ...}`, the filaments in the AMS. |
| `POST /api/print` | Body `{"serial", "filename", "file_b64", "timelapse"?, "bed_leveling"?, "use_ams"?}`. Sends the `.gcode.3mf` to the daemon and returns its answer with its status code. Bodies over 50 MB get 413. |
| `GET /` | A small page to list, add and remove accounts. |

Status, AMS and print calls go to the daemon of the account that owns the serial; an unknown serial gets 404.

For a print, `result: "sent"` (with `return_code: -1`, `print_result: -999`) and `result: "success"` both mean the job was accepted.
A print cannot be cancelled through this add-on.
The first status call after a start can take a while, because the daemon connects to Bambu first.

## Security

The add-on listens only through Home Assistant ingress (no host port).
Only users logged in to Home Assistant and calls proxied by the Supervisor reach it. The API itself has no authentication.
The tokens are never returned by the API and are masked in the add-on log.
