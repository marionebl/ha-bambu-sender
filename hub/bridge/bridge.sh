#!/bin/sh
# Usage: bridge.sh <credentials.toml> <port> | --check. server.py sets HOME to the account's home dir (with the cert).
. "$(dirname "$0")/externals.env"
if [ -d /amd64 ]; then
  # aarch64: x86_64 rootfs under qemu. The plugin dlopen()s its libs by bare name, so point the guest loader at /usr/lib
  # (-E sets the variable for the emulated process only).
  export "$BRIDGE_LIB_ENV=/usr/lib/$PLUGIN_NET_LIB"
  RUN="qemu-x86_64 -E LD_LIBRARY_PATH=/usr/lib -L /amd64 /amd64/usr/local/bin/boocloud-bridge"
else
  # amd64: native. The plugin hardcodes its working dir.
  export "$BRIDGE_LIB_ENV=/opt/bridge/lib/$PLUGIN_NET_LIB" LD_LIBRARY_PATH=/opt/bridge/lib
  mkdir -p "$PLUGIN_AGENT_DIR/log" "$PLUGIN_AGENT_DIR/config" "$PLUGIN_AGENT_DIR/cert"
  cp -n /opt/bridge/slicer_base64.cer "$PLUGIN_AGENT_DIR/cert/"
  RUN=/opt/bridge/boocloud-bridge
fi
[ "$1" = --check ] && exec $RUN --version
# The plugin is downloaded at app start; the server only launches the daemon once it is in place.
eval "[ -f \"\$$BRIDGE_LIB_ENV\" ]" || { echo "error: plugin not installed yet" >&2; exit 3; }
exec $RUN -c "$1" --no-fetch daemon --port "$2" --bind 127.0.0.1
