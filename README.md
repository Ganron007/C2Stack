# C2Stack

<p align="center">
  <img src="assets/c2stack-logo.svg" alt="C2Stack Logo" width="620">
</p>

<p align="center">
  <a href="https://github.com/Ganron007/C2Stack"><img src="https://img.shields.io/badge/Status-v3.1.0-blue.svg" alt="Status"></a>
  <a href="https://github.com/Ganron007/C2Stack"><img src="https://img.shields.io/badge/Platform-Docker%20Compose-amber.svg" alt="Platform"></a>
  <a href="https://github.com/Ganron007/C2Stack"><img src="https://img.shields.io/badge/Redirector-Apache%20Proxy-red.svg" alt="Redirector"></a>
  <a href="https://github.com/Ganron007/C2Stack"><img src="https://img.shields.io/badge/DNS%20C2-Meridian%20Tunnel-green.svg" alt="DNS C2"></a>
</p>

> [!NOTE]
> **Docker-First C2 Framework Training Environment** — a containerized practice range that turns any lab into a safe, multi-tier C2 playground with zero VM management overhead for backends.

C2Stack runs a header-aware **Apache redirector** in front of five C2 frameworks as Docker containers. Your existing **Kali VM** is the operator workstation — it runs the C2 clients and attack tooling, reaching the frameworks through published control ports.

- **Meridian + Sliver + Havoc** — start by default. No extra flags needed.
- **Mythic** — profile-gated (`--profile mythic`). Pulls upstream image.
- **Adaptix** — profile-gated (`--profile adaptix`). Builds from source (~5 min first run).

---

## Architecture

<p align="center">
  <img src="assets/c2stack-architecture.svg" alt="C2Stack Architecture" width="960">
</p>

### Traffic Flow & OPSEC Boundaries

1. **Callback Initiation**: A payload on a victim calls back to `http://<host-ip-on-vmnet2>:<REDIRECTOR_HTTP_PORT>/<prefix>` with header `X-Request-ID: cadre-c2`, or sends UDP DNS TXT queries to `<host-ip-on-vmnet2>:<MERIDIAN_DNS_PORT>` (default `15353`, mapped to the container's `5353/udp` — Windows mDNS occupies 5353 on the host).
2. **Header Inspection**: The Apache redirector validates the header:
   - **Valid Header**: Proxies to the matching C2 container on the isolated `c2_core` network based on the URI prefix.
   - **Missing / Invalid Header**: Serves a benign **CloudEdge CDN Decoy Page** (shielding the teamservers from scanners and incident responders).
3. **Network Isolation**: The C2 containers live on an internal `c2_core` network with **zero direct internet egress** and are only reachable through the redirector.
4. **Out-of-band cloud C2 (Loki-style Azure Blob)**: *not part of this stack* — all C2Stack agents egress through the redirector (HTTP) or the Meridian DNS listener.

---

## Quick Start

Prerequisites:
- Docker Desktop running on the host (Windows / Linux / macOS).
- A Kali VM as the operator workstation (or host shell).
- Host reachable from the CADRE lab network (vmnet2, `192.168.77.0/24`).

```powershell
# Windows (Docker Desktop)
cd C2Stack\Docker
.\docker-bootstrap.ps1               # redirector + meridian + sliver + havoc (default)
.\docker-bootstrap.ps1 -Mythic       # also bring up Mythic
.\docker-bootstrap.ps1 -Adaptix      # also bring up Adaptix (builds from source)
.\docker-bootstrap.ps1 -All          # all frameworks
```

```bash
# Linux / macOS
cd C2Stack/Docker
./docker-bootstrap.sh               # redirector + meridian + sliver + havoc (default)
./docker-bootstrap.sh --mythic      # also bring up Mythic
./docker-bootstrap.sh --adaptix     # also bring up Adaptix (builds from source)
./docker-bootstrap.sh --all         # all frameworks
```

This copies `.env.example` → `.env`, builds the images, and starts the stack.

---

## Components

| Container | Role | C2 Port (Internal) | Operator / Control Port | Egress Transports |
|---|---|:---:|:---:|:---|
| **`redirector`** | Apache header-based proxy + decoy CDN | 80 (published) | — | HTTP / HTTPS Proxy |
| **`meridian`** | Dual-transport Go stdlib C2 + async Python daemon | 8080 (internal) | 15353/udp (DNS egress) | **HTTP(S) / WS + Chunked DNS TXT** |
| **`havoc`** | Havoc teamserver (C++ Demon evasion payload) | 80 | 40056 (Qt GUI) | HTTP / HTTPS / SMB |
| **`sliver`** | Sliver teamserver (Go implant + extensions) | 80 | 31337 (CLI / RPC) | mTLS / WireGuard / HTTP / DNS |
| **`adaptix`** | Adaptix teamserver (Multiplayer Go + Qt GUI) | 80 | 4321 (Qt GUI) | HTTP/S / DNS / SMB / TCP Gopher |
| **`mythic`** | Mythic core: server + postgres + rabbitmq | 80 | 7443 (Web UI/API) | http C2 profile via mythic-cli (Linux host) |

---

## Framework Highlights

### 1. Meridian C2 — Lightweight Dual-Transport & DNS Tunneling
- **Zero-Dependency Implant**: Pure Go (`parallax`), stdlib only (`crypto/ecdh`, `crypto/aes`). Compiles anywhere in seconds.
- **Native DNS TXT Tunneling**: Uppercase Base32 chunking (36-byte packets) over UDP (container `5353/udp`, host default `15353`). Ideal for restricted network egress practice and DFIR-Nexus DNS telemetry evaluation.
- **Wire Cryptography**: X25519 Diffie-Hellman + HKDF-SHA256 derivation + AES-256-GCM AEAD envelopes binding messages to session IDs.
- **Encrypted-at-Rest Results**: SQLite results encrypted under the server master key.

### 2. Havoc C2 — Windows Endpoint Evasion
- C++ Demon payload with indirect syscalls, API hashing, in-memory execution, and sleep obfuscation (Ekko/Zilean).
- Connects to the published teamserver port (`40056`) using the Havoc Qt5 client.

### 3. Sliver C2 — General Lateral Movement
- Feature-rich Go implants supporting Armory extensions, BOFs, and pivot listeners (TCP / SMB).
- Controlled via `sliver-client` on port `31337`.

### 4. Adaptix C2 — Multiplayer Operations
- Go/C++ post-exploitation framework with a Qt GUI client (`:4321`).
- Multi-listener matrix: HTTP/S, DNS/DoH, SMB, TCP Beacon + TCP/mTLS Gopher.

---

## Using With CADRE

## Documentation & Guides

- **[Field Practice & Study Guide](Doc/PRACTICE-GUIDE.md)** — Complete step-by-step tutorial, hands-on lab modules, DNS covert tunneling walk-through, and DFIR-Nexus detection synergy.
- **[Docker Architecture Reference](Doc/Docker.md)** — In-depth container layout, listener tuning, volume persistence, and isolation mechanics.
