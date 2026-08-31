# ---- Build stage: compile Havoc teamserver from vendored in-tree source ----
# Source: Docker/havoc/src (vendored upstream HavocFramework/Havoc, GPL-3.0).
# No git clone / network downloads at build time (Go module downloads only).
FROM golang:1.21-bookworm AS builder

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        make \
        gcc \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Vendored upstream tree (Docker/havoc/src). Build only the teamserver
# (skip the Qt6 GUI client - not needed in the container; the operator runs
# the Qt client on the Kali VM). dev-ts-compile avoids Install.sh's mingw
# toolchain download - the runtime stage sets those up instead.
COPY src/ .
RUN make dev-ts-compile

# ---- Runtime stage ----
FROM debian:bookworm-slim

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        wget \
        tar \
        nasm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/havoc/teamserver

# Havoc's makefile compiles the teamserver to the repo root (`../havoc`).
COPY --from=builder /src/havoc ./havoc

# The v0.7 teamserver CLI requires a profile; the built-in default is
# `havoc server -d` which reads ./data/havoc.yaotl. Set it up along with the
# musl cross toolchains the default profile points at (same layout the
# upstream Install.sh produces) and the SQLite DB dir used by the teamserver.
COPY --from=builder /src/profiles ./profiles
RUN mkdir -p /opt/havoc/teamserver/data \
    && cp /opt/havoc/teamserver/profiles/havoc.yaotl /opt/havoc/teamserver/data/havoc.yaotl \
    && wget -q https://musl.cc/x86_64-w64-mingw32-cross.tgz -O /tmp/mingw64.tgz \
    && wget -q https://musl.cc/i686-w64-mingw32-cross.tgz -O /tmp/mingw32.tgz \
    && tar zxf /tmp/mingw64.tgz -C /opt/havoc/teamserver/data \
    && tar zxf /tmp/mingw32.tgz -C /opt/havoc/teamserver/data \
    && rm -f /tmp/mingw64.tgz /tmp/mingw32.tgz

# Teamserver state (SQLite DB, generated certs, loot) persists via the named
# volume mounted at ./data (the path the teamserver actually writes to).
VOLUME ["/opt/havoc/teamserver/data"]

# C2 HTTP listener (proxied by the redirector) and teamserver control port.
EXPOSE 80 40056

# Havoc teamserver v0.7 CLI. `-d` runs the default profile
# (./data/havoc.yaotl, operators 5pider/Neo, password password1234).
CMD ["./havoc", "server", "-d"]
