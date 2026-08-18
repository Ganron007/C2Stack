# C2Stack Docker Practice Guide

## Overview

C2Stack is deployed as Docker containers: a header-aware **redirector** in front of the
**Mythic**, **Sliver**, and **Havoc** C2 frameworks, with your existing **Kali VM** as
the operator workstation. This collapses the old two-VM / two-network design into
containers while keeping the core workflow intact.

The operator workflow and C2 workflow are unchanged — only the infrastructure changed
from VMs to containers.

## What Must Stay True

The practice build should still preserve these behaviors:

- Victim-facing entry point remains separate from the real C2 backend.
- Redirector logic still requires a valid header before proxying to the backend.
- Mythic, Sliver, Havoc, Adaptix, and Meridian remain usable as distinct frameworks.
- Loki remains optional and separate because it already uses Azure Blob Storage rather than the redirector path.
- Operator state should persist across restarts.

## What Can Be Containerized

These components are reasonable Docker candidates:

- Redirector Apache proxy.
- Meridian C2 daemon (HTTP + DNS TXT listeners).
- Mythic teamserver and its supporting services.
- Sliver server.
- Havoc teamserver.
- Adaptix teamserver.
- Supporting web UI and service glue.

These can also be mounted into volumes so they survive restart without rebuilding the image.

## What Is Harder To Replace

The following parts are where Docker-only becomes less faithful to the current setup:

- The VM trust boundary between victim-facing redirector and C2 backend.
- The Kali operator workstation as a dense tool host.
- The clean separation that comes from hypervisor-level isolation.

If the goal is realistic operator practice, the missing isolation matters more than the service count.

## Recommended Docker-First Practice Layout

Use a single Docker host and split the lab into three logical layers:

1. `redirector` container on the victim-facing network.
2. `meridian`, `sliver`, `havoc`, `adaptix`, and `mythic` containers on the back-end network.
3. One operator workstation, either as the host itself or as a lightweight Kali VM, for tools and hands-on interaction.

This keeps the deployment quick without forcing every tool into a separate container.

### Suggested shape

```text
Host
├─ docker compose
│  ├─ redirector
│  ├─ meridian
│  ├─ sliver
│  ├─ havoc
│  ├─ adaptix (optional profile)
│  └─ mythic (optional profile)
└─ optional operator VM or host shell
   └─ nmap, hashcat, certipy, netexec, smb tools, browser access
```

## Why Not Pure Docker Everywhere

Pure Docker is possible, but it comes with tradeoffs:

- Weaker isolation than VMs.
- More manual volume and permission handling.
- Less natural separation between lab services and operator tooling.
- Harder to model a realistic compromised-redirector scenario.

For quick practice, that is acceptable.
For stronger realism, it is a downgrade.

## Best Option By Constraint

### Fastest setup

Docker-only services plus host-based tooling.

Use this when the goal is to practice workflow, callbacks, and operator muscle memory with minimal setup time.

### Best balance

Docker services plus one lightweight operator VM.

Use this when you want most of the speed of Docker but still want a familiar Kali-like working environment.

### Strongest isolation

Keep the current two-VM design.

Use this when you care most about realistic containment and the redirector/C2 trust boundary.

## Recommendation

For a standalone practice build, I would choose the **Docker-first + single operator workstation** approach.

That gives the best speed-to-learning ratio without forcing the whole stack into a fragile all-container layout. It is the cleanest compromise if the goal is efficient repetition rather than production-grade isolation.

## Implementation (Docker-first)

The stack is implemented under `C2Stack/Docker/`. The Kali VM is the operator
workstation; the redirector and the five C2 frameworks run as containers.

### Layout

