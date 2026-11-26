#!/bin/bash
# Installs Flathub's Bambu Studio (native build for this machine's arch) and makes an APP dir that slice.sh accepts:
# APP/bin/bambu-studio runs the binary with the GNOME runtime's own ld.so, so no flatpak/bwrap at run time.
# usage: flatpak-app.sh <appdir>   (needs flatpak, run as root for --system)
set -euo pipefail
OUT=${1:?appdir}
flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --system -y --noninteractive flathub com.bambulab.BambuStudio
flatpak info --system com.bambulab.BambuStudio
A=$(flatpak info --system --show-location com.bambulab.BambuStudio)/files
RT_REF=$(flatpak info --system --show-runtime com.bambulab.BambuStudio)
R=$(flatpak info --system --show-location "$RT_REF")/files
echo "app=$A runtime=$R ($RT_REF)"
LDSO=$(cd "$R" && find lib lib64 -name 'ld-linux*.so.[0-9]' 2>/dev/null | head -1 || true)
[ -n "$LDSO" ] || { echo "FAIL: no ld-linux in runtime" >&2; exit 2; }
LIBDIR=lib/$(uname -m)-linux-gnu
RES=$(cd "$A" && dirname "$(find . -path '*/profiles/BBL.json' | head -1)" | xargs dirname)
echo "resources=$A/$RES"
mkdir -p "$OUT/bin"; rm -rf "$OUT/app" "$OUT/runtime" "$OUT/resources"
cp -a "$A" "$OUT/app"; cp -a "$R" "$OUT/runtime"; ln -s "app/$RES" "$OUT/resources"
ls -la "$OUT/app/bin"
REAL=$(cd "$OUT/app" && find bin -maxdepth 1 -type f \( -name bambu-studio -o -name BambuStudio \) | head -1 || true)
[ -n "$REAL" ] || { echo "FAIL: no Bambu Studio binary in app/bin" >&2; exit 2; }
cat > "$OUT/bin/bambu-studio" <<W
#!/bin/sh
D=\$(cd "\$(dirname "\$0")/.." && pwd)
exec "\$D/runtime/$LDSO" --library-path "\$D/app/lib:\$D/runtime/$LIBDIR:\$D/runtime/lib" "\$D/app/$REAL" "\$@"
W
chmod 755 "$OUT/bin/bambu-studio"
du -sh "$OUT/app" "$OUT/runtime"
