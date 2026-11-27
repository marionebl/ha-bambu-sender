#!/bin/bash
# Test stand-in for slice.sh: copies FAKE_RESULT to <outdir> and records the env it got.
set -euo pipefail
mkdir -p "$2"; cp "$FAKE_RESULT" "$2/$(basename "${1%.*}").gcode.3mf"
printf '%s\n' "PRINTER=$PRINTER" "PROCESS=$PROCESS" "BED=$BED" "COLOURS=$COLOURS" "FILAMENT=${3:-}" > "$2/../slice-env.txt"
