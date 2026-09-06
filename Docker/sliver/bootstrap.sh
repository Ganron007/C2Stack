#!/usr/bin/env bash
# C2Stack sliver bootstrap: starts the daemon, mints a local operator config,
# and creates the HTTP C2 listener the redirector routes to (/cloud/storage/objects,
# header X-Request-ID: cadre-c2). Idempotent: on restart the operator already
# exists (error is ignored) and the console is only used to ensure the listener
# record is present.
set -e
export PATH=/root/.sliver/go/bin:$PATH

sliver-server daemon --lhost 0.0.0.0 --lport 31337 &
DAEMON_PID=$!

for i in $(seq 1 60); do
  (exec 3<>/dev/tcp/127.0.0.1/31337) 2>/dev/null && break
  sleep 1
done

for i in $(seq 1 20); do
  sliver-server operator --name cadre --lhost 127.0.0.1 -p 31337 -s /tmp/cadre.op.cfg -P all && break
  sleep 1
done

printf 'cadre\n' | sliver-client import /tmp/cadre.op.cfg >/dev/null 2>&1 || true
sliver-client console --rc /bootrc.rc >/dev/null 2>&1 || true

wait $DAEMON_PID
