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

if ! docker info >/dev/null 2>&1; then
  echo "Docker does not appear to be running. Start Docker and retry." >&2
  exit 1
fi
echo "[bootstrap] Docker is available."

PROFILES=()
NO_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --mythic|--all) PROFILES+=(--profile mythic) ;;
    --adaptix|--all) PROFILES+=(--profile adaptix) ;;
    --no-build) NO_BUILD=1 ;;
    *) echo "[bootstrap] Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Source .env so the "next steps" block below reflects the operator's actual
# configuration instead of collapsing every ${VAR:-default} to the default.
# set -a exports the variables for the subsequent expansions.
set -a
# shellcheck disable=SC1091
. ./.env
set +a

echo "[bootstrap] Starting C2Stack stack..."
if [ "$NO_BUILD" -eq 1 ]; then
  # "${PROFILES[@]}" is unsafe under `set -u` with an empty array on bash < 4.4.
  docker compose --env-file .env ${PROFILES[@]+"${PROFILES[@]}"} up -d
else
  docker compose --env-file .env ${PROFILES[@]+"${PROFILES[@]}"} up -d --build
fi


echo
echo "[bootstrap] Stack status:"
docker compose --env-file .env ps

# Profile-gated probe hints, built here rather than inside the heredoc below:
# a command substitution containing a nested heredoc breaks the outer one.
EXTRA_PROBES=""
if [ "${#PROFILES[@]}" -gt 0 ]; then
  EXTRA_PROBES="  # with --mythic:
    curl -H \"\${C2_HEADER_NAME:-X-Request-ID}: \${C2_HEADER_VALUE:-cadre-c2}\" \\\\
      http://<host-ip-on-vmnet2>:\${REDIRECTOR_HTTP_PORT:-80}\${MYTHIC_URI_PREFIX:-/cdn/media/stream}/
  # with --adaptix (needs an HTTP listener created in the Qt client first):
    curl -H \"\${C2_HEADER_NAME:-X-Request-ID}: \${C2_HEADER_VALUE:-cadre-c2}\" \\\\
      http://<host-ip-on-vmnet2>:\${REDIRECTOR_HTTP_PORT:-80}\${ADAPTIX_URI_PREFIX:-/api/v1/sync}/"
fi

cat <<EOF

[bootstrap] Next steps for the operator:
  - C2Stack Flight Control UI    : http://localhost:${PORTAL_PORT:-8000} (or http://<host-ip-on-vmnet2>:${PORTAL_PORT:-8000})
  - Redirector callback endpoint : http://<host-ip-on-vmnet2>:${REDIRECTOR_HTTP_PORT:-80}
  - Mythic UI (if enabled)       : https://<host-ip-on-vmnet2>:${MYTHIC_UI_PORT:-7443}
  - Sliver operator port         : ${SLIVER_CTRL_PORT:-31337}
  - Havoc teamserver port        : ${HAVOC_TS_PORT:-40056}
  - Adaptix teamserver port      : ${ADAPTIX_TS_PORT:-4321}  (Qt GUI client)
  - Meridian DNS Listener        : <host-ip-on-vmnet2>:${MERIDIAN_DNS_PORT:-15353}/udp (DNS Covert Channel, zone ${MERIDIAN_DNS_DOMAIN:-c2.cadre.local})
  - Meridian HTTP Callback       : http://<host-ip-on-vmnet2>:${REDIRECTOR_HTTP_PORT:-80}${MERIDIAN_URI_PREFIX:-/gateway/v1/telemetry}

  Verify the redirector decoy page (no header -> CloudEdge CDN):
    curl http://<host-ip-on-vmnet2>:${REDIRECTOR_HTTP_PORT:-80}/

  Verify C2 routing (with header -> backend). Meridian is always enabled, so
  probe it; the Mythic/Adaptix probes need their profiles:
    curl -H "${C2_HEADER_NAME:-X-Request-ID}: ${C2_HEADER_VALUE:-cadre-c2}" \\
      http://<host-ip-on-vmnet2>:${REDIRECTOR_HTTP_PORT:-80}${MERIDIAN_URI_PREFIX:-/gateway/v1/telemetry}/
${EXTRA_PROBES}
EOF
