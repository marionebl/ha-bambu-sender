#!/bin/bash
# One-time setup on Ubuntu 24.04 -> tools/bambu-studio/app (Bambu Studio 2.9.1, extracted official AppImage).
# Fast path: the self-contained tarball from release.sh (app + system libs, only python3 needed from apt).
# Fallback: the official AppImage plus the few system libs it links from apt. No X server needed.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
SUDO=$([ "$(id -u)" = 0 ] || echo sudo)
V=v02.09.01.70
TARBALL=${BS_TARBALL_URL:-https://github.com/marionebl/ha-bambu-sender/releases/download/bambu-studio-$V/bambu-studio-2.9.1.70-ubuntu24.04.tar.gz}
APPIMAGE=https://github.com/bambulab/BambuStudio/releases/download/$V/BambuStudio_ubu24-$V-20261103183404.AppImage
apt_in() { $SUDO apt-get update -qq && $SUDO apt-get install -y -qq --no-install-recommends "$@" >/dev/null; }
if [ ! -x "$ROOT/app/bin/bambu-studio" ]; then
  apt_in ca-certificates curl python3
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  if curl -fsSL -o "$T/pkg.tar.gz" "$TARBALL"; then
    tar -xzf "$T/pkg.tar.gz" -C "$ROOT"; echo "from tarball"
  else
    echo "no tarball at $TARBALL; using the official AppImage" >&2
    apt_in libgtk-3-0t64 libwebkit2gtk-4.1-0 libgl1 libgstreamer-plugins-base1.0-0
    curl -fsSL -o "$T/bs.AppImage" "$APPIMAGE"; chmod +x "$T/bs.AppImage"
    (cd "$T" && ./bs.AppImage --appimage-extract >/dev/null) && mv "$T/squashfs-root" "$ROOT/app"
  fi
fi
echo "slicer ready: $ROOT/app/bin/bambu-studio"
