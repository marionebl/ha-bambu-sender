# Bambu Studio CLI slicer (headless)

Bambu Studio 2.9.1 (`v02.09.01.70`, official Ubuntu 24.04 AppImage, extracted). Slices with Bambu's own system
profiles; no X server. Replaces `tools/slicer` (OrcaSlicer).

## Setup (once per fresh Ubuntu 24.04 session)

    tools/bambu-studio/setup.sh

Uses the prebuilt tarball (release `bambu-studio-v02.09.01.70`, ~350 MB) when it exists, else falls back to the
official AppImage plus four apt packages (slower).

## Slice

    [PRINTER="Bambu Lab P1S 0.4 nozzle"] [PROCESS=...] [BED=...] [COLOURS=#hex,#hex] \
      tools/bambu-studio/bin/slice.sh <input.stl|3mf> <outdir> ["Bambu PETG HF @BBL H2S" ...]

Defaults: printer `Bambu Lab H2S 0.4 nozzle`; process and filament are that printer's `default_print_profile` and
`default_filament_profile`; `BED=High Temp Plate`. P1S: `PROCESS="0.20mm Standard @BBL X1C"`, filament
`"Bambu PETG HF @BBL P1S 0.4 nozzle"`. Output: `<outdir>/<name>.gcode.3mf`.

The CLI only accepts fully merged profiles (this build ships no `*_full` ones), so `bin/bs.py` merges
`inherits` + `include` + own keys, as Bambu Studio's PresetBundle does. It also fills `printer_model_id`.

## Publishing the tarball (needs docker and a `gh` with release rights)

    tools/bambu-studio/release.sh --publish
