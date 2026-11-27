> Deprecated: replaced by the Bambu Hub app in `../hub`. Kept because the hub image is built from this source.

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

## Install and updates

GitHub Actions builds the image on each push to `main` and pushes `ghcr.io/marionebl/ha-bambu-slicer-<arch>:<version>`. Home Assistant pulls that image, so the Pi builds nothing.

1. Once: Settings > Apps > App store > ⋮ > Repositories, add `https://github.com/marionebl/ha-bambu-sender`. Install "Bambu Slicer" and turn on auto update.
2. For each change: raise `version` in `config.yaml`. The pull request check fails if the app changed and the version did not. After the merge, HA offers (or installs) the update.

Sliced files go to `/share/bambu_slicer/`. Jobs (the last 10) stay in the app's `/data/jobs/`.

## Tests

    python3 -m pytest slicer/tests          # API with a fake slicer
    slicer/tests/image_test.sh              # the built image with the real slicer (needs docker)
