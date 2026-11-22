#!/bin/bash
# usage: slice.sh <input.stl|3mf> <outdir> [filament-profile-name ...]
# Slices plate 1 for Bambu Lab H2S 0.4 with OrcaSlicer 2.5.2 -> <outdir>/<name>.gcode.3mf
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
IN=${1:?input}; OUT=${2:?outdir}; shift 2 || true
[ -s "$IN" ] || { echo "FAIL: input missing or empty: $IN" >&2; exit 2; }
FILS=("$@"); [ ${#FILS[@]} -gt 0 ] || FILS=("Bambu PLA Basic @BBL H2S")
mkdir -p "$OUT" "$ROOT/profiles/flat" "$ROOT/datadir"
OUT=$(cd "$OUT" && pwd)
PF=$ROOT/profiles/flat
python3 -I "$ROOT/bin/resolve_profile.py" machine "Bambu Lab H2S 0.4 nozzle" "$PF/machine.json" >/dev/null
python3 -I "$ROOT/bin/resolve_profile.py" process "${PROCESS:-0.20mm Standard @BBL H2S}" "$PF/process.json" >/dev/null
FL=""; i=0
for f in "${FILS[@]}"; do
  i=$((i+1)); python3 -I "$ROOT/bin/resolve_profile.py" filament "$f" "$PF/filament_$i.json" >/dev/null
  FL="$FL${FL:+;}$PF/filament_$i.json"
done
# The CLI otherwise slices for Cool Plate; BED must match the plate in the printer (HA: print_bed_type).
python3 -I - "$PF/process.json" "${BED:-High Temp Plate}" <<'PY'
import json, sys
p, bed = sys.argv[1:]
d = json.load(open(p)); d["curr_bed_type"] = bed; json.dump(d, open(p, "w"), indent=1)
PY
# COLOURS (comma-separated, one per filament) sets the AMS tray colours, so the file matches the loaded spools.
if [ -n "${COLOURS:-}" ]; then
  IFS=, read -ra CS <<< "$COLOURS"; j=0
  for c in "${CS[@]}"; do j=$((j+1)); python3 -I - "$PF/filament_$j.json" "$c" <<'PY'
import json, sys
p, c = sys.argv[1:]
d = json.load(open(p)); d["default_filament_colour"] = [c]; d["filament_colour"] = [c]; json.dump(d, open(p, "w"), indent=1)
PY
  done
fi
STEM=$(basename "$IN"); STEM=${STEM%.*}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-$(mktemp -d)}
rm -f "$OUT/$STEM.gcode.3mf"
xvfb-run -a "$ROOT/dl/squashfs-root/AppRun" --datadir "$ROOT/datadir" --allow-newer-file \
  --slice 1 --load-settings "$PF/machine.json;$PF/process.json" --load-filaments "$FL" \
  --outputdir "$OUT" --export-3mf "$STEM.gcode.3mf" "$IN"
[ -s "$OUT/$STEM.gcode.3mf" ] || { echo "FAIL: no .gcode.3mf written" >&2; exit 3; }
python3 -I "$ROOT/bin/postprocess.py" "$OUT/$STEM.gcode.3mf" O1S
echo "$OUT/$STEM.gcode.3mf"
