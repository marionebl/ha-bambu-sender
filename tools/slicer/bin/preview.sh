#!/bin/bash
# usage: preview.sh <file.gcode.3mf> <out.png>
set -euo pipefail
exec python3 -I "$(dirname "$0")/preview.py" "$@"
