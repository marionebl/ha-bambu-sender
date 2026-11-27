#!/bin/bash
# Builds the app image, slices the test plate in headless Chromium (mt engine top-level, in a cross-origin iframe, st
# engine) and natively, then runs the G-code guard on every browser result. Bambu Sender and HA are mocked.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
W=$(mktemp -d); JOBS=$W/jobs; mkdir -p "$JOBS" "$W/pw"
CTX=$W/ctx; "$REPO/slicer/install.sh" "$CTX" >/dev/null
docker build -q -t bambu-slicer-test "$CTX" >/dev/null
docker image ls bambu-slicer-test --format 'image size {{.Size}}'
(cd "$W/pw" && npm init -y >/dev/null && npm i --silent playwright && npx playwright install --with-deps chromium >/dev/null)
python3 "$HERE/mocks.py" 18801 & MOCK=$!
docker run -d --rm --name bambu-slicer-browser --network host -e SLICER_PORT=18802 -v "$JOBS":/data/jobs \
  -e SLICER_SENDER_URL=http://127.0.0.1:18801 -e SLICER_CORE_URL=http://127.0.0.1:18801/core \
  -e SLICER_SHARE=/tmp/share -e SUPERVISOR_TOKEN=tok bambu-slicer-test >/dev/null
trap 'docker logs bambu-slicer-browser 2>&1 | tail -20; docker rm -f bambu-slicer-browser >/dev/null 2>&1; kill $MOCK' EXIT
for _ in $(seq 50); do curl -fs http://127.0.0.1:18802/api/printers >/dev/null && break; sleep 0.2; done
# Engine facts: the wasm's SIMD use, from its disassembly, and the release's own build notes.
docker cp bambu-slicer-browser:/srv/engine "$W/engine"
ls -la "$W"/engine/*/engine-*.wasm
sudo apt-get install -y -qq wabt >/dev/null 2>&1 || true
for v in st mt; do
  F=$(ls "$W"/engine/*/engine-$v.wasm)
  if command -v wasm-objdump >/dev/null; then
    wasm-objdump -d "$F" > "$W/$v.dis" 2>/dev/null || true
    echo "engine-$v.wasm: $(wc -l < "$W/$v.dis") disassembled lines, $(grep -c 'v128\|i8x16\|i32x4\|f32x4\|f64x2' "$W/$v.dis" || true) with SIMD (v128) opcodes"
  fi
done
set +e
APP=http://127.0.0.1:18802 FRAME_HOST=http://localhost:18803 PLAIN=http://127.0.0.1:18804 STL="$REPO/tools/bambu-studio/test/test_plate_v3.1.stl" \
  RESULT="$W/result.json" PW_DIR="$W/pw" node "$HERE/browser_test.mjs"; T=$?
set -e
[ -s "$W/result.json" ] || { echo "no result"; exit 1; }
G=0
for c in top-level-mt iframe-no-coop top-level-st; do
  ID=$(python3 -c "import json;c=json.load(open('$W/result.json'))['cases'].get('$c');print(c['jobId'] if c else '')")
  [ -n "$ID" ] || { echo "guard $c: no job"; G=1; continue; }
  F=$(ls "$JOBS/$ID"/out/*.gcode.3mf)
  echo "guard $c ($ID):"
  python3 "$REPO/tools/review/bin/check_gcode.py" "$F" --printer H2S && echo "GUARD $c: PASS" || { echo "GUARD $c: FAIL"; G=1; }
done
[ "$T" = 0 ] && [ "$G" = 0 ]
