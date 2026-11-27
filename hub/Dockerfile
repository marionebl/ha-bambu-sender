# Build context is the repo root: docker build -f hub/Dockerfile .
# No RUN runs under emulation: the x86_64 pieces are cross-compiled or only COPYed.
ARG TARGETARCH

# Bridge: boo-cloud v0.2.2 + our patch, always built for x86_64. On an arm64 builder that is a cross build.
FROM --platform=$BUILDPLATFORM rust:1.88-bookworm AS bridge
RUN apt-get update && apt-get install -y --no-install-recommends xz-utils patch && rm -rf /var/lib/apt/lists/* \
 && rustup target add x86_64-unknown-linux-gnu
RUN git clone --depth 1 --branch v0.2.2 https://github.com/estampo/boo-cloud /src
COPY hub/bridge/bridge.patch.xz /tmp/
RUN set -e; cd /src/bridge && xz -dc /tmp/bridge.patch.xz | patch -p1; \
    if [ "$(uname -m)" != x86_64 ]; then \
      apt-get update && apt-get install -y --no-install-recommends g++-x86-64-linux-gnu libc6-dev-amd64-cross; \
      export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc \
             CXX_x86_64_unknown_linux_gnu=x86_64-linux-gnu-g++ AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar; \
    fi; \
    cargo build --release --locked --target x86_64-unknown-linux-gnu; \
    mkdir /out && cp target/x86_64-unknown-linux-gnu/release/boocloud-bridge /out/ && cp /src/LICENSE /out/LICENSE \
 && readelf -h /out/boocloud-bridge | grep -q X86-64

# The cert the bridge needs comes from the vendor's AGPL slicer repo (URL in externals.env). The vendor's proprietary
# network plugin is NOT in the image: hub/bridge/plugin.py downloads it on the user's device at app start (pinned SHA-256).
FROM debian:trixie-slim AS cert
COPY hub/externals.env /externals.env
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN . /externals.env && mkdir /out && curl -fsSL -o /out/slicer_base64.cer "$CERT_URL" && test -s /out/slicer_base64.cer

# The slicer from Flathub: the only ready-made aarch64 Linux build (see hub/slicer/flatpak-app.sh).
FROM debian:trixie-slim AS studio
RUN apt-get update && apt-get install -y --no-install-recommends flatpak ca-certificates && rm -rf /var/lib/apt/lists/*
COPY hub/slicer/flatpak-app.sh hub/externals.env /
RUN /flatpak-app.sh /opt/bs && rm -rf /var/lib/flatpak

FROM debian:trixie-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates ffmpeg && rm -rf /var/lib/apt/lists/*

# aarch64: run the x86_64 bridge and plugin under qemu in an x86_64 rootfs (COPY only, the Pi has no amd64 binfmt).
# The plugin libs land in /amd64/usr/lib at app start (hub/plugin.py).
# glibc 2.34 is the highest symbol needed by bridge and plugin, so bookworm (2.36) suffices.
FROM --platform=linux/amd64 gcr.io/distroless/cc-debian12 AS amd64root
FROM --platform=linux/amd64 debian:bookworm-slim AS amd64libs
FROM base AS arch-arm64
RUN apt-get update && apt-get install -y --no-install-recommends qemu-user && rm -rf /var/lib/apt/lists/*
COPY --from=amd64root / /amd64/
# liblzma/libz are dependencies of the plugin that distroless lacks.
COPY --from=amd64libs /usr/lib/x86_64-linux-gnu/liblzma.so.5 /usr/lib/x86_64-linux-gnu/libz.so.1 /amd64/usr/lib/x86_64-linux-gnu/
COPY --from=bridge /out/boocloud-bridge /amd64/usr/local/bin/boocloud-bridge
# qemu -L only redirects paths that exist in the prefix, so pre-create the plugin's working dirs.
COPY hub/externals.env /opt/bridge/externals.env
COPY --from=cert /out/slicer_base64.cer /tmp/cert/
# The ELF interpreter symlink is dropped by COPY from distroless, and it must be relative to resolve inside /amd64.
# qemu does not apply -L to statx (used by Rust is_file), so the bridge's existence check needs host-side links (made by plugin.py).
RUN . /opt/bridge/externals.env && mkdir -p /amd64$PLUGIN_AGENT_DIR/log /amd64$PLUGIN_AGENT_DIR/config /amd64$PLUGIN_AGENT_DIR/cert /amd64/lib64 \
 && cp /tmp/cert/slicer_base64.cer /amd64$PLUGIN_AGENT_DIR/cert/ && rm -r /tmp/cert \
 && ln -sf ../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /amd64/lib64/ld-linux-x86-64.so.2
# plugin.py creates the host-side links to the plugin libs (a link named like a vendor lib must not be baked in).
ENV DW_PLUGIN_LINK_DIR=/amd64/usr/lib DW_PLUGIN_HOST_LINK_DIR=/usr/lib

# amd64: native.
FROM base AS arch-amd64
RUN apt-get update && apt-get install -y --no-install-recommends libssl3t64 libstdc++6 liblzma5 zlib1g && rm -rf /var/lib/apt/lists/*
COPY --from=bridge /out/boocloud-bridge /opt/bridge/boocloud-bridge
RUN mkdir -p /opt/bridge/lib
ENV DW_PLUGIN_LINK_DIR=/opt/bridge/lib

FROM arch-${TARGETARCH}
ARG VERSION=dev
ARG HASS_ARCH=unknown
COPY --from=cert /out/slicer_base64.cer /opt/bridge/
COPY --from=bridge /out/LICENSE /usr/share/licenses/boo-cloud/LICENSE
COPY hub/bridge/server.py hub/bridge/plugin.py hub/bridge/bridge.sh hub/externals.py hub/externals.env /opt/bridge/
COPY --from=studio /opt/bs /opt/bs
COPY hub/slicer/slice.sh hub/slicer/bs.py hub/externals.py hub/externals.env /opt/bs/bin/
COPY hub/slicer/server.py /srv/slicer_server.py
COPY hub/externals.py /srv/
COPY hub/hub.py /srv/
COPY hub/ui/index.html hub/ui/style.css hub/ui/app.js hub/ui/api.js hub/ui/util.js /srv/ui/
COPY hub/ui/views /srv/ui/views
COPY hub/app.py hub/ha.py hub/filament.py hub/queue/queue_*.py /srv/
COPY hub/run.sh /run.sh
ENV DW_DAEMON_CMD=/opt/bridge/bridge.sh DW_CERT=/opt/bridge/slicer_base64.cer \
    SLICER_SENDER_URL=http://127.0.0.1:8098 SLICER_PORT=8099 \
    BS_APP=/opt/bs SLICER_SLICE_CMD=/opt/bs/bin/slice.sh SLICER_BS=/opt/bs/bin/bs.py SLICER_PROFILES=/opt/bs/resources/profiles
LABEL io.hass.type="app" io.hass.name="Druckwerk" io.hass.version="$VERSION" io.hass.arch="$HASS_ARCH" \
      org.opencontainers.image.source="https://github.com/marionebl/druckwerk"
CMD ["/run.sh"]
