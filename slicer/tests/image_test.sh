#!/bin/bash
# Builds the app image and slices the test plate STL through the HTTP API, with Bambu Sender and HA mocked.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
CTX=$(mktemp -d); "$REPO/slicer/install.sh" "$CTX/app" >/dev/null
docker build -q ${BUILD_ARGS:-} -t bambu-slicer-test "$CTX/app" >/dev/null
docker image ls bambu-slicer-test --format 'image size {{.Size}}'
python3 "$HERE/mocks.py" 18801 & MOCK=$!
docker run -d --rm --name bambu-slicer-test --network host -e SLICER_PORT=18802 \
  -e SLICER_SENDER_URL=http://127.0.0.1:18801 -e SLICER_CORE_URL=http://127.0.0.1:18801/core \
  -e SLICER_SHARE=/tmp/share -e SUPERVISOR_TOKEN=tok bambu-slicer-test >/dev/null
trap 'docker logs bambu-slicer-test 2>&1 | tail -20; docker rm -f bambu-slicer-test >/dev/null 2>&1; kill $MOCK' EXIT
B=http://127.0.0.1:18802
for _ in $(seq 50); do curl -fs $B/api/printers >/dev/null && break; sleep 0.2; done
J=$(curl -fsS -X POST --data-binary @"$REPO/tools/bambu-studio/test/test_plate_v3.1.stl" "$B/api/jobs?name=test_plate_v3.1.stl" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -fsS -X POST -d '{"serial":"H2S0001","quality":"better","tray":"0:0"}' $B/api/jobs/$J/slice >/dev/null
S=$(date +%s)
while :; do
  STATE=$(curl -fsS $B/api/jobs/$J | python3 -c 'import json,sys;print(json.load(sys.stdin)["state"])')
  [ "$STATE" = done ] || [ "$STATE" = failed ] && break; sleep 2
done
echo "slice $STATE in $(( $(date +%s) - S )) s"
curl -fsS $B/api/jobs/$J | python3 -c 'import json,sys;j=json.load(sys.stdin);print(json.dumps({k:j.get(k) for k in ("state","error","settings","info")},indent=1));print(j.get("log","")[-800:])'
[ "$STATE" = done ]
curl -fsS -o /dev/null -w 'plate.png %{http_code} %{size_download} B\n' $B/api/jobs/$J/plate.png
curl -fsS -o /dev/null -w 'plate.gcode %{http_code} %{size_download} B\n' $B/api/jobs/$J/plate.gcode
curl -fsS -X POST -d '{"confirm":true}' $B/api/jobs/$J/print; echo
curl -fsS http://127.0.0.1:18801/calls; echo
