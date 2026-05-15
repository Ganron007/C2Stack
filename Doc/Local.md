# redStack on Local VMs — Full Documentation

## 1. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                     Host Machine (Your Computer)                   │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │  Hypervisor: VirtualBox / VMware / libvirt                  │   │
│  │                                                             │   │
│  │  ┌───────────────────────────────────────────────────────┐  │   │
│  │  │  Private Network (NAT) 10.50.0.0/16                  │  │   │
│  │  │  redStack Team Network                                │  │   │
│  │  │                                                       │  │   │
│  │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐            │  │   │
│  │  │  │  mythic  │  │  sliver  │  │  havoc   │            │  │   │
│  │  │  │ 10.50.0.10│  │10.50.0.11│  │10.50.0.12│            │  │   │
│  │  │  │ 2vCPU/4GB │  │2vCPU/4GB │  │2vCPU/4GB│            │  │   │
│  │  │  │C2:80/443 │  │C2:80/443 │  │C2:80/443│            │  │   │
│  │  │  │UI:7443   │  │Mux:31337 │  │TS:40056 │            │  │   │
│  │  │  └──────────┘  └──────────┘  └──────────┘            │  │   │
│  │  │                                                       │  │   │
│  │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐            │  │   │
│  │  │  │guacamole │  │ windows  │  │   kali   │            │  │   │
│  │  │  │10.50.0.20│  │10.50.0.30│  │10.50.0.40│            │  │   │
│  │  │  │2vCPU/2GB │  │4vCPU/8GB │  │2vCPU/4GB │            │  │   │
│  │  │  │Portal:443│  │RDP:3389  │  │SSH:22    │            │  │   │
│  │  │  │+ Host FW │  │          │  │XRDP:3389 │            │  │   │
│  │  │  └──────────┘  └──────────┘  └──────────┘            │  │   │
│  │  └───────────────────────────────────────────────────────┘  │   │
│  │                                                             │   │
│  │  ┌───────────────────────────────────────────────────────┐  │   │
│  │  │  Host-Only / Bridged Network 10.60.0.0/16            │  │   │
│  │  │  redStack Redirector Network                          │  │   │
│  │  │                                                       │  │   │
│  │  │  ┌──────────────────┐   Port Forwarding:             │  │   │
│  │  │  │   redirector     │   Host:8443 → guac:443         │  │   │
│  │  │  │   10.60.0.10     │   Host:8080 → redirector:80    │  │   │
│  │  │  │   2vCPU/2GB      │   Host:8443 → redirector:443   │  │   │
│  │  │  │   Apache Proxy   │                                │  │   │
│  │  │  └──────────────────┘                                │  │   │
│  │  └───────────────────────────────────────────────────────┘  │   │
│  └────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

## 2. Network Architecture

Unlike cloud deployments, local VMs use virtualized networking:

| Network | Purpose | CIDR | Type |
|---------|---------|------|------|
| Team Network | All internal lab VMs | 10.50.0.0/16 | NAT (NAT) |
| Redirect Network | Redirector (simulates external) | 10.60.0.0/16 | Host-Only |
| Host Port Forwarding | Public access to lab | Dynamic | Host→VM NAT |

**Routing between networks** is handled by Guacamole as the gateway (dual-homed) or by the host machine's routing table.

For **internet C2 callbacks** (practicing with real redirector), you need:
1. Port forwarding on your home router: `WAN:443 → Host:8443 → redirector:443`
2. Dynamic DNS domain pointing to your home IP
3. Your home router may need a static IP or DDNS service

## 3. Instance Resource Allocation

| VM | vCPU | RAM | Disk | Guest OS | Vagrant Box |
|----|------|-----|------|----------|-------------|
| mythic | 2 | 4GB | 30GB | Debian 12 | `debian/bookworm64` |
| guacamole | 2 | 2GB | 20GB | Debian 12 | `debian/bookworm64` |
| windows | 4 | 8GB | 80GB | Windows Server 2022 | `gusztavvargadr/windows-server-2022-standard` |
| redirector | 1 | 1GB | 20GB | Debian 12 | `debian/bookworm64` |
| sliver | 2 | 2GB | 25GB | Debian 12 | `debian/bookworm64` |
| havoc | 2 | 4GB | 30GB | Debian 12 | `debian/bookworm64` |
| kali | 2 | 4GB | 50GB | Kali Linux | `kalilinux/rolling` |

**Total:** ~14 vCPU, ~25GB RAM, ~255GB disk

## 4. Prerequisites

### Software to Install

1. **Vagrant** >= 2.3 — https://developer.hashicorp.com/vagrant/downloads
2. **VirtualBox** >= 7.0 — https://www.virtualbox.org (or VMware/libvirt)
3. **Git** — for cloning the repo
4. **At least 25GB free RAM** — this is the biggest constraint
5. **At least 100GB free disk** — VM images + disks

### Vagrant Plugins

```bash
vagrant plugin install vagrant-vbguest  # Auto-install VirtualBox Guest Additions
vagrant plugin install vagrant-hostmanager  # Optional: auto-manage /etc/hosts
```

