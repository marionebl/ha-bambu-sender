# Cross-compiles the camera/command fork of boo-cloud's bridge for x86_64 on the arm64 Pi
# (the Pi has no amd64 binfmt, so the build runs natively and only the target is x86_64).
FROM rust:1.88-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends g++-x86-64-linux-gnu libc6-dev-amd64-cross xz-utils patch \
 && rm -rf /var/lib/apt/lists/* && rustup target add x86_64-unknown-linux-gnu
ENV CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc \
    CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc \
    CXX_x86_64_unknown_linux_gnu=x86_64-linux-gnu-g++ \
    AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar \
    CARGO_BUILD_JOBS=3
RUN git clone --depth 1 --branch v0.2.2 https://github.com/estampo/boo-cloud /src
COPY bridge.patch.xz /tmp/
RUN cd /src/bridge && xz -dc /tmp/bridge.patch.xz | patch -p1 \
 && cargo build --release --locked --target x86_64-unknown-linux-gnu \
 && mkdir /out && cp target/x86_64-unknown-linux-gnu/release/boocloud-bridge /out/ \
 && x86_64-linux-gnu-readelf -h /out/boocloud-bridge | grep -q 'X86-64'
CMD ["sh", "-c", "mkdir -p /share/bambu_build && cp /out/boocloud-bridge /share/bambu_build/boocloud-bridge && sha256sum /share/bambu_build/boocloud-bridge && echo BUILDER_COPY_OK"]
