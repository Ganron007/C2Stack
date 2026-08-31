# Mythic in C2Stack

Mythic is the *only* framework here that is not built from vendored source:
upstream ships pre-built container images (same model as the base images the
other frameworks use), so the compose profile pins those images directly.

## What the `mythic` profile provides (verified)

| Service          | Image                                        | Role                                   |
|------------------|----------------------------------------------|----------------------------------------|
| `mythic_server`  | `ghcr.io/its-a-feature/mythic_server:v3.4.0.61` | Go server: DB init, webserver, rabbit wiring (port 17443 API, 17444 metrics); published host-side on `MYTHIC_UI_PORT` (default 7443) |
| `mythic_postgres`| `postgres:15`                                | Mythic SQL backend                     |
| `mythic_rabbitmq`| `rabbitmq:3-management`                      | Event/RPC broker + mgmt API (server requires the mgmt plugin) |

Verified on Docker Desktop 29.x (compose v5.3.1, flag order matter):
`docker compose --env-file .env --profile mythic up -d mythic_postgres mythic_rabbitmq mythic_server`
- Server completes all 6 init steps, container reports `healthy`.
- API listener answers on 17443 (plain HTTP; the HTTPS/UI layer of this build
  needs the official scaffold — see below) and `/static/` is auth-gated (401).

> Note: the v3.4.0.61 image binds 17443 (not 7443); the compose mapping is
> `MYTHIC_UI_PORT:17443`.

## Why not the full 4.0 stack?

Mythic 4.0 is a multi-container app (nginx + react + graphql + server +
postgres + rabbitmq + docs/jupyter). Wiring all of it by hand in this compose
is outside the usable scope; upstream's supported path is `mythic-cli`
(Unix-only, Linux host or orbstack — Docker Desktop on macOS/Windows is not
officially supported for C2 containers).

## Operator steps to make callbacks operational (Linux host)

```bash
git clone --depth 1 https://github.com/its-a-feature/Mythic
cd Mythic && sudo make            # builds mythic-cli
sudo ./mythic-cli install github https://github.com/MythicC2Profiles/http
sudo ./mythic-cli install github https://github.com/MythicAgents/apollo
sudo ./mythic-cli start
```

Then in the UI (`https://<host>:7443`, default admin `mythic_admin`/`mythic`):
create a "http" listener whose callback host is the redirector
(`http://<redirector-host>` + `/cdn/media/stream` prefix). The redirector
already routes `MYTHIC_URI_PREFIX=/cdn/media/stream` → `Mythic:80`.

## Env

- `MYTHIC_UI_PORT` — host port for the Web UI/API (default 7443).
- Admin credentials default: `mythic_admin` / `mythic` (change for real use).
