#!/bin/bash
# One-time setup on Ubuntu 24.04: OrcaSlicer 2.5.2 (headless via xvfb) plus the review tools' Python deps.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
SUDO=$([ "$(id -u)" = 0 ] || echo sudo)
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq --no-install-recommends xvfb xauth curl ca-certificates python3 python3-numpy python3-matplotlib \
  libgtk-3-0t64 libwebkit2gtk-4.1-0 libgl1 libegl1 libopengl0 libglu1-mesa libgstreamer-plugins-base1.0-0 \
  libgstreamer-gl1.0-0 libmspack0t64 libsecret-1-0 >/dev/null
mkdir -p "$ROOT/dl"
if [ ! -x "$ROOT/dl/squashfs-root/AppRun" ]; then
  curl -fsSL -o "$ROOT/dl/orca.AppImage" \
    https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.5.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.2.AppImage
  chmod +x "$ROOT/dl/orca.AppImage"
  (cd "$ROOT/dl" && ./orca.AppImage --appimage-extract >/dev/null && rm orca.AppImage)
fi
echo "slicer ready: $ROOT/dl/squashfs-root/AppRun"
