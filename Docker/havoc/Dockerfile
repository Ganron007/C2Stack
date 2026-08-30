# ---- Build stage: compile Havoc teamserver from source ----
FROM golang:1.21-bookworm AS builder

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends git make gcc sudo npm nodejs libcap2-bin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# Build only the web UI + teamserver (skip the Qt6 GUI client - not needed in
# the container; the operator runs the Qt client on the Kali VM).
RUN git clone --depth 1 https://github.com/HavocFramework/Havoc . \
    && make ts-build teamserver

# ---- Runtime stage ----
FROM debian:bookworm-slim

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Teamserver data (profiles, generated certs) persists via the named volume.
VOLUME ["/opt/havoc/teamserver/Data"]

WORKDIR /opt/havoc/teamserver
# Havoc's makefile compiles the teamserver to the repo root (`../havoc`);
# listener profile yaml files ship in `<repo>/profiles/`.
COPY --from=builder /src/havoc ./havoc
COPY --from=builder /src/profiles ./profiles

# C2 HTTP listener (proxied by the redirector) and teamserver control port.
EXPOSE 80 40056

# Havoc teamserver (v0.7+ CLI: `havoc server [--profile]`; the operator creates
# the HTTP C2 listener via the Havoc Qt client - see Doc/Docker.md).
CMD ["./havoc", "server", "-d"]
