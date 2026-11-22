#!/bin/bash
# Build the self-contained tarball (extracted Bambu Studio + the system libs it links) in a clean
# ubuntu:24.04 container -> dist/<name>.tar.gz. With --publish also upload it as a GitHub release asset.
# usage: release.sh [--publish]    (needs docker; --publish needs gh with release rights; CA=<bundle> if a proxy re-signs TLS)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
V=v02.09.01.70; NAME=bambu-studio-2.9.1.70-ubuntu24.04; TAG=bambu-studio-$V
mkdir -p "$ROOT/dist"
docker run --rm -e CURL_CA_BUNDLE=${CA:+/ca.pem} ${CA:+-v "$CA":/ca.pem:ro} -v "$ROOT/dist":/dist -e NAME=$NAME -e V=$V ubuntu:24.04 bash -euo pipefail -c '
  dpkg-query -W -f="\${Package}\n" | sort > /base.pkgs
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends ca-certificates curl libgtk-3-0t64 libwebkit2gtk-4.1-0 libgl1 libgstreamer-plugins-base1.0-0 >/dev/null
  mkdir /pkg && cd /pkg
  curl -fsSL -o bs.AppImage "https://github.com/bambulab/BambuStudio/releases/download/$V/BambuStudio_ubu24-$V-20261103183404.AppImage"
  chmod +x bs.AppImage && ./bs.AppImage --appimage-extract >/dev/null && mv squashfs-root app && rm bs.AppImage
  mkdir lib
  # copy every linked lib that no base-image package provides
  LD_LIBRARY_PATH=/pkg/app/bin ldd app/bin/bambu-studio | awk "/=> \//{print \$3}" | grep -v "^/pkg/" | sort -u | while read -r f; do
    p=$(dpkg -S "$(readlink -f "$f")" | head -1 | cut -d: -f1)
    grep -qx "$p" /base.pkgs || cp -L "$f" lib/
  done
  tar -czf /dist/$NAME.tar.gz app lib'
ls -l "$ROOT/dist/$NAME.tar.gz"
[ "${1:-}" = --publish ] && gh release create "$TAG" "$ROOT/dist/$NAME.tar.gz" -R marionebl/ha-bambu-sender \
  --title "Bambu Studio 2.9.1 (headless, ubuntu 24.04)" --notes "Extracted official AppImage $V plus linked system libs. Unpack into tools/bambu-studio/ (setup.sh does it)."
true
