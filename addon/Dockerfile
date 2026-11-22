ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest

# amd64 rootfs sources: COPY only, never RUN (the Pi has no amd64 binfmt).
# glibc 2.34 is the highest symbol needed by bridge and plugin, so bookworm (2.36) suffices.
FROM --platform=linux/amd64 gcr.io/distroless/cc-debian12 AS amd64root
FROM --platform=linux/amd64 debian:bookworm-slim AS amd64libs

FROM ${BUILD_FROM} AS fetch
ARG PLUGIN_URL=https://public-cdn.bblmw.com/upgrade/studio/plugins/02.05.00.56/8b2ace6050/linux_02.05.00.56.zip
ARG CERT_URL=https://raw.githubusercontent.com/bambulab/BambuStudio/master/resources/cert/slicer_base64.cer
# The bridge is the camera-enabled fork build (bridge-fork/out/boocloud-bridge), shipped next to this
# Dockerfile as boocloud-bridge-camera. The plugin zip supplies the networking lib plus the camera libs.
COPY boocloud-bridge-camera /out/boocloud-bridge
RUN apk add --no-cache curl unzip \
 && curl -fsSL -o /tmp/plugin.zip "$PLUGIN_URL" \
 && unzip -j /tmp/plugin.zip '*libbambu_networking.so' '*libBambuSource.so' '*libagora_rtc_sdk.so' '*libagora-fdkaac.so' '*liblive555.so' -d /out \
 && curl -fsSL -o /out/slicer_base64.cer "$CERT_URL" \
 && for f in libbambu_networking.so libBambuSource.so libagora_rtc_sdk.so libagora-fdkaac.so liblive555.so slicer_base64.cer boocloud-bridge; do test -s /out/$f || { echo "missing $f"; exit 1; }; done \
 && chmod 755 /out/boocloud-bridge

FROM ${BUILD_FROM}
RUN apk add --no-cache qemu-x86_64 python3 ffmpeg

COPY --from=amd64root / /amd64/
# liblzma/libz are dependencies of the plugin that distroless lacks.
COPY --from=amd64libs /usr/lib/x86_64-linux-gnu/liblzma.so.5 /usr/lib/x86_64-linux-gnu/libz.so.1 /amd64/usr/lib/x86_64-linux-gnu/
COPY --from=fetch /out/boocloud-bridge /amd64/usr/local/bin/boocloud-bridge
COPY --from=fetch /out/libbambu_networking.so /out/libBambuSource.so /out/libagora_rtc_sdk.so /out/libagora-fdkaac.so /out/liblive555.so /amd64/usr/lib/
# qemu -L only redirects paths that exist in the prefix, so pre-create the plugin's working dirs.
COPY --from=fetch /out/slicer_base64.cer /amd64/tmp/bambu_agent/cert/slicer_base64.cer
# The ELF interpreter symlink is dropped by COPY from distroless, and it must be relative to resolve inside /amd64.
RUN mkdir -p /amd64/tmp/bambu_agent/log /amd64/tmp/bambu_agent/config /amd64/lib64 \
 && ln -sf ../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /amd64/lib64/ld-linux-x86-64.so.2
# qemu 11 does not apply -L to statx (used by Rust is_file), so the bridge's existence check needs a host-side path.
# libBambuSource.so is dlopen'd by the camera snapshot code (no Rust-side stat), but link it the same way for any future check.
RUN ln -s /amd64/usr/lib/libbambu_networking.so /usr/lib/libbambu_networking.so \
 && ln -s /amd64/usr/lib/libBambuSource.so /usr/lib/libBambuSource.so

COPY run.sh bridge.sh server.py /
RUN chmod 755 /run.sh /bridge.sh
CMD ["/run.sh"]
