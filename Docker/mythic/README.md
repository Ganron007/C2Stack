# Mythic in C2Stack

Mythic is the *only* framework here that is not built from vendored source:
upstream ships pre-built container images (same model as the base images the
other frameworks use), so the compose profile pins those images directly.

## What the `mythic` profile provides (verified Sep 2026)

| Service          | Image                                        | Role                                   |
|------------------|----------------------------------------------|----------------------------------------|
| `mythic_server`  | `ghcr.io/its-a-feature/mythic_server:v3.4.0.61` | Go server: DB init, webserver, rabbit wiring (port 17443 API, 17444 gRPC); published host-side on `MYTHIC_UI_PORT` (default 7443) |
| `mythic_postgres`| `postgres:15`                                | Mythic SQL backend                     |
| `mythic_rabbitmq`| `rabbitmq:3-management`                      | Event/RPC broker + mgmt API, vhost `mythic_vhost` (user `mythic_user` / `mythic`) |
| `mythic_apollo`  | `ghcr.io/mythicagents/apollo:v0.0.1.19`      | Apollo payload-type container: 81 commands, self-registers over RabbitMQ |
| `mythic_http`    | `ghcr.io/mythicc2profiles/http:v0.0.3.2`     | HTTP C2 profile container: serves the :80 listener agents call back to, self-registers over RabbitMQ |

Verified on Docker Desktop (compose v5.3.1, flag order matters):
`docker compose --env-file .env --profile mythic up -d`

- Server completes all 6 init steps, container reports `healthy`; API answers
  on 17443 (plain HTTP) and `/static/` is auth-gated (401).
- Apollo self-registers: 81 commands in the DB, `payloadtype.container_running=t`.
- HTTP C2 profile self-registers: `c2profile` table populated (`id=1, name=http,
  container_running=t`), HTTP listener live on :80 inside the container.
- JWT login: `POST /auth` with `{"username":"mythic_admin","password":"mythic"}`.
- **Full payload build verified**: created an Apollo .exe (1.7 MB, valid MZ PE)
  with the http C2 profile, `callback_host=http://192.168.77.1` (redirector),
  `callback_port=80`, `X-Request-ID: cadre-c2` header. Build completed
  `success` — the implant is callback-ready through the redirector.

## How registration works (no mythic-cli)

Mythic 3.4's server is a **pure orchestrator** — it spawns no containers and
needs no docker socket. Payload types / C2 profiles / translators are *sibling
containers* that self-register through RabbitMQ sync queues (`pt_sync`,
`c2_sync`, `tr_sync`). Upstream's `mythic-cli` is only a docker-compose
generator; the wiring in `Docker/docker-compose.yml` replaces it directly.

Two different container frameworks are in play:
- **Apollo** (Python, `mythic-container==0.6.14`): reads `rabbitmq_config.json`
  from the working dir via Dynaconf (no envvar prefix → env vars don't affect
  broker host). C2Stack mounts `apollo/rabbitmq_config.json` — this file also
  sets `mythic_server_host`/`mythic_server_port` so built payloads upload back
  to the server via `/direct/upload`.
- **http C2 profile** (Go, `MythicContainer v1.5.1`): reads broker config from
  env vars (`RABBITMQ_HOST`, `MYTHIC_SERVER_HOST`, etc.) — no config file
  mount needed. The compose service sets these directly.

## Operator steps to create a callback-ready payload

All done via the REST API (no mythic-cli, no browser UI needed):

1. **Auth**: `POST /auth` → JWT
2. **Create C2 instance**: `POST /api/v1.4/create_c2parameter_instance_webhook`
   with `instance_name`, `c2profile_id=1` (http), and `c2_instance` (JSON
   string with `callback_host`, `callback_port`, `headers`, etc.)
3. **Start the profile**: `POST /api/v1.4/start_stop_profile_webhook` with
   `id=1` (http c2profile id), `action=start`
4. **Build payload**: `POST /api/v1.4/createpayload_webhook` with
   `payloadDefinition` containing `payload_type=apollo`, `selected_os=Windows`,
   `c2_profiles=[{c2_profile:"http", c2_profile_parameters:{...}}]`
5. **Download**: `GET /direct/download/<payload_uuid>` (JWT auth) → .exe

Key gotchas learned (all verified):
- `callback_host` must NOT include a port (the OPSEC check rejects it; port
  goes in `callback_port` separately).
- `c2_instance` in the create webhook must be a **JSON string**, not an object.
- The `id` field (not `c2_profile_id`) is used in start_stop_profile_webhook.
- Apollo's `rabbitmq_config.json` MUST include `mythic_server_host` +
  `mythic_server_port` or payload uploads fail with "Cannot connect to host none".

## Why not v4.0?

Mythic v4.0 is still RC-only (`v4.0.0rc1`–`v4.0.0rc5` on GHCR; no GA tag).
v4 bundles C2 profiles inside the server image, but the RC tags are marked
"test containers — do not use" upstream. v3.4.0.61 + the separate
`MythicC2Profiles/http` container gives us the same capability today without
running RC software. When v4 goes GA, the upgrade path is: swap the server
image pin, drop the `mythic_http` service, and profiles register automatically.

## Env

- `MYTHIC_UI_PORT` — host port for the REST API (default 7443).
- `MYTHIC_BACKEND_HOST=mythic_http` — redirector proxies `/cdn/media/stream`
  to the http C2 profile container (not the server).
- Admin credentials default: `mythic_admin` / `mythic` (change for real use).
