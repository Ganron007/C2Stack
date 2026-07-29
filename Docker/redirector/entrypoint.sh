#!/usr/bin/env bash
# C2Stack redirector entrypoint.
# Renders the Apache vhost from environment variables, then runs Apache in
# the foreground. Only the C2Stack variables are substituted (envsubst with an
# explicit list) so Apache's own ${APACHE_LOG_DIR} is left untouched.
set -euo pipefail

TEMPLATE="/etc/apache2/sites-available/c2stack.conf.template"
CONF="/etc/apache2/sites-available/c2stack.conf"

VARS='${C2_HEADER_NAME} ${C2_HEADER_VALUE} ${MYTHIC_URI_PREFIX} ${SLIVER_URI_PREFIX} ${HAVOC_URI_PREFIX} ${ADAPTIX_URI_PREFIX} ${MYTHIC_BACKEND_HOST} ${MYTHIC_BACKEND_PORT} ${SLIVER_BACKEND_HOST} ${SLIVER_BACKEND_PORT} ${HAVOC_BACKEND_HOST} ${HAVOC_BACKEND_PORT} ${ADAPTIX_BACKEND_HOST} ${ADAPTIX_BACKEND_PORT}'

envsubst "${VARS}" < "${TEMPLATE}" > "${CONF}"

a2ensite c2stack.conf
apache2ctl configtest

echo "[redirector] C2 header: ${C2_HEADER_NAME}: ${C2_HEADER_VALUE}"
echo "[redirector] routes: mythic=${MYTHIC_URI_PREFIX} -> ${MYTHIC_BACKEND_HOST}:${MYTHIC_BACKEND_PORT}, sliver=${SLIVER_URI_PREFIX} -> ${SLIVER_BACKEND_HOST}:${SLIVER_BACKEND_PORT}, havoc=${HAVOC_URI_PREFIX} -> ${HAVOC_BACKEND_HOST}:${HAVOC_BACKEND_PORT}, adaptix=${ADAPTIX_URI_PREFIX} -> ${ADAPTIX_BACKEND_HOST}:${ADAPTIX_BACKEND_PORT}"

exec apache2ctl -D FOREGROUND
