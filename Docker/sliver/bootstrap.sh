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

# --lhost MUST be the explicit container IPv4, never 0.0.0.0: with 0.0.0.0
# Sliver binds [::]:80 only and every IPv4 SYN gets RST (verified: the old
# 127.0.0.1 post-check passed for months while other containers were refused).
# The usable address is the one peers resolve us as: intersect `hostname -i`
# with `getent hosts sliver`. Discovered BEFORE the daemon starts because the
# persisted-listener surgery below needs it too.
sliver_ip=""
for cand in $(hostname -i); do
  if getent hosts sliver | awk '{print $1}' | grep -qx "$cand"; then sliver_ip="$cand"; break; fi
done
if [ -z "$sliver_ip" ]; then
  sliver_ip=$(hostname -i | awk '{print $1}')
  log "WARN: no hostname -i address resolves as 'sliver'; binding $sliver_ip"
fi
log "sliver backend IPv4: $sliver_ip"

# Reconcile the persisted HTTP listener BEFORE the daemon restores it. Sliver
# keeps http_listeners rows across restarts; after a subnet reshuffle the row
# still points at the OLD container IP, the restore binds nothing usable, and
# every fresh create then dies with "port 80 is in use" (the uniqueness check
# sees the stale row) while `jobs` shows nothing - a crash loop with no
# listener and no error naming the DB row. So: point the row at this boot's IP
# (website edge is already correct), or drop it if it predates the website and
# let the create path below rebuild it. Needs sqlite3 (in the image).
if [ -f /root/.sliver/sliver.db ] && command -v sqlite3 >/dev/null 2>&1; then
  stale_host=$(sqlite3 /root/.sliver/sliver.db "SELECT host FROM http_listeners WHERE port = 80;" 2>/dev/null || true)
  stale_site=$(sqlite3 /root/.sliver/sliver.db "SELECT website FROM http_listeners WHERE port = 80;" 2>/dev/null || true)
  if [ -n "$stale_host" ] && [ "$stale_site" != "edge" ]; then
    log "dropping persisted :80 listener (website '$stale_site', predates edge)"
    sqlite3 /root/.sliver/sliver.db "DELETE FROM listener_jobs WHERE id IN (SELECT listener_job_id FROM http_listeners WHERE port = 80); DELETE FROM http_listeners WHERE port = 80;" || log "WARN: listener row cleanup failed"
  elif [ -n "$stale_host" ] && [ "$stale_host" != "$sliver_ip" ]; then
    log "repointing persisted :80 listener $stale_host -> $sliver_ip"
    sqlite3 /root/.sliver/sliver.db "UPDATE http_listeners SET host = '$sliver_ip' WHERE port = 80;" || log "WARN: listener repoint failed"
  elif [ -n "$stale_host" ]; then
    log "persisted :80 listener already points at $sliver_ip"
  fi
elif [ -f /root/.sliver/sliver.db ]; then
  log "WARN: sqlite3 missing, cannot reconcile persisted listeners"
fi

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
# NOTE: the rc file holds only commands, no comments - Sliver's rc parser
# treats a '#' line as an unknown command ("rc line N error: unknown
# command "#""), so the rationale for these flags lives here instead.
# v1.7.7 has NO --rootpath flag (supported: -d/--domain, -w/--website,
# -L/--lhost, -l/--lport, -D/--disable-otp, -T, -J); cobra rejected the old
# line and the error was swallowed, so nothing listened on :80. The server
# registers a catch-all route (server/c2/http.go: router.HandleFunc("/{rpath:.*}"))
# so the redirector's /cloud/storage/objects prefix needs no root-path config.
# The daemon restores the persisted listener at boot (already reconciled to
# this boot's IP above), so prefer it: if a :80 http job is already up, keep
# it. Only kill + recreate when it is missing or wrong. Blind kill/create
# races the restore and loses ("port 80 is in use" against a job row that
# `jobs -K` cannot clear because no socket exists for it).
log "checking for a restored :80 listener job"
cat > /tmp/jobs.rc <<'RC'
jobs
exit
RC
restored=$(timeout 90 sliver-client console --rc /tmp/jobs.rc 2>/dev/null | grep -c -E 'http +tcp +80' || true)
rm -f /tmp/jobs.rc
if [ "$restored" -ge 1 ]; then
  log "restored :80 listener job present, keeping it (flags were reconciled pre-boot)"
else
  log "no restored :80 job; clearing leftovers and creating fresh"
  log "clearing any persisted HTTP listener on :80"
cat > /tmp/killjobs.rc <<'RC'
jobs -K
exit
RC
timeout 90 sliver-client console --rc /tmp/killjobs.rc 2>/dev/null || true
rm -f /tmp/killjobs.rc
# The kill is asynchronous and the daemon may still be holding :80 (restored
# job or slow teardown). Creating immediately then fails with "port 80 is in
# use" and the post-check below FATALS into a crash loop (seen 2026-10-04
# after a subnet reshuffle). Poll until the port is actually free.
log "waiting for :80 to free up"
for i in $(seq 1 30); do
  if (exec 3<>/dev/tcp/127.0.0.1/80) 2>/dev/null; then
    exec 3<&-; exec 3>&-
    sleep 1
  else
    break
  fi
done
if (exec 3<>/dev/tcp/127.0.0.1/80) 2>/dev/null; then
  exec 3<&-; exec 3>&-
  log "WARN: :80 still bound after kill; attempting create anyway"
fi

# $sliver_ip was discovered at the top (also used by the pre-boot DB
# surgery); 0.0.0.0 is never used (binds [::]-only, refuses IPv4).
log "creating HTTP listener on $sliver_ip:80 (website edge)"
cat > /tmp/boot.rc <<RC
http --lhost $sliver_ip --lport 80 --website edge
exit
RC
create_out=$(sliver-client console --rc /tmp/boot.rc 2>&1) || log "WARN: console exited non-zero (see output above)"
echo "$create_out" >&2
rm -f /tmp/boot.rc
# A stale restored job can still win the race ("port 80 is in use"): kill
# once more and retry a single time rather than crash-looping.
if echo "$create_out" | grep -qi "in use\|AlreadyExists"; then
  log "create raced a restored job; killing and retrying once"
  printf 'jobs -K\nexit\n' > /tmp/killjobs2.rc
  timeout 90 sliver-client console --rc /tmp/killjobs2.rc 2>/dev/null || true
  rm -f /tmp/killjobs2.rc
  sleep 5
  cat > /tmp/boot.rc <<RC
http --lhost $sliver_ip --lport 80 --website edge
exit
RC
  if ! sliver-client console --rc /tmp/boot.rc; then
    log "WARN: retry console exited non-zero (see output above)"
  fi
  rm -f /tmp/boot.rc
fi
fi # end: no restored job -> kill + recreate branch

# Post-condition: prove the ROUTABLE address accepts TCP, not just loopback.
# The old check dialled 127.0.0.1:80 and passed for months while every IPv4
# SYN from other containers got RST (the 0.0.0.0 -> [::]-only bind above).
listening=0
for i in $(seq 1 15); do
  if (exec 3<>/dev/tcp/$sliver_ip/80) 2>/dev/null; then listening=1; break; fi
  sleep 1
done
if [ "$listening" -ne 1 ]; then
  log "FATAL: nothing is listening on $sliver_ip:80 - HTTP C2 is NOT configured."
  log "Check the console output above for a rejected 'http' command."
  exit 1
fi
log "HTTP C2 listener confirmed on $sliver_ip:80"

wait "$DAEMON_PID"
