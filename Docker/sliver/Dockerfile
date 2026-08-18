FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        lsb-release \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Sliver (downloads the latest prebuilt server + client binaries).
RUN curl -s https://sliver.sh/install | bash

# Sliver server data / config live here and persist via the named volume.
VOLUME ["/root/.sliver"]

# C2 HTTP listener (proxied by the redirector) and operator/control port.
EXPOSE 80 31337

# Run the server in daemon mode. The operator creates the HTTP C2 listener via
# sliver-client (see Doc/Docker.md): bind port 80 with
# RootPath "/cloud/storage/objects" so the redirector can route to it.
CMD ["sliver-server", "daemon", "--lhost", "0.0.0.0", "--lport", "31337"]
