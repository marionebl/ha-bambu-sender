# Druckwerk API (ingress port)

Contract between the app backend (`hub/`), the web UI (`hub/ui/`) and the `druckwerk` integration.
Paths are relative to the ingress root. All bodies are JSON. Errors: `{"error": "<text>"}` with 400, 401, 403 (text starts `rule:`), 404, 409, 502.

Identity comes from the Supervisor ingress headers `X-Remote-User-Id`, `X-Remote-User-Name`, `X-Remote-User-Display-Name`.
Calls from the integration go to `http://<app hostname>:8099` without ingress and carry `X-Hub-Secret`; they are internal (admin everywhere).
The app only trusts the identity headers from the Supervisor ingress gateway (172.30.32.2, env `HUB_INGRESS_IPS`); every route except the web UI's own files (`GET /`, `/index.html`, `/style.css`, `/app.js`, `/api.js`, `/util.js`, `/views/*.js`, served from hub/ui) answers 401 without an identity or the secret.
The secret reaches the integration through Supervisor discovery: on start the app POSTs `{service: "druckwerk", config: {host: <own hostname>, bridge_port: 8098, hub_port: 8099, secret}}` to `http://supervisor/discovery` (retried every 30 s until it works). `GET /api/integration` shows the same data to HA admins as the fallback.

## Me

- `GET /api/me` → `{user_id, name, is_admin, setup_done, printers: [{serial, role: "admin"|"user"}]}`

## Setup (HA admins only)

- `GET /api/setup` → `{accounts: [{id, email, logged_in, daemon_running}], printers: [{serial, device_name, model, account, configured, suggested_prefix}]}`
  `suggested_prefix` is the printer integration's entity prefix for that serial: the device whose identifiers contain the serial, then the entity registry's `<serial>_print_status` entity minus `sensor.` and `_print_status`; falls back to the slug of the device name; null if HA has no such device.
  `config` is added to each printer: `null`, or the saved printer (shape of the `PUT` answer) for editing.
- `POST /api/setup/login/start` `{email, password}` → `{state: "done"|"need_code"|"need_tfa", account}`
- `POST /api/setup/login/code` `{email, code}` → same
- `DELETE /api/setup/accounts/{id}`
- `PUT /api/setup/printers/{serial}` `{name, ha_prefix, plate, nozzle, pause_entities: [entity_id], electricity_kwh_price?: number}` → `{serial, name, model, account, ha_prefix, plate, nozzle, pause_entities, electricity_kwh_price}`.
  `model` and `account` come from the bridge; 400 if HA has no `sensor.<ha_prefix>_print_status`, 404 if no bridge account has the serial.
- `DELETE /api/setup/printers/{serial}`
- `GET /api/integration` → `{url, secret}` (HA admins; fallback for the config flow, same data as the discovery message)
- `/api/bridge/*` proxies the bridge's own page and API (HA admins only).

## Printers (filtered to printers the caller has an active grant on; HA admins see all)

- `GET /api/printers` → `[{serial, name, model, role, online, status, stage, progress, remaining_min, end_time, layer, total_layers,
  nozzle_temp, nozzle_target, bed_temp, bed_target, chamber_temp, task_name, cover_url, camera_url,
  active_tray: {unit, slot} | null,
  ams: [{unit, humidity_index, temp, trays: [{slot, type, colour: "RRGGBB", name, remain_pct, spool_id}]}],
  external: {type, colour, name} | null,
  hms: [{code, severity: "fatal"|"serious"|"common"|"info", text, url}],
  current_job: job | null, queue_length}]`
  Values come from the printer integration's entities, bridge status as fallback when a printer has no `print_status` entity. `progress` is a number 0..100, `remaining_min` whole minutes, temps numbers; unavailable values are null.
  `active_tray` and tray `slot` are 0-based, `unit` is the AMS number (0 = "AMS 1"); trays and humidity come from the AMS devices in the entity registry. `humidity_index` is the printer integration's 1 (dry) .. 5 (wet). Empty slots have `type: ""`. `spool_id` is the spool assigned to that tray, or null. `camera_url` and `cover_url` are ingress-relative.
- `GET /api/printers/{serial}/camera` → MJPEG stream of the cloud camera (bridge `/api/camera/{serial}/live`, query string passed on); with `?mode=snapshot` one JPEG (`/snapshot`). Needs any active grant on the printer.
- `GET /api/printers/{serial}/cover` → the print's cover image (HA `image.<prefix>_cover_image`). Needs any active grant.
- `POST /api/printers/{serial}/start-next` `{plate_clear: true}`; `POST /api/printers/{serial}/start` `{job_id, plate_clear: true}` (admin of printer).

