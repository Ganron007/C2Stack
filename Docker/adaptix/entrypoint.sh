#!/usr/bin/env bash
set -euo pipefail

cd /app
PASSWORD="${ADAPTIX_TEAMSERVER_PASSWORD:?ADAPTIX_TEAMSERVER_PASSWORD must be set; run docker-bootstrap.sh}"
if [[ ! "$PASSWORD" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "[adaptix] FATAL: password must use letters, digits, dot, underscore, or hyphen" >&2
  exit 1
fi

if [[ ! -s /app/server.rsa.crt || ! -s /app/server.rsa.key ]]; then
  echo "[adaptix] Generating deployment-local teamserver certificate..."
  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout /app/server.rsa.key -out /app/server.rsa.crt -days 825 \
    -subj "/CN=adaptix" \
    -addext "subjectAltName=DNS:adaptix,DNS:localhost,IP:127.0.0.1"
fi
mkdir -p /trust
install -m 0644 /app/server.rsa.crt /trust/adaptix.crt
sed "s|__ADAPTIX_TEAMSERVER_PASSWORD__|$PASSWORD|g" \
  /app/profile.yaml > /app/profile.runtime.yaml
if grep -q '__ADAPTIX_TEAMSERVER_PASSWORD__' /app/profile.runtime.yaml; then
  echo "[adaptix] FATAL: profile password placeholder was not rendered" >&2
  exit 1
fi

echo "[adaptix] Starting Adaptix C2 Server..."
echo "[adaptix] Teamserver listening on :4321 (operators), HTTP C2 via redirector on :80"
exec /app/adaptixserver -profile /app/profile.runtime.yaml
