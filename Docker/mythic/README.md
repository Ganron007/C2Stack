# Mythic in C2Stack

Mythic is the *only* framework here that is not built from vendored source:
upstream ships pre-built container images (same model as the base images the
other frameworks use), so the compose profile pins those images directly.

## What the `mythic` profile provides (verified Sep 2026)

| Service          | Image                                        | Role                                   |
|------------------|----------------------------------------------|----------------------------------------|
| `mythic_server`  | `ghcr.io/its-a-feature/mythic_server:v3.4.0.61` | Go server: DB init, webserver, rabbit wiring (port 17443 API, 17444 gRPC); published host-side on `MYTHIC_UI_PORT` (default 7443) |
| `mythic_postgres`| `postgres:15`                                | Mythic SQL backend                     |
| `mythic_rabbitmq`| pinned RabbitMQ image                      | Event/RPC broker + mgmt API, vhost `mythic_vhost`; deployment password is `MYTHIC_RABBITMQ_PASSWORD` in the ignored `.env` |
| `mythic_apollo`  | `ghcr.io/mythicagents/apollo:v0.0.1.19`      | Apollo payload-type container: 81 commands, self-registers over RabbitMQ |
| `mythic_http`    | `ghcr.io/mythicc2profiles/http:v0.0.3.2`     | HTTP C2 profile container: serves the :80 listener agents call back to, self-registers over RabbitMQ |

Verified on Docker Desktop (compose v5.3.1, flag order matters):
`docker compose --env-file .env --profile mythic up -d` (after running the
bootstrap once so the ignored Apollo config is rendered)

- Server completes all 6 init steps, container reports `healthy`; API answers
  on 17443 (plain HTTP) and `/static/` is auth-gated (401).
- Apollo self-registers: 81 commands in the DB, `payloadtype.container_running=t`.
- HTTP C2 profile self-registers: `c2profile` table populated (`id=1, name=http,
  container_running=t`), HTTP listener live on :80 inside the container.
- JWT login: `POST /auth` with the operator name and `MYTHIC_ADMIN_PASSWORD` from the ignored `.env`.
- **Full payload build verified**: created an Apollo .exe (1.7 MB, valid MZ PE)
  with the http C2 profile, `callback_host=http://192.168.100.1` (redirector),
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
  from the working dir via Dynaconf. Bootstrap renders the ignored
  `apollo/rabbitmq_config.generated.json` with the generated broker password
  and mounts it as `rabbitmq_config.json` — this file also
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
- `callback_host` must be BARE (`http://<redir>`, no port — OPSEC rejects
  ports) and `post_uri` the FULL path (`/cdn/media/stream/data`): a rooted
  post_uri REPLACES any base path (`ParseURLAndPort`), so a path-carrying
  host + `data` silently phones `http://host/data` into the decoy.
- `c2_instance` in the create webhook must be a **JSON string**, not an object.
- `payloadDefinition` in createpayload_webhook must ALSO be a JSON string,
  and `build_parameters` a LIST of `{name, value}` (a dict is rejected, yet
  the build still queues with defaults — silent wrong-config builds).
- Webhook replies arrive as CONCATENATED json objects (`{...}{...}`).
- The `id` field (not `c2_profile_id`) is used in start_stop_profile_webhook.
- Apollo's `rabbitmq_config.json` MUST include `mythic_server_host` +
  `mythic_server_port` or payload uploads fail with "Cannot connect to host none".
- **Task output lives in the `response` table, never `task.stdout`**
  (Apollo's `process_response` sets Success with no output mapping, so the
  server never overwrites the creation-time notice). The portal reads
  `response` and returns inline output.
- Operator file upload is multipart ONLY (`task_upload_file_webhook`,
  field `file`); JSON bodies get "Missing file in form". Portal mirror:
  `POST /api/ops/mythic/upload`.
- Registry keying is broken upstream: any real path fails the build
  (`Config.cs: CS1009`, build params are stamped unescaped).