### Windows License Note

The Windows Server 2022 box is a **180-day evaluation** image. After 180 days, it will begin shutting down every hour. Options:
- Redeploy (destroy + up) to refresh the eval period
- License via Volume Licensing or retail key

## 5. Step-by-Step Deployment Guide

### Step 1: Clone and Prepare

```bash
cd redStack/Local
```

Review and edit the Vagrantfile to match your hardware. Key settings at the top of the file:

```ruby
# Adjust based on your machine
$ram_adjustments = {
  'mythic'      => { cpus: 2, memory: 4096 },
  'guacamole'   => { cpus: 2, memory: 2048 },
  'windows'     => { cpus: 4, memory: 8192 },
  'redirector'  => { cpus: 1, memory: 1024 },
  'sliver'      => { cpus: 2, memory: 2048 },
  'havoc'       => { cpus: 2, memory: 4096 },
  'kali'        => { cpus: 2, memory: 4096 },
}

# Adjust SSH password
$ssh_password = "redStack2024!"

# Your host's LAN IP for redirector callback simulation
# Only needed if testing C2 callbacks from other machines on your network
$your_lan_ip = "192.168.1.100/32"
```

### Step 2: Deploy All VMs

```bash
cd Local

# Deploy all VMs (sequential to avoid resource contention)
vagrant up

# Or deploy selectively (to save resources)
vagrant up guacamole mythic
vagrant up sliver havoc kali
vagrant up windows      # Windows takes ~15 min alone
vagrant up redirector
```

**Total time:** ~45-90 minutes depending on hardware, internet speed, and whether boxes are cached.

### Step 3: Verify Deployment

```bash
# Check all VMs are running
vagrant status

# SSH into any VM to check
vagrant ssh mythic
vagrant ssh guacamole
```

### Step 4: Access the Lab

1. **Guacamole Portal:** `https://localhost:8443/guacamole`
   - Login: `guacadmin` / password from Vagrantfile (`$ssh_password`)
2. Pre-configured connections (same as cloud version)

### Step 5: Configure C2 Callbacks (Optional)

For C2 callbacks from outside your LAN:

```bash
# On your home router, forward:
#   WAN 443 → Host 8443 → redirector 443
#   WAN 80  → Host 8080 → redirector 80

# Set up dynamic DNS: c2.your-ddns.net → your home WAN IP
# Update redirector_domain in Guacamole to use your DDNS domain
```

### Step 6: Clean Up

```bash
# Stop all VMs (preserves state)
vagrant halt

# Destroy all VMs (frees disk space)
vagrant destroy -f

# Destroy a single VM
vagrant destroy windows -f
```

## 6. Working Guide — Operating the Lab

### Access Methods

| VM | Access | Command |
|----|--------|---------|
| mythic | SSH | `vagrant ssh mythic` |
| guacamole | SSH | `vagrant ssh guacamole` |
| redirector | SSH | `vagrant ssh redirector` |
| sliver | SSH | `vagrant ssh sliver` |
| havoc | SSH | `vagrant ssh havoc` |
| kali | SSH | `vagrant ssh kali` |
| windows | RDP | Use Guacamole or: `vagrant rdp windows` |
| All | Guacamole | `https://localhost:8443/guacamole` |

### Using the Lab

Everything works identically to the cloud version once VMs are running:

#### Mythic C2
```bash
vagrant ssh mythic
cd /opt/Mythic
./mythic-cli status
# Web UI: https://localhost:8443/guacamole → Windows (RDP) → browser at https://mythic:7443
```

#### Sliver C2
```bash
vagrant ssh sliver
sliver-client
```

#### Havoc C2
```bash
vagrant ssh havoc
~/build_havoc.sh  # First time only (15-25 min)
# Then use Guacamole → Havoc Desktop (VNC)
```

#### Kali Tools
```bash
vagrant ssh kali
sudo install-kali-tools
```

### Snapshot Management (Critical Feature)

Unlike cloud deployments, local VMs support snapshots:

```bash
# Take snapshot before a practice session
vagrant snapshot save pre-session

# Take snapshot of individual VM
vagrant snapshot save windows pre-exploit

# Restore after session
vagrant snapshot restore pre-session

# List snapshots
vagrant snapshot list
```

## 7. Local Setup Script Changes vs AWS

The setup scripts in `Local/setup_scripts/` are adapted from the AWS versions with these key changes:

### Removed
- All AWS IMDS (169.254.169.254) metadata calls
- All AWS-specific `user_data` templating (Terraform `templatefile()`)
- Security group rules (replaced by UFW and VirtualBox host-only firewall)
- VPC peering references (replaced by dual-NIC routing)

### Changed
- `/etc/hosts` entries use static IPs (10.50.0.x, 10.60.0.x) instead of Terraform variables
- SSH password auth is enabled for all (not just VPC CIDRs) since local networks are trusted
- UFW rules reference the host-only network CIDRs directly
- Guacamole Docker compose uses environment variables passed via `Vagrantfile` shell provisioner
- Windows password is passed directly via Vagrant (not encrypted)
- No Elastic IP needed — Guacamole is accessed via `localhost:8443`
- Redirector has no public IP — it uses host-only network

