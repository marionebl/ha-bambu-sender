#!/bin/bash
# Installs the slicer from Flathub (native build for this machine's arch) and makes an APP dir that slice.sh accepts:
# APP/bin/studio runs the binary with the GNOME runtime's own ld.so, so no flatpak/bwrap at run time.
# usage: flatpak-app.sh <appdir>   (needs flatpak, run as root for --system; leaves the flatpak install broken)
set -euo pipefail
E=$(dirname "$0"); [ -f "$E/externals.env" ] || E=$E/..; . "$E/externals.env"
OUT=${1:?appdir}
flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --system -y --noninteractive flathub "$FLATHUB_APP_ID"
flatpak info --system "$FLATHUB_APP_ID"
A=$(flatpak info --system --show-location "$FLATHUB_APP_ID")/files
RT_REF=$(flatpak info --system --show-runtime "$FLATHUB_APP_ID")
R=$(flatpak info --system --show-location "$RT_REF")/files
echo "app=$A runtime=$R ($RT_REF)"
LDSO=$(cd "$R" && find lib lib64 -name "ld-linux-$(uname -m | tr _ -).so.[0-9]" 2>/dev/null | head -1 || true)
[ -n "$LDSO" ] || { echo "FAIL: no ld-linux in runtime" >&2; exit 2; }
LIBDIR=lib/$(uname -m)-linux-gnu
RES=$(cd "$A" && dirname "$(find . -path "$PROFILE_SEARCH" | head -1)" | xargs dirname)
echo "resources=$A/$RES"
mkdir -p "$OUT/bin"; rm -rf "$OUT/app" "$OUT/runtime" "$OUT/resources"
mv "$A" "$OUT/app"; mv "$R" "$OUT/runtime";  # mv: the files are hardlinks into the ostree repo, a copy doubles the size
ln -s "app/$RES" "$OUT/resources"
REAL=$(cd "$OUT/app" && find bin -maxdepth 1 -type f \( -name "${STUDIO_BINARY_NAMES%% *}" -o -name "${STUDIO_BINARY_NAMES##* }" \) | head -1 || true)
[ -n "$REAL" ] || { echo "FAIL: no slicer binary in app/bin" >&2; exit 2; }
cat > "$OUT/bin/studio" <<W
#!/bin/sh
D=\$(cd "\$(dirname "\$0")/.." && pwd)
exec "\$D/runtime/$LDSO" --library-path "\$D/app/lib:\$D/runtime/$LIBDIR:\$D/runtime/lib" "\$D/app/$REAL" "\$@"
W
chmod 755 "$OUT/bin/studio"
du -sh "$OUT/app" "$OUT/runtime"
