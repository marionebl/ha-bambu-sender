#!/bin/bash
# Copies this app into Home Assistant's local apps folder; symlinks to tools/ become real files there.
set -euo pipefail
SRC=$(cd "$(dirname "$0")" && pwd); DEST=${1:-/addons/bambu_slicer}
rm -rf "$DEST"; mkdir -p "$DEST"
cp -L "$SRC"/{config.yaml,Dockerfile,server.py,index.html,slice.sh,bs.py,flatpak-app.sh,README.md} "$DEST"/
chmod 755 "$DEST"/{slice.sh,bs.py,flatpak-app.sh}
echo "copied to $DEST"
