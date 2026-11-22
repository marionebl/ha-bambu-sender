---
name: bambu-print-flow
description: Take a print from the Pandabau Prints tracker to a running print on a Bambu printer. Covers slicing good/better/best, the Plate Review artifact, Mario's sign-off, upload to Home Assistant and print start.
---

# Bambu print flow

Use this skill for any request to slice, review, upload or print a plate for Mario's Bambu printers.

## Hard rules

- Do not send anything to a printer without Mario's explicit sign-off in this conversation. Sign-off is a message from Mario that names the plate and the variant, for example "print better". A sign-off for one file does not cover a re-slice.
- Before the sign-off, show the sliced plate in the Plate Review artifact. Show the printer camera too.
- For every slice, make three variants: Good, Better, Best. They trade quality against print time.
- Do the research and fact-finding yourself. Do not hand a question back to Mario that a tool can answer.
- Do not hide a failed check. Report each FAIL line, mismatch and deviation.

## Fixed facts

| Item | Value |
| --- | --- |
| Pandabau Prints tracker | https://claude.ai/artifact/LFtBgAoZjs7n8gDnp9Ysve |
| Plate Review artifact | https://claude.ai/artifact/X5JQjihtWQnq6ZasewB5oh |
| Tools repo | https://github.com/marionebl/ha-bambu-sender (folder `tools/`) |
| Tilman's H2S | select `select.tilman_h2s_print_file`, camera `camera.tilman_h2s_cloud_camera` |
| Meno's P1S (MAM-HH-3D) | select `select.mam_hh_3d_print_file`, camera `camera.meno_ps1_cloud_camera` |
| Upload | `PUT $BAMBU_SENDER_UPLOAD_URL/<name>.gcode.3mf` with header `X-Bambu-Sender-Secret: $BAMBU_SENDER_UPLOAD_SECRET` |
| Print service | `bambu_sender.print_file` (fields: `serial` or `device_id`, `file`, `timelapse`, `bed_leveling`, `use_ams`, `force`) |

The slicer in `tools/slicer` makes files for the H2S 0.4 nozzle only. A P1S plate needs a P1S machine profile in `slice.sh` first. Tell Mario this before you start a P1S plate.

The Home Assistant tools are the HA-MCP tools (`ha_get_state`, `ha_call_service`, `ha_get_camera_image`, ...). If they are not in the session, say so and stop at the upload step.

## Step 1: Find the plate in the tracker

1. Read the tracker with the ArtifactData tool. Collections:
   - `plates`: `{name, status, printer, material, time, members:[{project, parts}], extras, note, review, cover}`. Plate status is `draft`, `ready`, `printing` or `printed`.
   - `projects`: `{name, status, material, note, deps, updated}`.
   - `files`: `{project, name, version, size, asset, enc}`. With `enc: "b64"` the asset is base64 text of the file.
2. Find the plate that Mario names. If he names none, take the plate with status `ready` or `draft` that was created last, and say which one you took.
3. Get the source model (3MF or STL). Read it from the `files` asset of the member project, newest version first. Decode base64 when `enc` is `b64`.

## Step 2: Check the printer before you slice

Do these reads in parallel with HA-MCP:

- The printer state and the AMS slots: the filament type and the colour per slot. The slice must use the filament that is loaded.
- The bed plate type (`print_bed_type`). Use it for `BED`.
- A camera image (`ha_get_camera_image`). Look for objects on the bed.

If the loaded filament or the bed does not fit the plate, tell Mario what must change. Continue with the slice, because Mario can change the spool while you work.

## Step 3: Slice with a delegated agent

Start one fresh Agent (model `sonnet`, not a fork) for the three slices. Give it this brief, with the values filled in:

