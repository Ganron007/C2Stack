# Mythic in C2Stack

Mythic is the *only* framework here that is not built from vendored source:
upstream ships pre-built container images (same model as the base images the
other frameworks use), so the compose profile pins those images directly.

## What the `mythic` profile provides (verified)

| Service          | Image                                        | Role                                   |
|------------------|----------------------------------------------|----------------------------------------|
| `mythic_server`  | `ghcr.io/its-a-feature/mythic_server:v3.4.0.61` | Go server: DB init, webserver, rabbit wiring (port 17443 API, 17444 metrics); published host-side on `MYTHIC_UI_PORT` (default 7443) |
| `mythic_postgres`| `postgres:15`                                | Mythic SQL backend                     |
| `mythic_rabbitmq`| `rabbitmq:3-management`                      | Event/RPC broker + mgmt API, vhost `mythic_vhost` (user `mythic_user` / `mythic`) |
| `mythic_apollo`  | `ghcr.io/mythicagents/apollo:v0.0.1.19`      | Apollo payload-type container: self-registers over RabbitMQ sync queues (NO mythic-cli, NO docker socket) |

Verified on Docker Desktop (compose v5.3.1, flag order matters):
`docker compose --env-file .env --profile mythic up -d mythic_postgres mythic_rabbitmq mythic_server mythic_apollo`

- Server completes all 6 init steps, container reports `healthy`; API answers
  on 17443 (plain HTTP) and `/static/` is auth-gated (401).
- Apollo connects: logs show `started listening for messages on
  apollo_pt_on_new_callback` / `apollo_pt_rpc_resync`; the server registers the
  `Apollo` payload type (visible in `postgres.public.payloadtype`).
- JWT login verified: `POST /auth` with `{"username":"mythic_admin","password":"mythic"}`
  returns a token; webhooks live under `/api/v1.4/*_webhook`.

## How registration works (no mythic-cli)

Mythic 3.4's server is a **pure orchestrator** — it spawns no containers and
needs no docker socket. Payload types / C2 profiles / translators are *sibling
containers* that self-register through RabbitMQ sync queues (`pt_sync`,
`c2_sync`, `tr_sync`). Upstream's `mythic-cli` is only a docker-compose
generator; the wiring in `Docker/docker-compose.yml` replaces it directly.

The sibling container reads `rabbitmq_config.json` from its working directory
(no env vars affect the broker host — the apollo image pins
`mythic-container==0.6.14`, which loads that file via Dynaconf). C2Stack owns
`apollo/rabbitmq_config.json`: host `mythic_rabbitmq`, vhost `mythic_vhost`,
user/pass matching the compose rabbitmq service.

## What is still blocked, and why (evidence-based)

C2-profile containers (http / websocket / smb / tcp / azure_blob) are required
for agent callback traffic. They are **not obtainable from upstream anymore**:

- Not in the v3.4.0.61 image (mounted `/mythic` + image contents contain no
  profile code).
- Not in the repo at any reachable tag (v3.3.0.136 … v4.0.0rc5): the
  `c2_profiles/` directory is absent from tags **and** from commit history
  (`/commits?path=c2_profiles` returns nothing — history was rewritten).
- MythicAgents/MythicMeta orgs publish templates (`Mythic_C2_Container`),
  not the profile implementations.
- Therefore the operator steps below would ALSO fail with mythic-cli on a
  Linux host — the original reviewer gap (mythic-cli install) cannot be
  satisfied with code that no longer exists publicly.

Consequence: the Apollo payload type registers and can be queried/tasked over
the REST API, but building a callback-ready payload needs an http profile
instance (server logs: `Payload Type supports C2 Profile that's not yet
installed`). The planned fix is **Mythic v4 when it goes GA**: v4 ships
agents AND C2 profiles inside the server image (single-container model, no
profile containers at all). The `latest` GHCR tag is explicitly marked
"test containers — do not use"; v4 is RC-only today, so C2Stack stays pinned
to 3.4.0.61 (the highest stable tag).

## Operator notes

- REST surface: `http://<host-ip-on-vmnet2>:7443` (plain HTTP; see the note in
  `Doc/Docker.md` "Verified state"). Admin credentials default
  `mythic_admin` / `mythic` (change for real use).
- The browser UI (nginx + react + graphql) is upstream optional scaffolding and
  is not shipped; operations happen over the REST/psql surface.
- When v4 GA lands: swap the image pins, drop the rabbitmq + apollo services,
  and the http profile registers automatically — then set its callback host to
  the redirector under `/cdn/media/stream` (the redirector already routes
  `MYTHIC_URI_PREFIX` → `mythic_server:80`).
