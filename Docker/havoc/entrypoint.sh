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
# Config precedence:
#   1. /render/havoc/havoc.yaotl - written by the portal's Lab Config tab.
#   2. Render the repo template from the environment (plain compose path).
TARGET="/opt/havoc/teamserver/data/havoc.yaotl"
RENDERED="/render/havoc/havoc.yaotl"

IP="${VICTIM_REDIRECTOR_IP:?VICTIM_REDIRECTOR_IP must be set (or given a default in compose)}"
HTTP_PORT="${REDIRECTOR_HTTP_PORT:-80}"
TS_PORT="${HAVOC_TS_PORT:-40056}"
URI="${HAVOC_URI_PREFIX:-/edge/cache/assets}"
HDR_NAME="${C2_HEADER_NAME:-X-Request-ID}"
HDR_VALUE="${C2_HEADER_VALUE:-cadre-c2}"

CONFIG_SOURCE="environment (repo template)"
if [ -f "$RENDERED" ]; then
  # The portal refuses to write a profile with a surviving placeholder, but a
  # stale file from an older portal could not - so verify rather than trust.
  if grep -q '__[A-Z_]*__' "$RENDERED"; then
    echo "[havoc] FATAL: $RENDERED still contains placeholders:" >&2
    grep -o '__[A-Z_]*__' "$RENDERED" | sort -u >&2
    exit 1
  fi
  cp "$RENDERED" "$TARGET"
  CONFIG_SOURCE="portal render volume"
else
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
fi

# Report what the teamserver will actually load, read back from the file
# rather than from the environment: with the portal rendering, the
# environment is stale by definition and a log line describing a different
# profile than the active one is actively misleading.
echo "[havoc] profile source: ${CONFIG_SOURCE}"
# Read back from the active profile, not the environment: with the portal
# rendering, the environment is stale by definition and a log line describing
# a different profile than the one serving traffic is actively misleading.
# Address the Listeners block explicitly - a bare grep for the first quoted
# string in the file returns the compilers in the Build block.
sed -n '/^Listeners/,/^}/p' "$TARGET" | \
  grep -oE '"[0-9a-fA-F.:]+"' | head -1 | \
  while read -r h; do echo "[havoc]   listener Hosts: ${h}"; done
sed -n '/^Listeners/,/^}/p' "$TARGET" | \
  grep -oE '"/[^"]*/"' | head -1 | \
  while read -r u; do echo "[havoc]   uri: ${u}"; done
sed -n '/^Listeners/,/^}/p' "$TARGET" | \
  grep -oE '"[A-Za-z0-9-]+: [^"]+"' | head -1 | \
  while read -r h; do echo "[havoc]   header: ${h}"; done
echo "[havoc]   ts port: $(awk -F'=' '/^ *Port =/ {gsub(/ /,"",$2); print $2}' "$TARGET" | head -1)"
echo "[havoc]   listener port: $(sed -n '/^Listeners/,/^}/p' "$TARGET" | grep -oE 'PortBind *= *[0-9]+' | head -1 | grep -oE '[0-9]+')"
exec /opt/havoc/teamserver/havoc server -d