> Goal: slice `<model file>` for Tilman's Bambu H2S in three variants, so Mario can choose quality against time.
> Setup: `git clone https://github.com/marionebl/ha-bambu-sender && cd ha-bambu-sender/tools && slicer/setup.sh` (Ubuntu 24.04, about 3 minutes, needs apt and GitHub).
> For each variant, run:
> `PROCESS="<process>" BED="<bed>" COLOURS="<#hex per filament>" slicer/bin/slice.sh <model> out/<id> "<filament profile>"`
> If the model is a laid-out plate (objects already placed with gaps), add `ARRANGE=0`. Otherwise OrcaSlicer arranges the objects again, closer than the layout.
> - good: `0.24mm Standard @BBL H2S`
> - better: `0.20mm Standard @BBL H2S`
> - best: `0.16mm High Quality @BBL H2S`
> Then for each variant: `python3 review/bin/gcode2plate.py out/<id>/*.gcode.3mf review-out/<id> --plate-name "<plate>"` and `python3 review/bin/check_plate.py review-out/<id> --gcode3mf out/<id>/*.gcode.3mf`.
> Done when: each variant has a `.gcode.3mf` and `check_plate.py` prints `RESULT: ALL CHECKS PASSED`.
> A failure looks like: a `FAIL` line, a non-zero exit, or overlaps above 0 in the gcode2plate output. Report it. Do not change the tools to make a check pass.
> Do not upload or print anything. Do not change files outside the clone.
> Report: per variant the print time, the filament grams, the layer height, the overlap counts, the check result (quote the output), and the absolute paths of the `.gcode.3mf` and `review-out/<id>` folders. Name each deviation from this brief.

Check the report yourself. Run `check_plate.py` again on one variant, and read the times and grams from `review-out/<id>/plate.json`.

## Step 4: Publish to Plate Review

1. Read the Plate Review artifact first (Artifact `read`) to get the live page.
2. Make a folder with the page and the data:
   - `index.html`: the page source from `tools/review/page/index.html` (or the live page, if it is newer).
   - `variants.json`:
     ```json
     {"default": "better", "variants": [
       {"id": "good", "label": "Good", "summary": "0.24 mm, 1h 45m, 73.7 g"},
       {"id": "better", "label": "Better", "summary": "0.20 mm, 1h 58m, 73.5 g"},
       {"id": "best", "label": "Best", "summary": "0.16 mm, 3h 51m, 75.4 g"}]}
     ```
   - `<id>/plate.json` and `<id>/plate.bin.txt` for each variant, from `review-out/<id>`.
3. Publish with the Artifact tool: `url` = the Plate Review URL, `file_path` = `index.html`, and `files` = `variants.json` plus the six data files. Then open it so Mario sees it.
4. Set the tracker plate `review` to the Plate Review URL, and `status` to `ready`.

## Step 5: Ask for sign-off

Send Mario one message with:

- A table of the three variants: layer height, print time, filament grams.
- The overlap and check results. Name each FAIL.
- The camera image, and what you see on the bed.
- The loaded filament against the filament in the slice. Name each mismatch.
- One question: "Which variant do I print on <printer>?"

Then stop and wait. Do not upload before the sign-off. If Mario asks for a change, go back to step 3.

## Step 6: Upload

After the sign-off, upload only the chosen variant. Use a name that has the plate, the date and the variant, for example `test_plate_2026-11-21_v3_better.gcode.3mf`.

```
curl -fsS -X PUT --data-binary @<file> \
  -H "X-Bambu-Sender-Secret: $BAMBU_SENDER_UPLOAD_SECRET" \
  "$BAMBU_SENDER_UPLOAD_URL/<name>.gcode.3mf"
```

- Compare the `sha256` in the answer with `sha256sum <file>`. They must be equal.
- 409 means the name exists. Pick a new name. Use `?overwrite=1` only if Mario asks.
- 401 means a wrong secret, and it counts as a failed login. Do not retry in a loop.
- If the environment has no `BAMBU_SENDER_UPLOAD_URL`, say so. Do not guess the secret.

## Step 7: Start the print

1. Take a new camera image and read the printer state. The printer must be idle and the bed must be clear. If not, tell Mario and wait.
2. Check that the select (for example `select.tilman_h2s_print_file`) lists the uploaded name.
3. Call `bambu_sender.print_file` with `serial` or `device_id`, `file` = the uploaded name, `use_ams: true`, `bed_leveling: true` and `timelapse: false`, unless Mario asked for other values. Do not set `force` unless Mario asks for it, because `force` ignores a busy printer.
4. Prove the start: within 2 minutes the printer state must change to a printing or preparing state. Quote that state. If it does not change, read the add-on log (`ha_get_logs`) and report the error.

## Step 8: Update the tracker

- Set the plate `status` to `printing`. Add the variant, the file name and the start time to the plate `note`.
- Set each member project `status` to `printing`.
- When Mario says the print is done, set the plate to `printed` and the projects to `printed`.

## Report to Mario

Give the result in two or three lines: the plate, the variant, the printer, the expected end time. Name each step that you did not verify.
