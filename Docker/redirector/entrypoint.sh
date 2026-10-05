#!/usr/bin/env bash
# C2Stack redirector entrypoint.
#
# Config precedence:
#   1. /render/redirector/c2stack.conf - written by the portal's Lab Config
#      tab (render_all() in lagrender.py). Single writer, so the UI is the
#      source of truth once someone uses it.
#   2. Environment rendering below - the default path, so a plain
#      `docker compose up` with no portal involvement works unchanged.
#
# The rendered file is used as-is (the portal already substituted everything
# and refused to write a config with an unsubstituted reference). We still
# verify, because a stale file from an older portal could reintroduce the
# silent-fallthrough-to-decoy failure this guard exists to prevent.
set -euo pipefail

TEMPLATE="/etc/apache2/sites-available/c2stack.conf.template"
CONF="/etc/apache2/sites-available/c2stack.conf"
RENDERED="/render/redirector/c2stack.conf"

# Per-route header gate. Sliver's HTTP implant sends no custom headers, so its
# route must match on path alone; every other route keeps the header check.
# NOTE: this MUST be set before the envsubst call below - envsubst expands
# ${SLIVER_HEADER_GATE} from the environment at that moment.
# The OFF branch MUST be an always-true condition: a previous version checked
# for a header no request ever carries, which silently made the route match
# NOTHING (every Sliver request fell through to the decoy) while looking
# correctly configured. Every REQUEST_URI starts with /, so this is true.
if [ "${SLIVER_HEADER_GATE:-on}" = "off" ]; then
  SLIVER_HEADER_GATE='%{REQUEST_URI} ^/'
else
  SLIVER_HEADER_GATE='%{HTTP:'"${C2_HEADER_NAME}"'} ^'"${C2_HEADER_VALUE}"'$ [NC]'
fi
export SLIVER_HEADER_GATE

# NOTE: every variable referenced in the template MUST be listed here. A
# missing entry is left as the literal text "${VAR}" in the rendered config -
# apache2ctl configtest still passes, so the route silently never matches and
# every request falls through to the decoy page. That is exactly how the httpx
# route (MYTHIC_HTTPX_*) went missing: it was added to the template but never
# added here.
VARS='${C2_HEADER_NAME} ${C2_HEADER_VALUE} ${MYTHIC_URI_PREFIX} ${MYTHIC_HTTPX_URI_PREFIX} ${SLIVER_URI_PREFIX} ${HAVOC_URI_PREFIX} ${ADAPTIX_URI_PREFIX} ${MERIDIAN_URI_PREFIX} ${MYTHIC_BACKEND_HOST} ${MYTHIC_BACKEND_PORT} ${MYTHIC_HTTPX_BACKEND_HOST} ${MYTHIC_HTTPX_BACKEND_PORT} ${SLIVER_BACKEND_HOST} ${SLIVER_BACKEND_PORT} ${HAVOC_BACKEND_HOST} ${HAVOC_BACKEND_PORT} ${ADAPTIX_BACKEND_HOST} ${ADAPTIX_BACKEND_PORT} ${MERIDIAN_BACKEND_HOST} ${MERIDIAN_BACKEND_PORT} ${SLIVER_HEADER_GATE}'

# APACHE_LOG_DIR is Apache's own variable, expanded at runtime, so it is
# expected to remain in the rendered config.
unsubstituted_check() {
  grep -oE '\$\{[A-Z_]+\}' "$1" | sort -u | grep -v '^\${APACHE_LOG_DIR}$' || true
}

CONFIG_SOURCE="environment"
if [ -f "${RENDERED}" ]; then
  cp "${RENDERED}" "${CONF}"
  CONFIG_SOURCE="portal render volume"
else
  envsubst "${VARS}" < "${TEMPLATE}" > "${CONF}"
fi

# Fail loudly if any C2Stack ${VAR} survived substitution: an unsubstituted
# reference means a route that can never match.
UNSUBSTITUTED="$(unsubstituted_check "${CONF}")"
if [ -n "${UNSUBSTITUTED}" ]; then
  echo "[redirector] FATAL: unsubstituted variables remain in ${CONF}:" >&2
  echo "${UNSUBSTITUTED}" >&2
  exit 1
fi

a2ensite c2stack.conf
apache2ctl configtest

# OPSEC: minimise the server fingerprint. ServerTokens is server-level only
# (illegal inside <VirtualHost>), so it lives in this conf-enabled snippet.
# Result: `Server: Apache` instead of `Apache/2.4.68 (Debian)`. Fully
# removing the header needs mod_security and is out of scope; the version +
# distro leak is what matters for shodan-style fingerprinting.
cat > /etc/apache2/conf-enabled/c2stack-security.conf <<'EOF'
ServerTokens Prod
ServerSignature Off
EOF
apache2ctl configtest

# Report the routes as they exist in the ACTIVE config, not from the
# environment. When the portal rendered this file the environment is stale by
# definition, and a log line describing a different config than the one
# serving traffic is worse than no log line at all.
# Every pipeline below ends in `|| true`: under `set -o pipefail` a grep that
# matches nothing exits 1, and without the guard that would kill the
# entrypoint AFTER a good configtest - a healthy-looking config that never
# serves traffic.
echo "[redirector] config source: ${CONFIG_SOURCE}"
echo "[redirector] active routes:"
grep -oE 'RewriteCond %\{REQUEST_URI\} \^[^ ]+' "${CONF}" \
  | sed 's/RewriteCond %{REQUEST_URI} \^/  /' \
  | sort -u | while read -r prefix; do
      echo "[redirector]   ${prefix}"
    done || true
grep -oE 'http://[a-z_]+:[0-9]+' "${CONF}" | sort -u | \
  while read -r target; do echo "[redirector]   -> ${target}"; done || true
gate_line="$(grep -oE 'RewriteCond %\{HTTP:[A-Za-z-]+\} [^ ]+ \[NC\]' "${CONF}" | head -1 || true)"
if [ -n "${gate_line}" ]; then
  echo "[redirector]   header gate: ${gate_line}"
fi

exec apache2ctl -D FOREGROUND
