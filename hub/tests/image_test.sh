#!/bin/bash
# Builds the hub image, then checks what a user depends on: the bridge answers on its own port and through ingress,
# the bridge binary runs on this arch, and a test plate slices and prints through the slicer API (Sender and HA mocked).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
IMG=${IMG:-bambu-hub-test}
docker build -q -f "$REPO/hub/Dockerfile" ${BUILD_ARGS:-} -t $IMG "$REPO" >/dev/null
docker image ls $IMG --format 'image size {{.Size}}'
docker run --rm --entrypoint /opt/bridge/bridge.sh $IMG --check
python3 "$REPO/slicer/tests/mocks.py" 18801 & MOCK=$!
docker run -d --rm --name $IMG --network host -e BAMBU_PORT=18098 -e SLICER_PORT=18802 \
  -e SLICER_SENDER_URL=http://127.0.0.1:18801 -e SLICER_CORE_URL=http://127.0.0.1:18801/core \
  -e SLICER_SHARE=/tmp/share -e SUPERVISOR_TOKEN=tok $IMG >/dev/null
trap 'docker logs $IMG 2>&1 | tail -20; docker rm -f $IMG >/dev/null 2>&1; kill $MOCK' EXIT
BRIDGE=http://127.0.0.1:18098; B=http://127.0.0.1:18802
for _ in $(seq 100); do curl -fs $B/api/printers >/dev/null 2>&1 && curl -fs $BRIDGE/api/health >/dev/null 2>&1 && break; sleep 0.2; done
health() {  # $1 label, $2 url
  local out; out=$(curl -fsS "$2") || { echo "FAIL: $1: no answer from $2" >&2; exit 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["server"]=="ok" and d["accounts"]==[],d' || { echo "FAIL: $1: unexpected health $out" >&2; exit 1; }
  echo "$1: health ok, accounts []"
}
health "bridge port" $BRIDGE/api/health
health "via ingress" $B/api/bridge/api/health
curl -fsS $B/ | grep -q 'href="api/bridge/"'
curl -fsS $B/api/bridge/ | grep -q 'Bambu Sender'
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
