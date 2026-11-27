#!/bin/bash
# One container, two servers: the bridge API on 8098 (for the integration) and the ingress UI on 8099.
# If either dies the app exits, so Supervisor restarts both instead of leaving half a hub.
python3 -I /opt/bridge/server.py & bridge=$!
python3 -I /srv/hub.py & ui=$!
trap 'kill $bridge $ui 2>/dev/null; exit 0' TERM INT
wait -n
echo "a server exited; stopping" >&2
kill $bridge $ui 2>/dev/null
exit 1
