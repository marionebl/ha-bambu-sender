#!/bin/sh
# Docker build step: downloads the Muon3D Slicer Engine runtime and profile set (OrcaSlicer compiled to WebAssembly)
# and writes <out>/engine/: the runtime as it is, plus presets/<model>.json, flattened here so the browser needs no
# profile code. Needs node >= 22.18 (runs TypeScript) and python3 (reads PRINTERS from server.py).
set -eu
OUT=${1:?usage: build.sh <out dir>}
HERE=$(cd "$(dirname "$0")" && pwd)
TAG=v0.6.0-m1.9  # the newer ...m1.9.p1 release only has profiles
REPO=Muon-3D/muon3d-slicer-engine
W=$(mktemp -d)
for part in engine profiles; do
  curl -fsSL --retry 4 -o "$W/$part.tgz" "https://github.com/$REPO/releases/download/$TAG/muon3d-slicer-$part-${TAG#v}.tgz"
  mkdir "$W/$part" && tar -xzf "$W/$part.tgz" -C "$W/$part"
done
RT=$(dirname "$(find "$W/engine" -maxdepth 3 -name manifest.json | head -1)")
SET=$(dirname "$(find "$W/profiles" -maxdepth 6 -name 'index.*.json' | head -1)")
# flattenPreset is the engine's own reference implementation; the file has no imports besides erased types.
mkdir "$W/proto" && echo '{"type":"module"}' > "$W/proto/package.json"
curl -fsSL --retry 4 -o "$W/proto/profileSet.ts" "https://raw.githubusercontent.com/$REPO/$TAG/packages/protocol/src/profileSet.ts"
(cd "$HERE/.." && python3 -c 'import json, server; print(json.dumps(server.PRINTERS))') > "$W/printers.json"
mkdir -p "$OUT/engine/presets"
node --experimental-strip-types "$HERE/flatten.mjs" "$W/proto/profileSet.ts" "$SET" "$W/printers.json" "$OUT/engine/presets"
# The runtime sits in a folder named after the release so the server can cache it for good.
mkdir "$OUT/engine/$TAG" && cp -r "$RT"/. "$OUT/engine/$TAG/"
rm -f "$OUT/engine/$TAG"/*.br "$OUT/engine/$TAG"/*.gz  # the app does not serve precompressed copies
echo "{\"tag\": \"$TAG\"}" > "$OUT/engine/version.json"
ls -la "$OUT/engine/$TAG" "$OUT/engine/presets"
