#!/bin/bash
# usage: [PRINTER=...] [PROCESS=...] [BED=...] [COLOURS=#hex,...] slice.sh <input.stl|3mf> <outdir> [filament-profile ...]
# Slices plate 1 with the slicer's own system profiles -> <outdir>/<name>.gcode.3mf
# PRINTER default: DEFAULT_PRINTER in externals.env (preset names are in externals.py); PROCESS and the
# default filament come from that printer's default_print_profile / default_filament_profile.
set -euo pipefail
E=$(dirname "$0"); [ -f "$E/externals.env" ] || E=$E/..; . "$E/externals.env"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
APP=${BS_APP:-$ROOT/app}; PROF=$APP/resources/profiles; BS="python3 -I $ROOT/bin/bs.py"
IN=${1:?input}; OUT=${2:?outdir}; shift 2 || true
[ -s "$IN" ] || { echo "FAIL: input missing or empty: $IN" >&2; exit 2; }
[ -x "$APP/bin/studio" ] || { echo "FAIL: no slicer at $APP (see flatpak-app.sh)" >&2; exit 2; }
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
PRINTER=${PRINTER:-$DEFAULT_PRINTER}
$BS flatten "$PROF" machine "$PRINTER" "$W/machine.json"
J() { python3 -I -c 'import json,sys;d=json.load(open(sys.argv[1]));v=d[sys.argv[2]];print(v[0] if isinstance(v,list) else v)' "$@"; }
$BS flatten "$PROF" process "${PROCESS:-$(J "$W/machine.json" default_print_profile)}" "$W/process.json"
FILS=("$@"); [ ${#FILS[@]} -gt 0 ] || FILS=("$(J "$W/machine.json" default_filament_profile)")
IFS=, read -ra CS <<< "${COLOURS:-}"; FL=""; i=0
for f in "${FILS[@]}"; do
  i=$((i+1)); $BS flatten "$PROF" filament "$f" "$W/f$i.json"
  if [ -n "${CS[$((i-1))]:-}" ]; then   # COLOURS sets the AMS tray colours
    $BS set "$W/f$i.json" default_filament_colour "[\"${CS[$((i-1))]}\"]"; $BS set "$W/f$i.json" filament_colour "[\"${CS[$((i-1))]}\"]"
  fi
  FL="$FL${FL:+;}$W/f$i.json"
done
# Without this the CLI slices for Cool Plate; BED must match the plate in the printer.
$BS set "$W/process.json" curr_bed_type "\"${BED:-High Temp Plate}\""
STEM=$(basename "$IN"); STEM=${STEM%.*}
# The wrapper sets up the runtime itself; LD_LIBRARY_PATH only matters for a bundled slicer.
env -u DISPLAY -u WAYLAND_DISPLAY LC_ALL=C LD_LIBRARY_PATH="$APP/bin:$ROOT/lib" XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-$W} "$APP/bin/studio" --datadir "$W/data" --allow-newer-file ${BS_ARGS:-} \
  --slice 1 --load-settings "$W/machine.json;$W/process.json" --load-filaments "$FL" \
  --outputdir "$W" --export-3mf "$STEM.gcode.3mf" "$IN" >"$W/log" 2>&1 || { tail -20 "$W/log" >&2; exit 3; }
[ -s "$W/$STEM.gcode.3mf" ] || { tail -20 "$W/log" >&2; echo "FAIL: no .gcode.3mf written" >&2; exit 3; }
MID=$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["model_id"])' "$PROF/$PROFILE_DIR/machine/$(J "$W/machine.json" printer_model).json")
$BS fixup "$W/$STEM.gcode.3mf" "$MID"; $BS preview "$W/$STEM.gcode.3mf"; mv "$W/$STEM.gcode.3mf" "$OUT/"
echo "$OUT/$STEM.gcode.3mf"
