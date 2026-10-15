# ============================================================================
# Meridian C2 — Lightweight Dual-Transport C2 Server + Implant Payload Builder
# ============================================================================

# Stage 1: Build static Go implants (Zero external dependencies)
FROM golang:1.24.13-alpine@sha256:8bee1901f1e530bfb4a7850aa7a479d17ae3a18beb6e09064ed54cfd245b7191 AS builder

WORKDIR /src/implant
COPY implant/ .

RUN mkdir -p /payloads \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o /payloads/parallax-linux-amd64 . \
    && CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o /payloads/parallax-windows-amd64.exe .

# Stage 2: Runtime Meridian Server
FROM python:3.12.15-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    MERIDIAN_STATE=/root/.meridian

WORKDIR /app

# Copy server package and pyproject.toml
COPY pyproject.toml .
COPY meridian/ meridian/
COPY README.md .

# Install Meridian and runtime dependencies
RUN pip install --no-cache-dir .

# Copy precompiled payloads from builder stage
COPY --from=builder /payloads /opt/meridian/payloads

# Copy and setup entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Persist SQLite DB, keys, and session artifacts
VOLUME ["/root/.meridian"]

# 8080: HTTP/WebSocket C2 listener (proxied via redirector on c2_core)
# 5353/udp: Native DNS TXT C2 listener
EXPOSE 8080 5353/udp

ENTRYPOINT ["/entrypoint.sh"]
