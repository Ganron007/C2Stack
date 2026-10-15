#!/usr/bin/env bash
# C2Stack Docker practice-lab bootstrap (Linux/macOS host).
# Copies .env.example -> .env if missing, checks Docker, then builds + starts
# the stack. Pass --mythic, --adaptix, or --all to enable optional frameworks.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  if [ ! -f .env.example ]; then echo ".env.example missing; aborting." >&2; exit 1; fi
  cp .env.example .env
  echo "[bootstrap] Created .env from .env.example — review it before production use."
fi

# The Windows bootstrap may have written CRLF dotenv lines. Normalize before
# shell parsing so a trailing carriage return cannot become a stray command or
# part of a password passed to a framework container.
sed -i.bak $'s/\r$//' .env
rm -f .env.bak

if ! docker info >/dev/null 2>&1; then
  echo "Docker does not appear to be running. Start Docker and retry." >&2
  exit 1
fi
echo "[bootstrap] Docker is available."

# Mythic stores its database/admin credentials in persistent state. Do not
# silently replace legacy credentials when an initialized volume exists.
project_name="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env | tail -n 1 | sed 's/[[:space:]]#.*$//' | tr -d '\r')"
project_name="${project_name:-c2stack}"
mythic_volume="${project_name}_mythic_postgres"
if docker volume inspect "$mythic_volume" >/dev/null 2>&1; then
  missing_mythic=""
  for name in MYTHIC_ADMIN_PASSWORD MYTHIC_DB_PASSWORD MYTHIC_RABBITMQ_PASSWORD; do
    if ! grep -Eq "^${name}=.+$" .env; then missing_mythic="$missing_mythic $name"; fi
  done
  if [ -n "$missing_mythic" ]; then
    echo "An initialized Mythic volume exists but its credentials are not in .env:$missing_mythic" >&2
    echo "Restore the matching credentials to .env before starting this stack. Do not reset the volume." >&2
    exit 3
  fi
fi

# Per-deployment credentials stay in the ignored local .env and are never
# printed. Existing non-empty values, including migrated Mythic credentials,
# are preserved across bootstrap runs.
ensure_secret() {
  name="$1"
  current="$(sed -n "s/^${name}=//p" .env | head -n 1)"
  if [ -n "$current" ]; then return; fi
  value="$(openssl rand -hex 32)"
  if grep -q "^${name}=" .env; then
    sed -i.bak "s/^${name}=.*/${name}=${value}/" .env
    rm -f .env.bak
  else
    printf '%s=%s\n' "$name" "$value" >> .env
  fi
}
ensure_secret HAVOC_OPERATOR_PASSWORD
ensure_secret HAVOC_SERVICE_PASSWORD
ensure_secret ADAPTIX_TEAMSERVER_PASSWORD
ensure_secret MYTHIC_ADMIN_PASSWORD
ensure_secret MYTHIC_DB_PASSWORD
ensure_secret MYTHIC_RABBITMQ_PASSWORD
ensure_secret KALI_VNC_PASSWORD
ensure_secret C2STACK_API_KEY
echo "[bootstrap] Generated per-deployment framework, portal, and desktop credentials in the ignored .env file."

# Apollo 0.6.14 reads its RabbitMQ password from JSON rather than environment
# variables. Render the ignored runtime config from the same dotenv values
# Compose will use; never print the credential.
set -a
# shellcheck disable=SC1091
. ./.env
set +a
python3 - <<'PY'
import json
import os
from pathlib import Path

template_path = Path("mythic/apollo/rabbitmq_config.template.json")
output_path = Path("mythic/apollo/rabbitmq_config.generated.json")
config = json.loads(template_path.read_text(encoding="utf-8"))
password = os.environ.get("MYTHIC_RABBITMQ_PASSWORD", "")
if not password:
    raise SystemExit("MYTHIC_RABBITMQ_PASSWORD is required to render Apollo config")
config["rabbitmq_password"] = password
output_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
try:
    output_path.chmod(0o600)
except OSError:
    pass
PY

PROFILES=()
NO_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --all) PROFILES+=(--profile mythic --profile adaptix) ;;
    --mythic) PROFILES+=(--profile mythic) ;;
    --adaptix) PROFILES+=(--profile adaptix) ;;
    --no-build) NO_BUILD=1 ;;
    *) echo "[bootstrap] Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

