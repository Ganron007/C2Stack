FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        curl \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Sliver server + client directly from GitHub releases (pinned v1.7.6).
# (The official sliver.sh/install script assumes a systemd host - not usable in
# a container. Local practice artifact: signature check skipped.)
ARG SLIVER_VERSION=v1.7.6
RUN set -eux; \
    curl -fL -o /usr/local/bin/sliver-server \
        "https://github.com/BishopFox/sliver/releases/download/${SLIVER_VERSION}/sliver-server_linux-amd64"; \
    curl -fL -o /usr/local/bin/sliver-client \
        "https://github.com/BishopFox/sliver/releases/download/${SLIVER_VERSION}/sliver-client_linux-amd64"; \
    chmod +x /usr/local/bin/sliver-server /usr/local/bin/sliver-client; \
    sliver-server version; \
    sliver-client version

# Sliver server data / config live here and persist via the named volume.
VOLUME ["/root/.sliver"]

# C2 HTTP listener (proxied by the redirector) and operator/control port.
EXPOSE 80 31337

# The server downloads its Go build toolchain to /root/.sliver/go at first
# run; put it on PATH so implant generation via garble finds "go".
ENV PATH="/root/.sliver/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Run the server in daemon mode. The operator creates the HTTP C2 listener via
# sliver-client (see Doc/Docker.md): bind port 80 with
# RootPath "/cloud/storage/objects" so the redirector can route to it.
# PATH must include the Go toolchain the server downloads to /root/.sliver/go,
# otherwise implant generation via garble fails ("exec: go: not found").
CMD ["sh", "-c", "export PATH=/root/.sliver/go/bin:$PATH && sliver-server daemon --lhost 0.0.0.0 --lport 31337"]

