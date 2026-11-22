#!/bin/bash
# One-time setup on Ubuntu 24.04: Bambu Studio 2.9.1 (official AppImage, extracted) plus the few system libs it links.
# No X server needed: the CLI slices headless.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
SUDO=$([ "$(id -u)" = 0 ] || echo sudo)
V=v02.09.01.70
URL=https://github.com/bambulab/BambuStudio/releases/download/$V/BambuStudio_ubu24-$V-20261103183404.AppImage
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq --no-install-recommends ca-certificates curl python3 libgtk-3-0t64 libwebkit2gtk-4.1-0 libgl1 \
  libgstreamer-plugins-base1.0-0 >/dev/null
if [ ! -x "$ROOT/app/AppRun" ]; then
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  curl -fsSL -o "$T/bs.AppImage" "$URL"; chmod +x "$T/bs.AppImage"
  (cd "$T" && ./bs.AppImage --appimage-extract >/dev/null) && mv "$T/squashfs-root" "$ROOT/app"
fi
echo "slicer ready: $ROOT/app/AppRun"
