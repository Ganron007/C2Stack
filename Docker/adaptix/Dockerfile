# ============================================================================
# C2Stack — Adaptix C2 Server
# Builds the Adaptix teamserver + extenders from source and runs it behind
# the C2Stack redirector. Operator GUI client runs on Kali, not here.
# ============================================================================

FROM golang:1.25-bookworm AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        mingw-w64 g++-mingw-w64 gcc g++ make build-essential \
        libssl-dev zlib1g-dev git openssl ca-certificates wget \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# go-win7 toolchain for Windows 7/Server 2008 R2 agent compatibility
RUN git clone https://github.com/Adaptix-Framework/go-win7 /usr/lib/go-win7 && \
    mkdir -p /usr/lib/go-win7/pkg/include && \
    cd /usr/lib/go-win7/src/runtime && \
    for f in *.h; do ln -sf /usr/lib/go-win7/src/runtime/$f /usr/lib/go-win7/pkg/include/$f; done

# ---- Build Stage ----
FROM base AS builder

WORKDIR /src

# Copy the vendored Adaptix source tree (Docker/adaptix/src)
COPY src/ .

# Build server + extenders
RUN make server && make extenders

# ---- Runtime Stage ----
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates mingw-w64 g++-mingw-w64 gcc g++ make openssl wget git bash \
    && rm -rf /var/lib/apt/lists/*

# Go runtime for agent generation at runtime
RUN wget -q https://go.dev/dl/go1.25.4.linux-amd64.tar.gz -O /tmp/go.tar.gz && \
    rm -rf /usr/local/go && \
    tar -C /usr/local -xzf /tmp/go.tar.gz && \
    ln -s /usr/local/go/bin/go /usr/local/bin/go && \
    rm /tmp/go.tar.gz

# go-win7 for agent cross-compilation
RUN git clone https://github.com/Adaptix-Framework/go-win7 /usr/lib/go-win7 && \
    mkdir -p /usr/lib/go-win7/pkg/include && \
    cd /usr/lib/go-win7/src/runtime && \
    for f in *.h; do ln -sf /usr/lib/go-win7/src/runtime/$f /usr/lib/go-win7/pkg/include/$f; done

WORKDIR /app

# Copy built artifacts from builder
COPY --from=builder /src/dist/adaptixserver /app/adaptixserver
COPY --from=builder /src/dist/extenders /app/extenders
COPY --from=builder /src/dist/ssl_gen.sh /app/ssl_gen.sh
COPY --from=builder /src/dist/profile.yaml /app/profile.yaml
COPY --from=builder /src/dist/404page.html /app/404page.html

RUN mkdir -p /app/data && \
    chmod +x /app/ssl_gen.sh /app/adaptixserver && \
    printf '#!/usr/bin/env bash\nset -e\n\ncd /app\nif [ ! -f /app/server.rsa.crt ] || [ ! -f /app/server.rsa.key ]; then\n    echo "[adaptix] Generating self-signed teamserver certificate..."\n    openssl req -x509 -nodes -newkey rsa:2048 -keyout /app/server.rsa.key -out /app/server.rsa.crt -days 3650 -subj "/C=US/ST=State/L=City/O=AdaptixC2/CN=localhost"\nfi\necho "[adaptix] Starting Adaptix C2 Server..."\necho "[adaptix] Teamserver listening on :4321 (operators), HTTP C2 via redirector on :80"\nexec /app/adaptixserver -profile /app/profile.yaml\n' > /app/entrypoint.sh && \
    chmod +x /app/entrypoint.sh

EXPOSE 4321 80

ENTRYPOINT ["/app/entrypoint.sh"]
