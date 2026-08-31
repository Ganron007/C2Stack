# syntax=docker/dockerfile:1
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# Sliver server + client are too large for git (283 MB exe > GitHub 100 MB
# limit), so `Docker/sliver/bin/` is a gitignored LOCAL cache. The build
# prefers the cache (bind-mounted from the build context) and otherwise
# downloads the official v1.7.6 release from GitHub — both paths end with a
# pinned SHA256 verification, so the image content is identical either way.
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,source=bin,target=/opt/src-bin \
    set -e; \
    for f in sliver-server sliver-client; do \
      if [ -f "/opt/src-bin/${f}_linux-amd64" ]; then \
        cp "/opt/src-bin/${f}_linux-amd64" "/usr/local/bin/${f}"; \
      else \
        curl -fsSL "https://github.com/BishopFox/sliver/releases/download/v1.7.6/${f}_linux-amd64" -o "/usr/local/bin/${f}"; \
      fi; \
    done; \
    echo "df6ba0ea19af88538815e59cb72589fb759e325f69a9b3e85760f0c04ce5250d  /usr/local/bin/sliver-server" > /tmp/SHA256SUMS \
    && echo "50a17412ecebfcbeb15ee38b798f5b0b465fdb979ad00f016929e702969d2e5d  /usr/local/bin/sliver-client" >> /tmp/SHA256SUMS \
    && cd /usr/local/bin && sha256sum -c /tmp/SHA256SUMS \
    && chmod +x /usr/local/bin/sliver-server /usr/local/bin/sliver-client \
    && sliver-server version \
    && sliver-client version \
    && apt-get purge -y -qq curl \
    && rm -rf /var/lib/apt/lists/*

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
