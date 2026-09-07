# Reproducible PostgreSQL runtime with a rebuilt official gosu 1.19.
FROM golang:1.25.13-bookworm@sha256:e401dae1bf814e29204a8cb7915682e1780951e609ca0dd8865ee1937f510c48 AS gosu-builder

ARG GOSU_COMMIT=6456aaa0f3c854d199d0f037f068eb97515b7513

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --no-checkout https://github.com/tianon/gosu.git /src/gosu \
    && git -C /src/gosu fetch --depth 1 origin "${GOSU_COMMIT}" \
    && git -C /src/gosu checkout --detach "${GOSU_COMMIT}" \
    && test "$(git -C /src/gosu rev-parse HEAD)" = "${GOSU_COMMIT}"

WORKDIR /src/gosu
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -buildvcs=false -ldflags="-s -w" -o /out/gosu . \
    && chmod 0755 /out/gosu

FROM postgres:17.7-bookworm@sha256:86e0b703649d7a792bd9243ee28afc9d8f7c6b2b5638077c9d6882d4d472bbfd

LABEL org.opencontainers.image.source="https://github.com/inryeok-office/inryeok-bot" \
      org.opencontainers.image.gosu.version="1.19" \
      org.opencontainers.image.gosu.commit="6456aaa0f3c854d199d0f037f068eb97515b7513" \
      org.opencontainers.image.gosu.go="go1.25.13" \
      org.opencontainers.image.postgres.major="17"

USER root
COPY --from=gosu-builder /out/gosu /usr/local/bin/gosu

# Apply security updates from the official Debian repositories only. The
# PostgreSQL major version, entrypoint and PGDATA layout remain unchanged.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* \
    && chmod 0755 /usr/local/bin/gosu
