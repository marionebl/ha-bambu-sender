# Bambu Studio from Flathub: the only ready-made aarch64 Linux build. flatpak is needed only to download it;
# at run time the binary starts through the GNOME runtime's own ld.so (see flatpak-app.sh).
FROM debian:trixie-slim AS slicer
RUN apt-get update && apt-get install -y --no-install-recommends flatpak ca-certificates && rm -rf /var/lib/apt/lists/*
COPY flatpak-app.sh /
RUN /flatpak-app.sh /opt/bs

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=slicer /opt/bs /opt/bs
COPY slice.sh bs.py /opt/bs/bin/
COPY server.py index.html /srv/
ENV BS_APP=/opt/bs SLICER_SLICE_CMD=/opt/bs/bin/slice.sh SLICER_BS=/opt/bs/bin/bs.py SLICER_PROFILES=/opt/bs/resources/profiles
CMD ["python3", "-I", "/srv/server.py"]
