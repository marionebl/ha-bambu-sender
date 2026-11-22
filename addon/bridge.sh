#!/bin/sh
# Usage: bridge.sh <credentials.toml> <port>. server.py sets HOME to the account's home dir (with the cert in it).
export BAMBU_LIB_PATH=/usr/lib/libbambu_networking.so
# libBambuSource.so dlopen()s libagora_rtc_sdk.so / liblive555.so by bare name: point the guest loader at /usr/lib
# (-E sets the variable for the emulated process only, not for qemu itself).
exec qemu-x86_64 -E LD_LIBRARY_PATH=/usr/lib -L /amd64 /amd64/usr/local/bin/boocloud-bridge \
  -c "$1" --no-fetch daemon --port "$2" --bind 127.0.0.1
