# Bambu Slicer app

A Home Assistant app with a sidebar panel "Slicer". It slices a model with Bambu Studio on the Home Assistant machine and sends the result to a printer.

1. **Prepare:** upload an STL, 3MF, OBJ or STEP file. Pick the printer, Good/Better/Best and the AMS tray.
2. **Preview:** the print time, the grams, the plate image and a layer view of the G-code.
3. **Print:** tick that the plate is clear, then send. The app calls `bambu_sender.print_file`, so the busy check and the G-code guard of Bambu Sender apply.

Needs the Bambu Sender app (printers and AMS trays) and the bambu_sender integration (print service).

## Slicer

Bambu Studio comes from Flathub (`com.bambulab.BambuStudio`), the only ready-made aarch64 Linux build. `flatpak-app.sh` downloads it at build time and starts the binary through the GNOME runtime's own `ld.so`, so the app needs no flatpak at run time. The image is about 2.5 GB.

| Model | Printer profile | Good / Better / Best | Plate |
| --- | --- | --- | --- |
| H2S | Bambu Lab H2S 0.4 nozzle | 0.24mm Standard / 0.20mm Standard / 0.16mm High Quality @BBL H2S | High Temp Plate |
| P1S | Bambu Lab P1S 0.4 nozzle | 0.24mm Draft / 0.20mm Standard / 0.16mm High Quality @BBL X1C | Textured PEI Plate |

The filament profile comes from the tray's `tray_info_idx` (`bs.py filament-for`), else the Generic profile of the tray's type.

## Slicing in the browser

Under "Slice on", **This device** slices an STL with OrcaSlicer compiled to WebAssembly (the [Muon3D Slicer Engine](https://github.com/Muon-3D/muon3d-slicer-engine), AGPL-3.0, pinned in `engine/build.sh`) in a Web Worker, so the Pi's CPU is not the limit. The page then posts the G-code to `POST /api/jobs/<id>/gcode`, which packs a `.gcode.3mf` (`pack.py`) and stores it like a Pi slice, so Preview and Print work unchanged.

- The multi-thread engine needs a cross-origin isolated page. The server sends `Cross-Origin-Opener-Policy`, `Cross-Origin-Embedder-Policy` and `Document-Isolation-Policy` on every response; Chrome 137+ isolates even the Home Assistant sidebar frame. Where the page is not isolated (Safari, Firefox in the sidebar) it defaults to **Pi (Bambu Studio)**; "This device" stays selectable and uses the single-thread engine.
- 3MF, OBJ and STEP uploads slice on the Pi. If the engine fails to load or slice, the page shows the error and offers the Pi.
- The Docker build downloads the engine release and flattens the machine, the three quality presets and the compatible filament presets of each printer in `PRINTERS` into `/srv/engine/presets/<model>.json`. Engine files are served from `/engine/<release>/` with a one-year immutable cache.
- Browser slices have no plate image, and nobody has printed one yet: the guard and Bambu Sender checks pass, a real print is untested.

## Install on the Pi

In the Web Terminal:

    curl -fsSL https://github.com/marionebl/ha-bambu-sender/archive/refs/heads/<branch>.tar.gz | tar -xz -C /tmp
    /tmp/ha-bambu-sender-<branch>/slicer/install.sh

Then Settings > Apps > App store > ⋮ > Check for updates, and install "Bambu Slicer". Sliced files go to `/share/bambu_slicer/`. Jobs (the last 10) stay in the app's `/data/jobs/`.

## Tests

    python3 -m pytest slicer/tests          # API with a fake slicer
    slicer/tests/image_test.sh              # the built image with the real slicer (needs docker)
    slicer/tests/browser_test.sh            # headless Chromium slices in the image: mt, iframe, st, native, guard
