# redStack Local-Lite — Single-VM Simplified Deployment

## Rationale

The full 7-VM local deployment requires ~25GB RAM and complex multi-network orchestration. Local-Lite runs everything on **1 Debian VM** (optionally +1 Windows VM) — cutting resource requirements by ~70% while preserving all C2 operations and practice scenarios.

## Architecture

```
Host Machine
  │  vagrant up redstack-lite
  │
  ├── Port 8443 ──▶ Guacamole (web SSH/RDP portal)
  ├── Port 8080 ──▶ Apache Redirector (C2 frontend, HTTP)
  ├── Port 8443 ──▶ Apache Redirector (C2 frontend, HTTPS)
  ├── Port 7443 ──▶ Mythic Web UI (direct access)
  │
  └── [optional] vagrant up windows-lite
        └── Port 3389 ──▶ Windows Server 2022 (RDP via Guacamole)
```

Everything runs inside one Debian 12 VM. The redirector proxies to localhost C2 services. No VPC simulation, no inter-VM routing.

## Requirements

| Resource | Full Local (7 VMs) | Local-Lite |
|----------|-------------------|------------|
| RAM | ~25 GB | **~8 GB** |
| vCPUs | ~14 | **~4** |
| Disk | ~255 GB | **~60 GB** |
| VMs to manage | 7 | **1 (+1 optional)** |
| Setup scripts | 5 | **1** |

## What You Get

| Service | Runs As | Access |
|---------|---------|--------|
| **Guacamole** | Docker Compose | `https://localhost:8443/guacamole` |
| **Apache Redirector** | systemd (Apache2) | `http://localhost:8080` / `https://localhost:8443` |
| **Mythic C2** | Docker Compose | `https://localhost:7443` (or via Guacamole → Windows browser) |
| **Sliver C2** | systemd | `vagrant ssh` → `sliver-client` |
| **Havoc C2** | systemd + VNC | Build: `~/build_havoc.sh`, then Guacamole → Havoc Desktop (VNC) |
| **Kali Tools** | apt packages | `vagrant ssh` → `kali-tools` |
| **Windows** | Separate VM (optional) | RDP via Guacamole |

## C2 Traffic Flow (Same as Cloud)

```
[Implant on Windows]
      │
      │  HTTP GET http://localhost:8080/cdn/media/stream/payload
      │  Header: X-Request-ID: redstack-lite
      ▼
┌──────────────┐
│  Redirector  │  (same VM, port 8080)
│  Apache      │
└──────┬───────┘
       │
       ├─ /cdn/media/stream/*  ──▶ localhost:80  (Mythic container)
       ├─ /cloud/storage/objects/* ──▶ localhost:80 (Sliver)
       └─ /edge/cache/assets/*  ──▶ localhost:80  (Havoc)
```

The redirector proxies to the C2 service's *container/Host port* (not a separate machine), but the header validation, URI routing, and decoy page all work identically.

## Vagrantfile (Conceptual)

```ruby
Vagrant.configure("2") do |config|
  config.vm.define "redstack-lite", primary: true do |m|
    m.vm.box = "debian/bookworm64"
    m.vm.hostname = "redstack"
    m.vm.network "forwarded_port", guest: 443, host: 8443   # Guacamole + Redirector HTTPS
    m.vm.network "forwarded_port", guest: 80, host: 8080    # Redirector HTTP
    m.vm.network "forwarded_port", guest: 7443, host: 7443  # Mythic Web UI
    m.vm.provider "virtualbox" do |vb|
      vb.memory = 8192
      vb.cpus = 4
    end
    m.vm.provision "shell", path: "setup_lite.sh"
  end
end
```

## setup_lite.sh (Single Script — All Services)

The single installer script runs these steps sequentially:

### Phase 1: OS + Docker
```
- apt update/upgrade
- Install: docker.io, docker-compose, git, curl, ufw, build-essential, apache2
- Enable Docker, start system services
- Set SSH password auth for local access
```

### Phase 2: Guacamole
```
- docker-compose up for guacd + postgres + guacamole (same as AWS version)
- NGINX reverse proxy with self-signed SSL
- Auto-create connections via API:
  Windows (RDP), Mythic (SSH), Sliver (SSH), Havoc (SSH+VNC), Kali (SSH)
```

### Phase 3: Apache Redirector
```
- Enable mods: proxy, rewrite, ssl, headers
- self-signed SSL cert with IP SAN
- VirtualHost config with header validation:
  X-Request-ID: redstack-lite → route to C2 backends
  No header → CloudEdge CDN decoy page
- Generate test_redirector.sh
```

