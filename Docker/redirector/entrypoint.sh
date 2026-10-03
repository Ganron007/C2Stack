#!/usr/bin/env bash
# C2Stack redirector entrypoint.
# Renders the Apache vhost from environment variables, then runs Apache in
# the foreground. Only the C2Stack variables are substituted (envsubst with an
# explicit list) so Apache's own ${APACHE_LOG_DIR} is left untouched.
set -euo pipefail

TEMPLATE="/etc/apache2/sites-available/c2stack.conf.template"
CONF="/etc/apache2/sites-available/c2stack.conf"

# NOTE: every variable referenced in the template MUST be listed here. A
# missing entry is left as the literal text "${VAR}" in the rendered config -
# apache2ctl configtest still passes, so the route silently never matches and
# every request falls through to the decoy page. That is exactly how the httpx
# route (MYTHIC_HTTPX_*) went missing: it was added to the template but never
# added here.
# Per-route header gate. Sliver's implant never sends C2_HEADER_NAME, so its
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

VARS='${C2_HEADER_NAME} ${C2_HEADER_VALUE} ${MYTHIC_URI_PREFIX} ${MYTHIC_HTTPX_URI_PREFIX} ${SLIVER_URI_PREFIX} ${HAVOC_URI_PREFIX} ${ADAPTIX_URI_PREFIX} ${MERIDIAN_URI_PREFIX} ${MYTHIC_BACKEND_HOST} ${MYTHIC_BACKEND_PORT} ${MYTHIC_HTTPX_BACKEND_HOST} ${MYTHIC_HTTPX_BACKEND_PORT} ${SLIVER_BACKEND_HOST} ${SLIVER_BACKEND_PORT} ${HAVOC_BACKEND_HOST} ${HAVOC_BACKEND_PORT} ${ADAPTIX_BACKEND_HOST} ${ADAPTIX_BACKEND_PORT} ${MERIDIAN_BACKEND_HOST} ${MERIDIAN_BACKEND_PORT} ${SLIVER_HEADER_GATE}'

envsubst "${VARS}" < "${TEMPLATE}" > "${CONF}"

# Fail loudly if any C2Stack ${VAR} survived substitution: an unsubstituted
# reference means a route that can never match. APACHE_LOG_DIR is deliberately
# excluded - Apache itself expands it at runtime, so it is expected to remain.
UNSUBSTITUTED="$(grep -oE '\$\{[A-Z_]+\}' "${CONF}" | sort -u | grep -v '^\${APACHE_LOG_DIR}$' || true)"
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

echo "[redirector] C2 header: ${C2_HEADER_NAME}: ${C2_HEADER_VALUE}"
echo "[redirector] routes: mythic=${MYTHIC_URI_PREFIX} -> ${MYTHIC_BACKEND_HOST}:${MYTHIC_BACKEND_PORT}, mythic-httpx=${MYTHIC_HTTPX_URI_PREFIX} -> ${MYTHIC_HTTPX_BACKEND_HOST}:${MYTHIC_HTTPX_BACKEND_PORT}, sliver=${SLIVER_URI_PREFIX} -> ${SLIVER_BACKEND_HOST}:${SLIVER_BACKEND_PORT}, havoc=${HAVOC_URI_PREFIX} -> ${HAVOC_BACKEND_HOST}:${HAVOC_BACKEND_PORT}, adaptix=${ADAPTIX_URI_PREFIX} -> ${ADAPTIX_BACKEND_HOST}:${ADAPTIX_BACKEND_PORT}, meridian=${MERIDIAN_URI_PREFIX} -> ${MERIDIAN_BACKEND_HOST}:${MERIDIAN_BACKEND_PORT}"

exec apache2ctl -D FOREGROUND
