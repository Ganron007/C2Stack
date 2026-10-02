#!/usr/bin/env bash
# C2Stack sliver bootstrap: starts the daemon, mints a local operator config,
# and creates the HTTP C2 listener the redirector routes to (prefix
# /cloud/storage/objects, header X-Request-ID: cadre-c2).
#
# Every step is now ASSERTED. The previous version discarded its output and
# used `|| true`, so a rejected listener command (e.g. the bogus --rootpath
# flag, which cobra refuses on v1.7.7) left the container "healthy" with
# nothing listening on :80 - invisible from the outside.
set -euo pipefail
export PATH=/root/.sliver/go/bin:$PATH

log() { echo "[bootstrap] $*" >&2; }

sliver-server daemon --lhost 0.0.0.0 --lport 31337 &
DAEMON_PID=$!

# Forward termination to the daemon: as PID 1 this script would otherwise let
# `docker stop` SIGKILL the daemon after the 10s grace period.
cleanup() {
  log "shutting down daemon (pid $DAEMON_PID)"
  kill -TERM "$DAEMON_PID" 2>/dev/null || true
  wait "$DAEMON_PID" 2>/dev/null || true
}
trap cleanup TERM INT

# Wait for the daemon to accept connections. A bare /dev/tcp probe can succeed
# from the kernel backlog before the gRPC server is actually serving, so also
# require a real client connect below.
log "waiting for daemon on 127.0.0.1:31337"
daemon_up=0
for i in $(seq 1 60); do
  if (exec 3<>/dev/tcp/127.0.0.1/31337) 2>/dev/null; then daemon_up=1; break; fi
  sleep 1
done
if [ "$daemon_up" -ne 1 ]; then
  log "FATAL: daemon did not open 31337 within 60s"
  exit 1
fi

# Mint the operator config. `sliver-server operator` exits 0 even when it
# writes nothing (every error path is a bare `return`), so the retry loop built
# on `&& break` could never retry. Remove any stale copy, mint, then verify.
rm -f /tmp/cadre.op.cfg
log "minting operator config 'cadre'"
sliver-server operator --name cadre --lhost 127.0.0.1 -p 31337 -s /tmp/cadre.op.cfg -P all
if [ ! -s /tmp/cadre.op.cfg ]; then
  log "FATAL: operator mint produced no /tmp/cadre.op.cfg"
  exit 1
fi

printf 'cadre\n' | sliver-client import /tmp/cadre.op.cfg >/dev/null 2>&1 || true

# Create the HTTP listener. Keep the console output: it is the only place a
# rejected command surfaces ("unknown flag: ..."). On a re-run Sliver restores
# the persisted listener itself and this prints "port 80 is in use", which is
# harmless and expected.
# NOTE: /bootrc.rc holds only the two commands, no comments - Sliver's rc
# parser treats a '#' line as an unknown command ("rc line N error: unknown
# command "#""), so the rationale for these flags lives here instead.
#   http --lhost 0.0.0.0 --lport 80
# v1.7.7 has NO --rootpath flag (supported: -d/--domain, -w/--website,
# -L/--lhost, -l/--lport, -D/--disable-otp, -T, -J); cobra rejected the old
# line and the error was swallowed, so nothing listened on :80. The server
# registers a catch-all route (server/c2/http.go: router.HandleFunc("/{rpath:.*}"))
# so the redirector's /cloud/storage/objects prefix needs no root-path config.
# "port 80 is in use" on a re-run is expected: Sliver restores the persisted
# listener from its DB on boot, so this line is a no-op after the first start.
log "creating HTTP listener on :80 via /bootrc.rc"
if ! sliver-client console --rc /bootrc.rc; then
  log "WARN: console exited non-zero (see output above)"
fi

# Post-condition: prove something is actually listening on :80. Without this
# the container reports healthy while the C2 listener is missing.
listening=0
for i in $(seq 1 15); do
  if (exec 3<>/dev/tcp/127.0.0.1/80) 2>/dev/null; then listening=1; break; fi
  sleep 1
done
if [ "$listening" -ne 1 ]; then
  log "FATAL: nothing is listening on :80 - HTTP C2 is NOT configured."
  log "Check the console output above for a rejected 'http' command."
  exit 1
fi
log "HTTP C2 listener confirmed on :80"

wait "$DAEMON_PID"
