#!/usr/bin/env bash
# C2Stack Docker practice-lab bootstrap (Linux/macOS host).
# Copies .env.example -> .env if missing, checks Docker, then builds + starts
# the stack. Pass --mythic to also bring up the Mythic profile.
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

ARGS=(compose --env-file .env up -d --build)
if [ "${1:-}" = "--mythic" ]; then ARGS+=(--profile mythic); fi

echo "[bootstrap] Starting C2Stack stack..."
docker "${ARGS[@]}"

echo
echo "[bootstrap] Stack status:"
docker compose --env-file .env ps

cat <<EOF

[bootstrap] Next steps for the operator (Kali VM):
  - Redirector callback endpoint : http://<host-ip-on-vmnet2>:${REDIRECTOR_HTTP_PORT:-80}
  - Mythic UI (if enabled)       : https://<host-ip-on-vmnet2>:${MYTHIC_UI_PORT:-7443}
  - Sliver operator port         : ${SLIVER_CTRL_PORT:-31337}
  - Havoc teamserver port        : ${HAVOC_TS_PORT:-40056}

  Verify the redirector decoy page (no header -> CloudEdge CDN):
    curl http://<host-ip-on-vmnet2>:${REDIRECTOR_HTTP_PORT:-80}/

  Verify C2 routing (with header -> backend):
    curl -H "${C2_HEADER_NAME:-X-Request-ID}: ${C2_HEADER_VALUE:-cadre-c2}" \\
      http://<host-ip-on-vmnet2>:${REDIRECTOR_HTTP_PORT:-80}${MYTHIC_URI_PREFIX:-/cdn/media/stream}/
EOF