### Added
- `wait_for_guacamole.sh` — waits for Guacamole Docker containers to be ready
- `provision_windows.ps1` — adapted for local Windows setup

## 8. Practice How-To Scenarios

### Scenario 1: Full Internal Kill Chain (No Internet Needed)

All callbacks stay within the host-only network:

1. `vagrant up` all VMs
2. Access Guacamole at `https://localhost:8443/guacamole`
3. Open Windows (RDP) — it has internal access to all lab machines
4. From Windows, browse to `https://mythic:7443`
5. Generate Apollo payload, transfer to Windows desktop
6. Execute — callback goes through redirector to mythic (all local traffic)
7. Practice post-exploitation entirely offline

### Scenario 2: Practice Snapshot Workflow

```bash
# Before starting
vagrant snapshot save fresh-deploy

# Do your practice (make a mess)
# Install random tools, create users, break things

# When done — instant restore!
vagrant snapshot restore fresh-deploy

# Everything is back to clean state in seconds
```

### Scenario 3: Simulate Payload Delivery

```bash
# On host, download a payload
curl -o payload.exe http://localhost:8080/edge/cache/assets/payload.exe

# Transfer to Windows VM
vagrant upload payload.exe /Users/Administrator/Desktop/payload.exe windows

# On Windows (via Guacamole RDP), execute payload.exe
# Watch the callback in redirector logs:
vagrant ssh redirector -c "sudo tail -f /var/log/apache2/redirector-access.log"
```

### Scenario 4: Cross-Network Pivoting

1. Guacamole sits on both networks (10.50.0.0/16 and 10.60.0.0/16)
2. From Windows (10.50.0.30/16), access redirector (10.60.0.10) through Guacamole
3. Generate a Sliver implant that calls back to `10.60.0.10` (redirector internal IP)
4. From the host machine, access redirector via port forwarding: `localhost:8080`

### Scenario 5: Resource-Constrained Mode

If you don't have 25GB RAM, deploy a subset of VMs:

```bash
# Minimal lab (just C2 + Windows)
vagrant up mythic windows guacamole redirector

# C2-only lab (skip Windows and Kali)
vagrant up mythic sliver havoc guacamole redirector

# Single C2 (smallest footprint)
vagrant up mythic guacamole redirector
```

## 9. Performance Tuning

### Reduce Resource Usage

Edit the `Vagrantfile` to lower allocations:

```ruby
$ram_adjustments = {
  'mythic'      => { cpus: 2, memory: 2048 },  # Down from 4096
  'guacamole'   => { cpus: 1, memory: 1024 },  # Down from 2048
  'windows'     => { cpus: 2, memory: 4096 },  # Minimum for Windows
  'redirector'  => { cpus: 1, memory: 512 },
  'sliver'      => { cpus: 1, memory: 1024 },
  'havoc'       => { cpus: 2, memory: 2048 },
  'kali'        => { cpus: 1, memory: 2048 },
}
```

### Enable SSD Caching

For VirtualBox:
```bash
# Use SSD mode for disk
VBoxManage storageattach "redstack_mythic" --storagectl "SATA Controller" \
  --port 0 --device 0 --type hdd --medium --nonrotational on
```

### Use Linked Clones

Vagrantfile already enables linked clones, which share a base image and only store deltas:

```ruby
config.vm.provider "virtualbox" do |vb|
  vb.linked_clone = true
end
```

## 10. Troubleshooting (Local-Specific)

| Symptom | Fix |
|---------|-----|
| `VT-x is not available` | Enable virtualization in BIOS/UEFI |
| `Insufficient memory` | Close other apps, reduce RAM in Vagrantfile |
| Windows evaluation expired | `vagrant destroy windows && vagrant up windows` |
| Guacamole not loading | Check Docker: `vagrant ssh guacamole` → `docker ps` |
| VMs cannot reach internet | VirtualBox NAT usually works; check `VBoxNetDHCP` |
| Vagrant box download slow | Pre-download: `vagrant box add debian/bookworm64` |
| Port conflicts (8443/8080) | Change forwarded ports in Vagrantfile |
| Kali rename fails | `vagrant ssh kali` → check if user is `kali` or `admin` |
| Havoc build takes forever | First build compiles Qt5 + Go — expect 15-25 min |
| Snapshot disk full | Snapshots use extra disk — delete old ones: `vagrant snapshot list && vagrant snapshot delete <name>` |

## 11. Vagrantfile Reference

The `Local/Vagrantfile` defines all 7 VMs with:
- Static private IPs (10.50.0.x, 10.60.0.x)
- Port forwarding (host:8443 → guacamole:443, host:8080 → redirector:80)
- Shell provisioners running the adapted setup scripts
- Synced folders for file transfer
- Linked clone support for disk efficiency
- Adjustable CPU/RAM per VM
