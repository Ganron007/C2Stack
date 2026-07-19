# ---- Build stage: compile Havoc teamserver from source ----
FROM golang:1.21-bookworm AS builder

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends git make gcc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 https://github.com/HavocFramework/Havoc . \
    && make

# ---- Runtime stage ----
FROM debian:bookworm-slim

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Teamserver data (profiles, generated certs) persists via the named volume.
VOLUME ["/opt/havoc/teamserver/Data"]

WORKDIR /opt/havoc/teamserver
COPY --from=builder /src/teamserver/havoc ./havoc
COPY --from=builder /src/teamserver/Profiles ./Profiles

# C2 HTTP listener (proxied by the redirector) and teamserver control port.
EXPOSE 80 40056

# Havoc teamserver. The operator builds the HTTP C2 listener via the Havoc
# client (see Doc/Docker-Practice-Proposal.md): bind port 80 so the redirector
# can forward ${HAVOC_URI_PREFIX} to it.
CMD ["./havoc", "--dev", "--rest", "--range", "0.0.0.0/0"]