## Queue (see hub/queue/queue_routes.py and queue_access.py for rules)

- `GET /api/queue` → `{jobs, history, printers}`; job = `{id, file, source, target, model, filaments, options, state, error, created_at,
  owner_user_id, owner_name, printer?, started_at?, finished_at?, grams?, cost?, plate_png?}`
  states: `pending_approval, queued, starting, printing, done, failed, cancelled`.
- `POST /api/queue`, `POST /api/queue/{id}/{reorder|move|cancel|retry|approve|reject|skip}`.
  `reorder` takes `{position, scope?: serial}`: with `scope`, `position` is 0-based among the waiting (queued + pending_approval) jobs whose target or printer is that serial (other jobs keep their slots); without it, global. Access rules are unchanged: a user's reorder only permutes their own slots.
- `GET /api/queue/{id}/plate.png` → the job's plate image (kept with the job after the slice is pruned); visible to whoever sees the job.
- The job also carries `filament_grams` (per filament), `print_seconds` (slice estimate), `spools_used` (`[{spool_id, grams}]`, after done).

## Slicing

- Existing slicer routes: `POST /api/jobs` (upload), `POST /api/jobs/{id}/slice`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/(plate.png|plate.gcode)`, `POST /api/jobs/{id}/print` (direct print, admin of the printer only; the queue is the normal path).
  A slice belongs to the user who uploaded it (`owner_user_id`); others get 403, HA admins and the integration may use any. Slicing needs an active grant on the chosen printer.
- `POST /api/jobs/{id}/queue` `{target: serial|"any", model?, options?}` → queue job (file, grams and plate image taken from the slice). The 3MF is copied into the share under a unique name; the add follows the normal grant rules (403, or `pending_approval`).

## Filament and cost

- `GET /api/spools` (spools on printers the caller has a grant on; unassigned spools only for HA admins) → `[{id, brand, type, colour, name, grams_left, grams_total, price_per_kg, printer?, unit?, slot?}]`
- `POST /api/spools`, `PUT /api/spools/{id}`, `DELETE /api/spools/{id}` (admin of the spool's printer, or HA admin; unassigned spools: HA admins). An assigned spool needs `unit` and `slot`. A `PUT` may unlink a spool (`printer: null`).
- A job's `grams` comes from the sliced 3MF (`Metadata/slice_info.config` filament used_g per filament). On `done` (exactly once) the grams are taken off the spool in the tray the slice was made for, else a spool of the same type and colour on that printer; never below 0.
  `cost` = sum(grams × price_per_kg / 1000) + (print hours × printer watt estimate / 1000 × electricity_kwh_price), rounded to cents. `print hours` is the slice's estimate. Watt estimate per model in code (H2S, X1, P1S, P1P, A1, A1 mini). A filament without a matching spool, or whose spool has `price_per_kg: null` ("no price", allowed), costs 0. The queue copy of the 3MF is deleted when the job is done.

## Access

- `GET/POST /api/grants`, `DELETE /api/grants/{id}` (HA admins; GET also internal). Answers carry `user_name` (HA user name) and `active` (until/condition evaluated now). Grant: `{id, user_id, user_name, printer: serial|"*", role: "owner"|"user", until?, condition_entity?, needs_approval?, active}`.
- `GET /api/ha/users` → `[{id, name, is_admin}]` (HA admins and internal), from HA `config/auth/list`; system and inactive users are left out.
- `GET /api/ha/entities?domains=schedule,input_boolean,binary_sensor,person` → `[{entity_id, name, state}]` (HA admins), for the condition picker.

## Events for the integration

- `GET /api/events?since=<seq>` (internal) → `{seq, events: [{seq, type: "job_done"|"job_failed"|"job_started"|"approval_needed"|"queue_changed", serial, job}]}`.
  Without `since` the answer is `{seq: <current>, events: []}` (no backlog). `job` is the full job object (always with `id, file, owner_name, target, printer`; `printer` is null until a printer took it); `serial` is the job's printer or fixed target, `""` for `queue_changed` (then `job` is null) and for `any` jobs not yet placed.
  `job_started` fires when the print was sent, `job_failed` when a started job failed. Events are kept in memory (last 500); `seq` starts at the boot time in seconds, so it keeps growing across restarts.
  The integration polls this every 5 s and fires the notifications.
