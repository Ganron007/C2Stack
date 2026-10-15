# ---- Build stage: compile Havoc teamserver from vendored in-tree source ----
# Source: Docker/havoc/src (vendored upstream HavocFramework/Havoc, GPL-3.0).
# No git clone / network downloads at build time (Go module downloads only).
FROM golang:1.25.14-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS builder

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
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        mingw-w64 \
        wget \
        tar \
        nasm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/havoc/teamserver

# Havoc's makefile compiles the teamserver to the repo root (`../havoc`).
COPY --from=builder /src/havoc ./havoc

# The v0.7 teamserver CLI requires a profile; the built-in default is
# `havoc server -d` which reads ./data/havoc.yaotl. The committed C2Stack
# profile (Docker/havoc/havoc.yaotl) adds the HTTP listener for the
# redirector (bind :80, /edge/cache/assets, X-Request-ID header) on top of
# the upstream default. The musl cross toolchains the profile points at are
# laid out the same way the upstream Install.sh produces, plus the SQLite
# DB dir used by the teamserver.
COPY havoc.yaotl ./data/havoc.yaotl
RUN mkdir -p /opt/havoc/teamserver/data \
    && command -v x86_64-w64-mingw32-gcc \
    && command -v i686-w64-mingw32-gcc

# REQUIRED for payload generation: the teamserver compiles Demon payloads
# SERVER-SIDE (not in the Qt client). builder.go reads
# ./payloads/Demon/{src/core,src/crypt,src/inject,src/asm} + include, and
# agents/demons.go needs ./payloads/DllLdr.x64.bin and Shellcode.x64/x86.bin.
# Without this directory every Demon build silently produces a broken
# payload (see the mythic-http audit: missing payloads/ == broken builds).
COPY src/payloads ./payloads

# Teamserver state (SQLite DB, generated certs, loot) persists via the named
# volume mounted at ./data (the path the teamserver actually writes to).
VOLUME ["/opt/havoc/teamserver/data"]

# C2 HTTP listener (proxied by the redirector) and teamserver control port.
EXPOSE 80 40056

# Entrypoint renders the neutral profile template with the lab's
# VICTIM_REDIRECTOR_IP before launch (see entrypoint.sh).
COPY entrypoint.sh /usr/local/bin/havoc-entrypoint.sh
RUN chmod +x /usr/local/bin/havoc-entrypoint.sh

# Havoc teamserver v0.7 CLI. `-d` runs the default profile
# (./data/havoc.yaotl, operators 5pider/Neo, per-deployment password from env).
ENTRYPOINT ["/usr/local/bin/havoc-entrypoint.sh"]
