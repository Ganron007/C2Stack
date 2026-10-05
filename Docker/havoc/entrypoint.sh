#!/bin/sh
# Havoc teamserver entrypoint: render the repo profile template with this
# lab's values, then run the teamserver.
#
# Why: havoc.yaotl is neutralized for public release (Hosts holds the
# __VICTIM_REDIRECTOR_IP__ token, not a real address), and the teamserver
# bakes listener Hosts into every Demon at BUILD time - a wrong value here
# fails silently (beacons dial a dead address, no error anywhere). Rendering
# at container start keeps the repo neutral while the running lab stays
# correct. A leftover token is fatal, not best-effort.
set -eu

TEMPLATE="${HAVOC_PROFILE_TEMPLATE:-/templates/havoc.yaotl}"
TARGET="/opt/havoc/teamserver/data/havoc.yaotl"
IP="${VICTIM_REDIRECTOR_IP:-192.168.100.1}"

if [ ! -f "$TEMPLATE" ]; then
  echo "[havoc] FATAL: profile template missing: $TEMPLATE" >&2
  exit 1
fi
# Plain-token substitution (no envsubst dependency in this image).
sed "s/__VICTIM_REDIRECTOR_IP__/${IP}/g" "$TEMPLATE" > "$TARGET"
if grep -q "__VICTIM_REDIRECTOR_IP__" "$TARGET"; then
  echo "[havoc] FATAL: unrendered token left in $TARGET" >&2
  exit 1
fi
echo "[havoc] profile rendered with Hosts=$IP"
exec /opt/havoc/teamserver/havoc server -d
