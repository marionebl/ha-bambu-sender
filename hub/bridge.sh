#!/bin/sh
# Usage: bridge.sh <credentials.toml> <port> | --check. server.py sets HOME to the account's home dir (with the cert).
if [ -d /amd64 ]; then
  # aarch64: x86_64 rootfs under qemu. libBambuSource.so dlopen()s its libs by bare name, so point the guest loader at /usr/lib
  # (-E sets the variable for the emulated process only).
  export BAMBU_LIB_PATH=/usr/lib/libbambu_networking.so
  RUN="qemu-x86_64 -E LD_LIBRARY_PATH=/usr/lib -L /amd64 /amd64/usr/local/bin/boocloud-bridge"
else
  # amd64: native. The plugin hardcodes /tmp/bambu_agent.
  export BAMBU_LIB_PATH=/opt/bridge/lib/libbambu_networking.so LD_LIBRARY_PATH=/opt/bridge/lib
  mkdir -p /tmp/bambu_agent/log /tmp/bambu_agent/config /tmp/bambu_agent/cert
  cp -n /opt/bridge/slicer_base64.cer /tmp/bambu_agent/cert/
  RUN=/opt/bridge/boocloud-bridge
fi
[ "$1" = --check ] && exec $RUN --version
exec $RUN -c "$1" --no-fetch daemon --port "$2" --bind 127.0.0.1
