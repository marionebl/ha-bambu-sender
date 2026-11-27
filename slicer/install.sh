#!/bin/bash
# Copies this app into Home Assistant's local apps folder; symlinks to tools/ become real files there.
set -euo pipefail
SRC=$(cd "$(dirname "$0")" && pwd); DEST=${1:-/addons/bambu_slicer}
rm -rf "$DEST"; mkdir -p "$DEST"
cp -L "$SRC"/{config.yaml,Dockerfile,server.py,pack.py,index.html,slice.sh,bs.py,flatpak-app.sh,README.md} "$DEST"/
mkdir "$DEST/engine"; cp "$SRC"/engine/{build.sh,flatten.mjs} "$DEST/engine/"
chmod 755 "$DEST"/{slice.sh,bs.py,flatpak-app.sh} "$DEST/engine/build.sh"
echo "copied to $DEST"