### Phase 4: Mythic C2
```
- git clone Mythic to /opt/Mythic
- make (build CLI)
- mythic-cli start (Docker-based)
- Install HTTP C2 profile + Apollo agent
- Set admin password
- Enable systemd service for autostart
```

### Phase 5: Sliver C2
```
- curl https://sliver.sh/install | bash
- systemd service setup
- Generate admin operator config
- Create C2 profile JSON with header validation
- Enable swap (Go compiler needs it)
```

### Phase 6: Havoc C2
```
- Install build deps: cmake, nasm, mingw-w64, qt5, boost
- Install TigerVNC server + XFCE4
- Havoc profile creation
- Systemd service units for teamserver + VNC
- Drop build_havoc.sh (user runs manually: ~/build_havoc.sh)
```

### Phase 7: Kali Tools
```
- Install: nmap, enum4linux-ng, smbmap, mitm6, seclists,
  gobuster, ldap-utils, impacket-scripts, netexec, bloodhound,
  certipy-ad, responder, hashcat, john, pipx
- Create /usr/local/sbin/install-kali-tools (idempotent)
```

## Windows VM (Optional — Second Vagrant Box)

```ruby
config.vm.define "windows-lite" do |w|
  w.vm.box = "gusztavvargadr/windows-server-2022-standard"
  w.vm.hostname = "windows"
  w.vm.network "forwarded_port", guest: 3389, host: 3389
  w.vm.provider "virtualbox" do |vb|
    vb.memory = 4096
    vb.cpus = 2
  end
  w.vm.provision "shell", inline: <<-SHELL
    Add-Content -Path "$env:windir\\System32\\drivers\\etc\\hosts" -Value "`r`n10.0.2.15    redstack"
    net user Administrator "redStack2024!"
    Set-MpPreference -DisableRealtimeMonitoring $true
  SHELL
end
```

Windows reaches the redstack-lite VM via VirtualBox's default NAT gateway (usually `10.0.2.15`). No extra networking needed.

## Practice Scenarios (All Work)

| Scenario | How it works on Local-Lite |
|----------|---------------------------|
| **Deploy first beacon** | Mythic web UI at `https://localhost:7443` → generate Apollo → download → execute on Windows → callback via `localhost:8080/cdn/media/stream/` |
| **Cross-C2 pivot** | Sliver listener on `localhost:80` → generate implant → SCP to Windows → callback → Sliver `socks5` pivot |
| **Redirector OPSEC** | `curl -H "X-Request-ID: redstack-lite" http://localhost:8080/...` → gets C2. No header → decoy page |
| **Full kill chain** | Kali tools in SSH → scan Windows → deploy Sliver beacon → BloodHound → Certipy → laterally move |
| **All-in-one snapshot** | `vagrant snapshot save pre-session` → practice → `vagrant snapshot restore pre-session` |

## Comparison: Full Local vs Local-Lite

| Feature | Full Local (7 VMs) | Local-Lite (1-2 VMs) |
|---------|-------------------|---------------------|
| Realistic network segmentation | Yes (2 peered networks) | No (all localhost) |
| Realistic public IP redirector | No (both use port forwarding) | No (same) |
| All C2 frameworks work | Yes | Yes |
| Guacamole access portal | Yes | Yes |
| Header + URI gating | Yes (works across VNets) | Yes (works on localhost) |
| Redirector decoy page | Yes | Yes |
| Windows target machine | Yes | Yes (optional 2nd VM) |
| Kali AD tools | Yes | Yes |
| VPN tunnel for CTF ranges | Not applicable (local) | Not applicable (local) |
| Snapshot management | Per-VM snapshots | Single snapshot covers everything |
| RAM required | ~25 GB | ~8-12 GB |
| Disk required | ~255 GB | ~60-100 GB |
| Setup time | ~60-90 min | ~30-45 min |
| Maintenance burden | 7 boxes + 5 scripts | 1 script, 1 box |

## When to Use Which

**Use Full Local (7 VMs) when:**
- You have 25GB+ RAM to spare
- You want to practice network-level detection evasion
- You're training OPSEC concepts that rely on separate hosts
- You want the closest simulation to a real cloud deployment

**Use Local-Lite (1-2 VMs) when:**
- You have limited RAM (8-16GB)
- You just want to learn C2 framework operations (Mythic/Sliver/Havoc)
- You want the fastest possible setup path
- You mainly care about payload generation, callback handling, and post-exploitation
- Snapshot/restore workflow matters more than network fidelity
