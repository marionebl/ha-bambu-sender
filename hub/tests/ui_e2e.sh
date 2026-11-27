#!/bin/bash
# The web UI against the real hub in the real image: fake HA, bridge and Supervisor (e2e_mock.py), the Supervisor ingress
# replaced by ingress_proxy.py (path prefix + identity headers), real slicing. Fails on any console error,
# page error, failed request, HTTP status >= 400 or request outside the ingress prefix.
# Env: IMG, DOCKERFILE and BUILD_ARGS as in image_test.sh; SHOTS (screenshot dir); SKIP_BUILD=1 to reuse the image; ONLY=setup|seeded runs one of the two passes.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
IMG=${IMG:-druckwerk-e2e}; SHOTS=${SHOTS:-/tmp/ui-shots-e2e}
[ -n "${SKIP_BUILD:-}" ] || docker build -q -f "${DOCKERFILE:-$REPO/hub/Dockerfile}" ${BUILD_ARGS:-} -t $IMG "$REPO" >/dev/null
WORK=$(mktemp -d)
python3 "$HERE/e2e_mock.py" 18811 18812 & MOCK=$!
cleanup() { docker logs $IMG 2>&1 | tail -15; docker rm -f $IMG >/dev/null 2>&1 || true; kill $MOCK 2>/dev/null || true; }
trap cleanup EXIT
run_hub() {  # $1 data dir; the container runs only the hub (the bridge is the mock)
  docker rm -f $IMG >/dev/null 2>&1 || true
  docker run -d --rm --name $IMG --network host -v "$1:/data" ${E2E_DOCKER_ARGS:-} --entrypoint python3 \
    -e SLICER_PORT=18813 -e DW_PORT=18811 -e SLICER_SENDER_URL=http://127.0.0.1:18811 -e SLICER_CORE_URL=http://127.0.0.1:18811/core \
    -e HUB_SUPERVISOR_URL=http://127.0.0.1:18811/supervisor -e HUB_HA_WS=ws://127.0.0.1:18812/websocket -e HUB_HOSTNAME=hub-e2e \
    -e HUB_INGRESS_IPS=127.0.0.1 -e SLICER_JOBS=/tmp/jobs -e SLICER_SHARE=/tmp/share -e SUPERVISOR_TOKEN=tok $IMG -I /srv/hub.py >/dev/null
  for _ in $(seq 100); do curl -fs http://127.0.0.1:18813/index.html >/dev/null 2>&1 && return; sleep 0.2; done
  echo "FAIL: the hub did not start" >&2; exit 1
}
check() {  # $@ idents etc.
  python3 "$REPO/hub/ui/check_ui.py" --base-url http://127.0.0.1:18813 --shots "$SHOTS" "$@"
}
mkdir -p "$SHOTS"
if [ "${ONLY:-setup}" = setup ] || [ -z "${ONLY:-}" ]; then
  echo "== not set up yet: admin sees the wizard"
  mkdir -p $WORK/empty; run_hub $WORK/empty
  check --idents admin-new
fi
if [ "${ONLY:-seeded}" = seeded ]; then
  echo "== seeded hub: admin and a user with grants on two printers"
  python3 "$HERE/e2e_seed.py" $WORK/seeded; run_hub $WORK/seeded
  check --idents admin,user --model "$REPO/hub/tests/fixtures/cube20.stl" --slice-timeout 240
fi
echo "screenshots in $SHOTS"
