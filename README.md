# C2Stack

<p align="center">
  <img src="assets/c2stack-logo.svg" alt="C2Stack Logo" width="620">
</p>

<p align="center">
  <a href="https://github.com/CADRE-Platform/C2Stack"><img src="https://img.shields.io/badge/Status-v2.0.0-blue.svg" alt="Status"></a>
  <a href="https://github.com/CADRE-Platform/C2Stack"><img src="https://img.shields.io/badge/Platform-Docker%20Compose-amber.svg" alt="Platform"></a>
  <a href="https://github.com/CADRE-Platform/C2Stack"><img src="https://img.shields.io/badge/Redirector-Apache%20Proxy-red.svg" alt="Redirector"></a>
</p>

Docker-first C2 Framework Training Environment — a containerized practice range that
turns any lab into a safe C2 playground.

C2Stack runs a header-aware **redirector** in front of three C2 frameworks
(**Mythic**, **Sliver**, **Havoc**) as Docker containers. Your existing **Kali VM**
is the operator workstation — it runs the C2 clients and attack tooling and reaches
the frameworks through published ports. No VMs, no Vagrant, no hypervisor required.

## Architecture

```
                       Docker host (on vmnet2 / CADRE lab network)
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│   c2_edge (published)          c2_core (internal: true)                   │
│   ┌──────────────────┐         ┌──────────────────────────────────────┐  │
│   │   redirector     │  proxy  │  mythic · sliver · havoc  (containers)│  │
│   │  Apache :80      │ ──────▶ │  each listens on :80 for C2 callback  │  │
│   │  header check    │         └──────────────────────────────────────┘  │
│   └────────┬─────────┘                                                    │
└────────────┼─────────────────────────────────────────────────────────────┘
             │ victim-facing port (REDIRECTOR_HTTP_PORT)
             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  vmnet2 (192.168.77.0/24) — CADRE Lab / Targets                           │
│  dc01 · mbr01 · linux01 · · ·  (victims call back to host IP :80)         │
└──────────────────────────────────────────────────────────────────────────┘

   Kali VM (operator): sliver-client → host:31337, havoc client → host:40056,
   Mythic UI → https://host:7443
```

**Traffic flow:**
1. A payload on a victim calls back to `http://<host-ip-on-vmnet2>:<REDIRECTOR_HTTP_PORT>/<prefix>`
   with header `X-Request-ID: cadre-c2`.
2. The redirector validates the header → proxies to the matching C2 container on `c2_core`.
3. No valid header → serves a **decoy CDN page** (OPSEC).
4. The C2 containers live on an `internal` network — they have **no direct internet
   egress** and are only reachable through the redirector.

**Loki C2 (optional):**
Loki uses Azure Storage Blob as its C2 channel — agents talk directly to Azure,
bypassing the redirector entirely. It is out-of-band (not containerized) and best
suited for Azure hybrid AD environments where blob traffic blends in. Requires a
Storage Account + SAS token and outbound access to `*.blob.core.windows.net`.

## Quick Start

Prerequisites:
- Docker Desktop running on the host (Windows / Linux / macOS).
- A Kali VM as the operator workstation (you already have one).
- Host reachable from the CADRE lab network (vmnet2, 192.168.77.0/24).

```powershell
# Windows (Docker Desktop)
cd C2Stack\Docker
.\docker-bootstrap.ps1            # redirector + sliver + havoc
.\docker-bootstrap.ps1 -Mythic    # also bring up Mythic
```

```bash
# Linux / macOS
cd C2Stack/Docker
./docker-bootstrap.sh             # redirector + sliver + havoc
./docker-bootstrap.sh --mythic    # also bring up Mythic
```

This copies `.env.example` → `.env` (review it), builds the images, and starts the
stack. The redirector publishes its victim-facing port; the C2 frameworks publish
their operator/control ports to the host.

## Using With CADRE

The CADRE lab VMs (dc01/dc02/mbr01/mbr02/linux01 on 192.168.77.0/24) are your practice
targets. They are already vulnerable — ACLs, SPNs, delegations, SMB signing disabled,
LDAP signing not required, and 60+ documented attack paths.

1. Start the stack (above). Note the Docker host IP on vmnet2.
2. Generate a payload from Mythic/Sliver/Havoc → point the callback to
   `http://<host-ip-on-vmnet2>:<REDIRECTOR_HTTP_PORT>` with header
   `X-Request-ID: cadre-c2` and the framework's URI prefix.
3. Deliver the payload to a CADRE target (SMB share, phishing, etc.).
4. Watch callbacks arrive through the redirector into the right C2 container.

See [`Doc/Docker.md`](Doc/Docker.md) for the full architecture, listener tuning, and
operator next steps.

## Components

| Container | Role | C2 port (internal) | Operator/control port (published) |
|-----------|------|:------------------:|----------------------------------:|
| `redirector` | Apache header-based proxy + decoy page | 80 (published) | — |
| `mythic` | Mythic server + http C2 profile | 80 | 7443 (UI) |
| `sliver` | Sliver teamserver | 80 | 31337 |
| `havoc` | Havoc teamserver | 80 | 40056 |

All C2 frameworks share the `c2_core` internal network; only the redirector is
exposed. Configuration lives in `Docker/.env` (copy from `.env.example`).

## Requirements

| Resource | Minimum |
|----------|:-------:|
| RAM | 6 GB (redirector + 3 frameworks + overhead) |
| Disk | 20 GB (images + volumes) |
| Docker | Docker Desktop 4.x / Compose v2 |
| Network | vmnet2 (192.168.77.0/24) — shared with CADRE |

**Optional (for Loki C2):** Azure Storage Account + SAS token, outbound to
`*.blob.core.windows.net`.

## Repository Layout

```
C2Stack/
├── README.md
├── LICENSE               MIT
├── .gitignore
├── assets/               Logo (SVG) + lint config
├── Docker/               Docker-first deployment
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── docker-bootstrap.ps1 / .sh
│   ├── redirector/       Apache image (header routing + decoy)
│   ├── sliver/           sliver-server image
│   ├── havoc/            Havoc teamserver image
│   └── mythic/           (optional) Mythic data mount
└── Doc/
    └── Docker.md         Docker architecture + practice guide
```

## License

MIT