```
C2Stack/Docker/
├── docker-compose.yml        # redirector + meridian + sliver + havoc (+ mythic/adaptix profiles)
├── .env.example              # copy to .env and adjust
├── docker-bootstrap.ps1      # Windows host bootstrap (Docker Desktop)
├── docker-bootstrap.sh       # Linux/macOS host bootstrap
├── redirector/
│   ├── Dockerfile
│   ├── apache/
│   │   ├── c2stack.conf.template   # header-based routing, env-driven
│   │   └── decoy.html              # CloudEdge CDN decoy page
│   └── entrypoint.sh
├── meridian/                 # zero-dep Go implant + async Python C2 daemon
│   ├── Dockerfile            # multi-stage build (golang builder + python server)
│   ├── entrypoint.sh         # starts HTTP (:8080) & DNS (:5353) listeners
│   ├── meridian/             # python server package
│   └── implant/              # parallax Go implant source (stdlib only)
├── sliver/Dockerfile         # prebuilt sliver-server, daemon mode
├── havoc/Dockerfile          # builds teamserver from source
├── adaptix/Dockerfile        # builds Adaptix teamserver + extenders from source
└── mythic/                   # (optional) server data mount
```

### Networks

- `c2_edge` — the redirector publishes its victim-facing port here.
- `c2_core` — `internal: true` back-channel. The C2 frameworks have **no** direct
  internet egress and are only reachable through the redirector. The operator
  reaches their UIs/control ports via the host-published ports.

### Run it

```powershell
# Windows (Docker Desktop)
cd C2Stack\Docker
.\docker-bootstrap.ps1               # redirector + sliver + havoc (default)
.\docker-bootstrap.ps1 -Mythic       # also bring up Mythic
.\docker-bootstrap.ps1 -Adaptix      # also bring up Adaptix (builds from source)
.\docker-bootstrap.ps1 -All          # all four frameworks
```

```bash
# Linux/macOS
cd C2Stack/Docker
./docker-bootstrap.sh                # redirector + sliver + havoc (default)
./docker-bootstrap.sh --mythic       # also bring up Mythic
./docker-bootstrap.sh --adaptix      # also bring up Adaptix (builds from source)
./docker-bootstrap.sh --all          # all four frameworks
```

### Operator (Kali VM) next steps

- Callback endpoint: `http://<host-ip-on-vmnet2>:<REDIRECTOR_HTTP_PORT>` with header
  `X-Request-ID: cadre-c2`.
- Mythic UI: `https://<host-ip-on-vmnet2>:<MYTHIC_UI_PORT>` (enable `--profile mythic`).
- Sliver operator: connect `sliver-client` to `<host-ip>:31337`.
- Havoc teamserver: `<host-ip>:40056`.
- Adaptix operator: Qt GUI client → `<host-ip>:4321` (enable `--profile adaptix`).

### Framework listener tuning

The redirector forwards each URI prefix to the matching backend **preserving the full
path**, exactly like the VM setup. Configure each framework's HTTP C2 listener to match:

- **Sliver** — create the HTTP listener with `RootPath` = `/cloud/storage/objects` and
  bind port `80` inside the container.
- **Havoc** — bind the HTTP listener on port `80`; set the listener base path to
  `/edge/cache/assets` if your Havoc build supports a configurable path, otherwise the
  redirector still forwards the prefix and Havoc answers on its configured endpoints.
- **Mythic** — install the `http` C2 profile, then set its callback host to the
  redirector URL and ensure the profile serves C2 under `/cdn/media/stream`.
- **Adaptix** — the HTTP Beacon listener binds port `80` inside the container. Set
  the listener URI to `/api/v1/sync` to match the redirector prefix. The DNS, SMB,
  and TCP listeners operate out-of-band (not through the redirector).
- **Meridian** — the HTTP listener binds port `8080` on `c2_core` (`/gateway/v1/telemetry`),
  while the DNS listener listens on `0.0.0.0:5353/udp` (domain `c2.cadre.local`).

### Further Reading & Field Practice

- See the **[Field Practice & Study Guide](PRACTICE-GUIDE.md)** for detailed lab exercises, DNS covert tunneling walk-through, and DFIR-Nexus threat hunting queries.

### Known tradeoffs vs the VM design

- Container isolation is weaker than the hypervisor trust boundary between redirector
  and C2 backend. `c2_core` being `internal` recovers most of the egress isolation.
- Mythic and Adaptix are profile-gated to keep the default stack lightweight. Mythic
  pulls upstream images; Adaptix builds from source (first build ~5-10 min).
- Havoc is built from source at image-build time (needs network); Sliver pulls a
  prebuilt binary. Both persist data via named volumes.