echo "[bootstrap] Starting C2Stack stack..."
if [ "$NO_BUILD" -eq 1 ]; then
  # "${PROFILES[@]}" is unsafe under `set -u` with an empty array on bash < 4.4.
  docker compose --env-file .env ${PROFILES[@]+"${PROFILES[@]}"} up -d
else
  docker compose --env-file .env ${PROFILES[@]+"${PROFILES[@]}"} up -d --build
fi

# The portal's persisted Lab Config volume can outlive .env. Render it after
# deployment credentials exist, then restart the consumers so their effective
# config matches the saved settings and current credentials.
portal_sync='import json,os,urllib.request; req=urllib.request.Request("http://127.0.0.1:8000/api/config/apply", data=b"", method="POST", headers={"X-API-Key":os.environ["C2STACK_API_KEY"]}); result=json.load(urllib.request.urlopen(req, timeout=5)); raise SystemExit(0 if result.get("ok") and not result.get("errors") else 1)'
config_synced=0
for _ in $(seq 1 30); do
  if docker compose --env-file .env exec -T portal python -c "$portal_sync" >/dev/null 2>&1; then
    config_synced=1
    break
  fi
  sleep 2
done
if [ "$config_synced" -ne 1 ]; then
  echo "[bootstrap] Portal could not apply persisted lab config; refusing to leave config consumers on stale settings." >&2
  exit 1
fi
docker compose --env-file .env restart redirector havoc meridian
echo "[bootstrap] Persisted lab config synchronized; redirector, Havoc, and Meridian restarted with current credentials."


echo
echo "[bootstrap] Stack status:"
docker compose --env-file .env ps

# Profile-gated probe hints, built here rather than inside the heredoc below:
# a command substitution containing a nested heredoc breaks the outer one.
EXTRA_PROBES=""
if [ "${#PROFILES[@]}" -gt 0 ]; then
  EXTRA_PROBES="  # with --mythic:
    curl -H \"\${C2_HEADER_NAME:-X-Request-ID}: \${C2_HEADER_VALUE:-cadre-c2}\" \\\\
      http://<redirector-host>:\${REDIRECTOR_HTTP_PORT:-80}\${MYTHIC_URI_PREFIX:-/cdn/media/stream}/
  # with --adaptix (needs an HTTP listener created in the Qt client first):
    curl -H \"\${C2_HEADER_NAME:-X-Request-ID}: \${C2_HEADER_VALUE:-cadre-c2}\" \\\\
      http://<redirector-host>:\${REDIRECTOR_HTTP_PORT:-80}\${ADAPTIX_URI_PREFIX:-/api/v1/sync}/"
fi

cat <<EOF

[bootstrap] Next steps for the operator:
  - C2Stack Flight Control UI    : http://localhost:${PORTAL_PORT:-8000} (or http://<redirector-host>:${PORTAL_PORT:-8000})
  - Redirector callback endpoint : http://<redirector-host>:${REDIRECTOR_HTTP_PORT:-80}
  - Mythic REST API (if enabled) : http://<redirector-host>:${MYTHIC_UI_PORT:-7443}
  - Sliver operator port         : ${SLIVER_CTRL_PORT:-31337}
  - Havoc teamserver port        : ${HAVOC_TS_PORT:-40056}
  - Adaptix teamserver port      : ${ADAPTIX_TS_PORT:-4321}  (Qt GUI client)
  - Meridian DNS Listener        : <redirector-host>:${MERIDIAN_DNS_PORT:-15353}/udp (DNS Covert Channel, zone ${MERIDIAN_DNS_DOMAIN:-c2.lab.local})
  - Meridian HTTP Callback       : http://<redirector-host>:${REDIRECTOR_HTTP_PORT:-80}${MERIDIAN_URI_PREFIX:-/gateway/v1/telemetry}

  Verify the redirector decoy page (no header -> CloudEdge CDN):
    curl http://<redirector-host>:${REDIRECTOR_HTTP_PORT:-80}/

  Verify C2 routing (with header -> backend). Meridian is always enabled, so
  probe it; the Mythic/Adaptix probes need their profiles:
    curl -H "${C2_HEADER_NAME:-X-Request-ID}: ${C2_HEADER_VALUE:-cadre-c2}" \\
      http://<redirector-host>:${REDIRECTOR_HTTP_PORT:-80}${MERIDIAN_URI_PREFIX:-/gateway/v1/telemetry}/
${EXTRA_PROBES}
EOF
