#!/bin/sh
# Havoc teamserver entrypoint: render the repo profile TEMPLATE with this
# lab's values, then run the teamserver.
#
# Why a template: havoc.yaotl is committed neutral (every network value is an
# __UPPER_SNAKE__ token), because the teamserver bakes listener Hosts, ports,
# URIs and headers into every Demon at BUILD time. A wrong value there fails
# SILENTLY - beacons dial a dead address and nothing reports an error. The
# previous failure mode was a read-only bind-mount of a profile holding one
# lab's real IP, which no longer tracked Docker/.env.
#
# Values come from the environment, which the portal's Lab Config tab writes
# into Docker/.env (stack scope), so one edit re-points Havoc with the rest.
set -eu

TEMPLATE="${HAVOC_PROFILE_TEMPLATE:-/templates/havoc.yaotl}"
TARGET="/opt/havoc/teamserver/data/havoc.yaotl"

IP="${VICTIM_REDIRECTOR_IP:?VICTIM_REDIRECTOR_IP must be set (or given a default in compose)}"
HTTP_PORT="${REDIRECTOR_HTTP_PORT:-80}"
TS_PORT="${HAVOC_TS_PORT:-40056}"
URI="${HAVOC_URI_PREFIX:-/edge/cache/assets}"
HDR_NAME="${C2_HEADER_NAME:-X-Request-ID}"
HDR_VALUE="${C2_HEADER_VALUE:-cadre-c2}"

if [ ! -f "$TEMPLATE" ]; then
  echo "[havoc] FATAL: profile template missing: $TEMPLATE" >&2
  exit 1
fi

# Plain-token substitution (no envsubst in this image). The | delimiters keep
# a value containing / from being read as a sed address.
sed \
  -e "s|__VICTIM_REDIRECTOR_IP__|${IP}|g" \
  -e "s|__REDIRECTOR_HTTP_PORT__|${HTTP_PORT}|g" \
  -e "s|__HAVOC_TS_PORT__|${TS_PORT}|g" \
  -e "s|__HAVOC_URI_PREFIX__|${URI}|g" \
  -e "s|__C2_HEADER_NAME__|${HDR_NAME}|g" \
  -e "s|__C2_HEADER_VALUE__|${HDR_VALUE}|g" \
  "$TEMPLATE" > "$TARGET"

# An unsubstituted token would make the teamserver bind the wrong port or
# accept beacons on a wrong path - fatal, never best-effort.
if grep -q '__[A-Z_]*__' "$TARGET"; then
  echo "[havoc] FATAL: unrendered token(s) left in $TARGET:" >&2
  grep -o '__[A-Z_]*__' "$TARGET" | sort -u >&2
  exit 1
fi

echo "[havoc] profile rendered: Hosts=$IP port=$HTTP_PORT uri=${URI}/ header='${HDR_NAME}: ${HDR_VALUE}' ts=$TS_PORT"
exec /opt/havoc/teamserver/havoc server -d
