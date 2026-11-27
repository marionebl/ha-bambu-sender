# Bambu Hub API (ingress port)

Contract between the app backend (`hub/`), the web UI (`hub/ui/`) and the `bambu_sender` integration.
Paths are relative to the ingress root. All bodies are JSON. Errors: `{"error": "<text>"}` with 400, 401, 403 (text starts `rule:`), 404, 409, 502.

Identity comes from the Supervisor ingress headers `X-Remote-User-Id`, `X-Remote-User-Name`, `X-Remote-User-Display-Name`.
Calls from the integration go to `http://<app hostname>:8099` without ingress and carry `X-Hub-Secret` (shared secret from `/api/integration`); they are internal (admin everywhere).

## Me

- `GET /api/me` → `{user_id, name, is_admin, setup_done, printers: [{serial, role: "admin"|"user"}]}`

## Setup (HA admins only)

- `GET /api/setup` → `{accounts: [{id, email, logged_in, daemon_running}], printers: [{serial, bambu_name, model, account, configured, suggested_prefix}]}`
  `suggested_prefix` is the ha-bambulab entity prefix for that serial, from the HA device registry, or null.
- `POST /api/setup/login/start` `{email, password}` → `{state: "done"|"need_code"|"need_tfa", account}`
- `POST /api/setup/login/code` `{email, code}` → same
- `DELETE /api/setup/accounts/{id}`
- `PUT /api/setup/printers/{serial}` `{name, ha_prefix, plate, nozzle, pause_entities: [entity_id], electricity_kwh_price?: number}` → printer
- `DELETE /api/setup/printers/{serial}`
- `GET /api/integration` → `{url, secret}` (for the integration config flow)

## Printers (filtered to printers the caller has an active grant on; HA admins see all)

- `GET /api/printers` → `[{serial, name, model, role, online, status, stage, progress, remaining_min, end_time, layer, total_layers,
  nozzle_temp, nozzle_target, bed_temp, bed_target, chamber_temp, task_name, cover_url, camera_url,
  active_tray: {unit, slot} | null,
  ams: [{unit, humidity_index, temp, trays: [{slot, type, colour: "RRGGBB", name, remain_pct, spool_id}]}],
  external: {type, colour, name} | null,
  hms: [{code, severity: "fatal"|"serious"|"common"|"info", text, url}],
  current_job: job | null, queue_length}]`
  Values come from ha-bambulab entities (fresh), bridge status as fallback. `camera_url` and `cover_url` are ingress-relative.
- `GET /api/printers/{serial}/camera` → MJPEG or image proxy of the cloud camera (bridge `/api/camera/{serial}/...`).
- `POST /api/printers/{serial}/start-next` `{plate_clear: true}`; `POST /api/printers/{serial}/start` `{job_id, plate_clear: true}` (admin of printer).

## Queue (see hub/queue/queue_routes.py and queue_access.py for rules)

- `GET /api/queue` → `{jobs, history, printers}`; job = `{id, file, source, target, model, filaments, options, state, error, created_at,
  owner_user_id, owner_name, printer?, started_at?, finished_at?, grams?, cost?, plate_png?}`
  states: `pending_approval, queued, starting, printing, done, failed, cancelled`.
- `POST /api/queue`, `POST /api/queue/{id}/{reorder|move|cancel|retry|approve|reject|skip}`.

## Slicing

- Existing slicer routes: `POST /api/jobs` (upload), `POST /api/jobs/{id}/slice`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/(plate.png|plate.gcode)`.
- `POST /api/jobs/{id}/queue` `{target: serial|"any", model?, options?}` → queue job (file, grams and plate image taken from the slice).

## Filament and cost

- `GET /api/spools` → `[{id, brand, type, colour, name, grams_left, grams_total, price_per_kg, printer?, unit?, slot?}]`
- `POST /api/spools`, `PUT /api/spools/{id}`, `DELETE /api/spools/{id}` (admin of the spool's printer, or HA admin).
- A job's `grams` comes from the sliced 3MF (`Metadata/slice_info.config` filament used_g per filament). On `done` the grams are taken off the spools in the used trays.
  `cost` = sum(grams × price_per_kg / 1000) + (print hours × printer watt estimate / 1000 × electricity_kwh_price), rounded to cents. Watt estimate per model in code (H2S, X1, P1S, P1P, A1, A1 mini).

## Access

- `GET/POST /api/grants`, `DELETE /api/grants/{id}` (HA admins). Grant: `{id, user_id, user_name, printer: serial|"*", role: "owner"|"user", until?, condition_entity?, needs_approval?, active}`.
- `GET /api/ha/users` → `[{id, name, is_admin}]` (HA admins), from HA `config/auth/list`.
- `GET /api/ha/entities?domains=schedule,input_boolean,binary_sensor,person` → `[{entity_id, name, state}]` (HA admins), for the condition picker.

## Events for the integration

- `GET /api/events?since=<seq>` (internal) → `{seq, events: [{seq, type: "job_done"|"job_failed"|"job_started"|"approval_needed"|"queue_changed", serial, job}]}`.
  The integration polls this every 5 s and fires the notifications.
