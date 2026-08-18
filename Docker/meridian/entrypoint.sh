#!/usr/bin/env bash
# ============================================================================
# Meridian C2 Daemon Entrypoint
# Initializes default HTTP and DNS listeners and starts background services.
# ============================================================================
set -euo pipefail

STATE_DIR="${MERIDIAN_STATE:-/root/.meridian}"
mkdir -p "${STATE_DIR}"

CONFIG_FILE="${STATE_DIR}/config.json"

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "[meridian] Initializing default listeners config in ${CONFIG_FILE}..."
    cat <<EOF > "${CONFIG_FILE}"
{
  "interval": 30,
  "jitter": 0.2,
  "store_results": "encrypted",
  "listeners": [
    {
      "name": "http-c2",
      "transport": "http",
      "host": "0.0.0.0",
      "port": 8080,
      "domain": "c2.cadre.local"
    },
    {
      "name": "dns-c2",
      "transport": "dns",
      "host": "0.0.0.0",
      "port": 5353,
      "domain": "c2.cadre.local"
    }
  ]
}
EOF
fi

echo "[meridian] Starting Meridian C2 Daemon..."
echo "[meridian] HTTP C2 listening on 0.0.0.0:8080 (backend for redirector)"
echo "[meridian] DNS C2 listening on 0.0.0.0:5353/udp (domain: c2.cadre.local)"
echo "[meridian] Precompiled implants available at /opt/meridian/payloads/"

# Start server daemon with python script to keep listeners active
python3 -c "
import time
from meridian.app import App

app = App.load()
for li in app.config.listeners:
    try:
        app.start_listener(li)
        print(f'[meridian] Started listener: {li.name} ({li.transport}://{li.host}:{li.port})')
    except Exception as e:
        print(f'[meridian] Failed to start listener {li.name}: {e}')

print('[meridian] Server running and ready for callbacks.')
try:
    while True:
        time.sleep(3600)
except (KeyboardInterrupt, SystemExit):
    app.shutdown()
"
