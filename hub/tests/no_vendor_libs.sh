#!/bin/sh
# Fails if the image holds any of the vendor's proprietary libraries (the plugin is downloaded at app start instead).
# Usage: no_vendor_libs.sh IMAGE
. "$(dirname "$0")/../externals.env"
expr=""; for p in $PLUGIN_FIND_PATTERNS; do expr="$expr${expr:+ -o }-name \"$p\""; done
found=$(docker run --rm --entrypoint sh "$1" -c "find / -xdev $expr") || exit 2
if [ -n "$found" ]; then echo "FAIL: vendor libraries in image $1:" >&2; echo "$found" >&2; exit 1; fi
echo "ok: no vendor libraries in $1"
