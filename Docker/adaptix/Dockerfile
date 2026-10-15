# ============================================================================
# C2Stack — Adaptix C2 Server
# Builds the Adaptix teamserver + extenders from source and runs it behind
# the C2Stack redirector. Operator GUI client runs on Kali, not here.
# ============================================================================

ARG GO_WIN7_COMMIT=7f6f947f37991ba5778c41ee093c8527560e4d9f
ARG GO_TARBALL_SHA256=9fa5ffeda4170de60f67f3aa0f824e426421ba724c21e133c1e35d6159ca1bec

FROM golang:1.25.14-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS base
ARG GO_WIN7_COMMIT

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        mingw-w64 g++-mingw-w64 gcc g++ make build-essential \
        libssl-dev zlib1g-dev git openssl ca-certificates wget \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# go-win7 toolchain for Windows 7/Server 2008 R2 agent compatibility
RUN git init /usr/lib/go-win7 && \
    git -C /usr/lib/go-win7 fetch --depth=1 https://github.com/Adaptix-Framework/go-win7.git "$GO_WIN7_COMMIT" && \
    git -C /usr/lib/go-win7 checkout --detach FETCH_HEAD && \
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
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS runtime
ARG GO_WIN7_COMMIT
ARG GO_TARBALL_SHA256

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates mingw-w64 g++-mingw-w64 gcc g++ make openssl wget git bash \
    && rm -rf /var/lib/apt/lists/*

# Go runtime for agent generation at runtime
RUN wget -q https://go.dev/dl/go1.25.4.linux-amd64.tar.gz -O /tmp/go.tar.gz && \
    echo "$GO_TARBALL_SHA256  /tmp/go.tar.gz" | sha256sum -c - && \
    rm -rf /usr/local/go && \
    tar -C /usr/local -xzf /tmp/go.tar.gz && \
    ln -s /usr/local/go/bin/go /usr/local/bin/go && \
    rm /tmp/go.tar.gz

# go-win7 for agent cross-compilation
RUN git init /usr/lib/go-win7 && \
    git -C /usr/lib/go-win7 fetch --depth=1 https://github.com/Adaptix-Framework/go-win7.git "$GO_WIN7_COMMIT" && \
    git -C /usr/lib/go-win7 checkout --detach FETCH_HEAD && \
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

RUN mkdir -p /app/data && chmod +x /app/ssl_gen.sh /app/adaptixserver
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 4321 80

ENTRYPOINT ["/app/entrypoint.sh"]
