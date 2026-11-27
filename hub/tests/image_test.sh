#!/bin/bash
# Builds the hub image, then checks what a user depends on: the bridge answers on its own port and through ingress,
# the bridge binary runs on this arch, and a test plate slices and prints through the slicer API (Sender and HA mocked).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
IMG=${IMG:-bambu-hub-test}
docker build -q -f "${DOCKERFILE:-$REPO/hub/Dockerfile}" ${BUILD_ARGS:-} -t $IMG "$REPO" >/dev/null
docker image ls $IMG --format 'image size {{.Size}}'
docker run --rm --entrypoint /opt/bridge/bridge.sh $IMG --check
python3 "$REPO/slicer/tests/mocks.py" 18801 & MOCK=$!
docker run -d --rm --name $IMG --network host -e BAMBU_PORT=18098 -e SLICER_PORT=18802 \
  -e SLICER_SENDER_URL=http://127.0.0.1:18801 -e SLICER_CORE_URL=http://127.0.0.1:18801/core \
  -e SLICER_SHARE=/tmp/share -e SUPERVISOR_TOKEN=tok -e HUB_INGRESS_IPS=127.0.0.1 -e HUB_HOSTNAME=hub-test \
  -e HUB_SUPERVISOR_URL=http://127.0.0.1:18801/supervisor -e HUB_HA_WS=ws://127.0.0.1:1/none $IMG >/dev/null
trap 'docker logs $IMG 2>&1 | tail -20; docker rm -f $IMG >/dev/null 2>&1; kill $MOCK' EXIT
BRIDGE=http://127.0.0.1:18098; B=http://127.0.0.1:18802
ID=(-H "X-Remote-User-Id: smoke-user" -H "X-Remote-User-Name: smoke")
for _ in $(seq 100); do curl -fs "${ID[@]}" $B/api/me >/dev/null 2>&1 && curl -fs $BRIDGE/api/health >/dev/null 2>&1 && break; sleep 0.2; done
# Identity: the app trusts Supervisor ingress headers, so no headers means 401 and headers mean 200.
code=$(curl -s -o /dev/null -w '%{http_code}' $B/api/me); [ "$code" = 401 ] || { echo "FAIL: /api/me without identity gave $code" >&2; exit 1; }
echo "GET /api/me without identity: $code"
ME=$(curl -fsS "${ID[@]}" $B/api/me); echo "GET /api/me with identity: 200 $ME"
echo "$ME" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["user_id"]=="smoke-user" and d["is_admin"] is False and d["setup_done"] is False,d'
# The slicing flow below runs as the integration (internal caller), which the mock HA cannot grant to a user.
SECRET=$(docker exec $IMG python3 -c 'import json;print(json.load(open("/data/hub.json"))["secret"])')
ID=(-H "X-Hub-Secret: $SECRET")
health() {  # $1 label, $2 url
  local out; out=$(curl -fsS "${ID[@]}" "$2") || { echo "FAIL: $1: no answer from $2" >&2; exit 1; }
  echo "$out" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["server"]=="ok" and d["accounts"]==[],d' || { echo "FAIL: $1: unexpected health $out" >&2; exit 1; }
  echo "$1: health ok, accounts []"
}
health "bridge port" $BRIDGE/api/health
health "via ingress" $B/api/bridge/api/health
# The web UI loads without identity (the API behind it does not). Captured first: grep -q would break curl's pipe.
page=$(curl -fsS $B/); grep -q '<title>Bambu Hub</title>' <<<"$page"
js=$(curl -fsS $B/api.js); grep -q "api/me" <<<"$js"; curl -fsS -o /dev/null $B/views/queue.js; echo "web UI served at /"
curl -fsS "${ID[@]}" $B/api/bridge/ | grep -q 'Bambu Sender'
J=$(curl -fsS "${ID[@]}" -X POST --data-binary @"$REPO/tools/bambu-studio/test/test_plate_v3.1.stl" "$B/api/jobs?name=test_plate_v3.1.stl" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -fsS "${ID[@]}" -X POST -d '{"serial":"H2S0001","quality":"better","tray":"0:0"}' $B/api/jobs/$J/slice >/dev/null
S=$(date +%s)
while :; do
  STATE=$(curl -fsS "${ID[@]}" $B/api/jobs/$J | python3 -c 'import json,sys;print(json.load(sys.stdin)["state"])')
  [ "$STATE" = done ] || [ "$STATE" = failed ] && break; sleep 2
done
echo "slice $STATE in $(( $(date +%s) - S )) s"
curl -fsS "${ID[@]}" $B/api/jobs/$J | python3 -c 'import json,sys;j=json.load(sys.stdin);print(json.dumps({k:j.get(k) for k in ("state","error","settings","info")},indent=1));print(j.get("log","")[-800:])'
[ "$STATE" = done ]
curl -fsS "${ID[@]}" -o /dev/null -w 'plate.png %{http_code} %{size_download} B\n' $B/api/jobs/$J/plate.png
curl -fsS "${ID[@]}" -o /dev/null -w 'plate.gcode %{http_code} %{size_download} B\n' $B/api/jobs/$J/plate.gcode
curl -fsS "${ID[@]}" -X POST -d '{"confirm":true}' $B/api/jobs/$J/print; echo
curl -fsS http://127.0.0.1:18801/calls; echo
curl -fsS http://127.0.0.1:18801/calls | python3 -c 'import json,sys;c=[x for x in json.load(sys.stdin) if x["path"]=="/supervisor/discovery"];assert c and c[0]["body"]["service"]=="bambu_sender" and c[0]["body"]["config"]["host"]=="hub-test",c;print("discovery sent to Supervisor")'