- Headless builds: `POST /api/ops/mythic/build` (returns uuid immediately;
  poll `/api/ops/mythic/build/<uuid>`; fetch
  `/api/ops/mythic/payload/<uuid>`). Include loader commands
  (`execute_coff`, `register_coff`, `register_file`) in `commands` or COFF
  flows fail with "unknown command".

## Why not v4.0?

Mythic v4.0 is still RC-only (`v4.0.0rc1`–`v4.0.0rc5` on GHCR; no GA tag).
v4 bundles C2 profiles inside the server image, but the RC tags are marked
"test containers — do not use" upstream. v3.4.0.61 + the separate
`MythicC2Profiles/http` container gives us the same capability today without
running RC software. When v4 goes GA, the upgrade path is: swap the server
image pin, drop the `mythic_http` service, and profiles register automatically.

## Data-preserving credential rotation

MYTHIC_ADMIN_PASSWORD, MYTHIC_DB_PASSWORD, and MYTHIC_RABBITMQ_PASSWORD are
persistent credentials. Editing .env alone does not change the password stored
in an initialized Mythic account, PostgreSQL role, or RabbitMQ user. Rotate
each backend credential in place, update the ignored runtime config, then
recreate the clients. Keep the named volumes: do not run docker compose down -v
or remove mythic_postgres during rotation.

1. Keep the API and broker reachable only from the trusted lab while rotating.
   Save a PostgreSQL custom-format dump, .env, and the generated Apollo config
   in an access-restricted backup location. Confirm the dump with pg_restore
   --list before changing anything.
2. Generate three independent random values (at least 32 random bytes each)
   and update the matching entries in the ignored Docker/.env. Never commit
   .env or mythic/apollo/rabbitmq_config.generated.json.
3. Change the Mythic operator password through the authenticated
   POST /api/v1.4/update_operator_password_webhook endpoint. Supply the
   operator's user ID and current password, and preserve the existing email
   field. Verify a new POST /auth login before proceeding.
4. Change the PostgreSQL role in place. For an interactive session, run
   docker exec -it c2stack-mythic_postgres-1 psql -U mythic_user -d mythic_db
   and then \password mythic_user; enter the new value at the prompts. Change
   the RabbitMQ user in place using rabbitmqctl change_password with
   mythic_user and the new password as arguments. Supply the broker value
   through a protected, non-logged input path rather than putting it in shell
   history or a checked-in script.
5. Re-render Apollo's ignored runtime config from .env by running
   .\docker-bootstrap.ps1 -Mythic -NoBuild on Windows or
   ./docker-bootstrap.sh --mythic --no-build on Linux/macOS. These bootstrap scripts
   preserve non-empty credentials and render the broker password into Apollo's
   runtime config.
6. Recreate the Mythic services so every client reloads the new values:

       docker compose --env-file .env --profile mythic up -d --force-recreate mythic_postgres mythic_rabbitmq mythic_server mythic_apollo mythic_http mythic_httpx portal

7. Verify the server is healthy, authenticate to PostgreSQL and RabbitMQ with
   the new values, log in to Mythic, and confirm Apollo plus the configured C2
   profiles are registered. Keep the protected backup until all checks pass.

### TLS before external exposure

The published Mythic API on MYTHIC_UI_PORT is plain HTTP, and its Compose
mapping binds the host port on all interfaces unless the deployment changes
that mapping. Do not expose this port directly to an untrusted network. Before
external access, keep the API behind a TLS-terminating proxy with a certificate
whose SAN matches the deployment's real DNS name; verify the chain and hostname
from a client outside the Docker network. The Havoc and Adaptix internal TLS
identities do not provide TLS for Mythic's REST API.

## Env

- `MYTHIC_UI_PORT` — host port for the REST API (default 7443).
- `MYTHIC_BACKEND_HOST=mythic_http` — redirector proxies `/cdn/media/stream`
  to the http C2 profile container (not the server).
- Admin username defaults to `mythic_admin`; bootstrap generates its password
  as `MYTHIC_ADMIN_PASSWORD` in the ignored `.env`.
