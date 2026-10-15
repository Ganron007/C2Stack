#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
trap 'status=$?; printf "bootstrap regression failed near line %s (status %s)\n" "$LINENO" "$status" >&2; exit "$status"' ERR

mkdir -p "$tmp/Docker" "$tmp/bin"
cp "$repo_root/Docker/docker-bootstrap.sh" "$tmp/Docker/docker-bootstrap.sh"
cp "$repo_root/Docker/.env.example" "$tmp/Docker/.env.example"
mkdir -p "$tmp/Docker/mythic/apollo"
cp "$repo_root/Docker/mythic/apollo/rabbitmq_config.template.json" "$tmp/Docker/mythic/apollo/rabbitmq_config.template.json"
# Exercise the cross-platform case: the PowerShell bootstrap writes CRLF.
python3 - "$tmp/Docker/.env.example" "$tmp/Docker/.env" <<'PY'
from pathlib import Path
import sys

source, target = map(Path, sys.argv[1:])
target.write_bytes(source.read_bytes().replace(b"\n", b"\r\n"))
PY

cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
if [ "$*" = 'volume inspect c2stack_mythic_postgres' ]; then
  [ "${DOCKER_VOLUME_EXISTS:-0}" = 1 ]
fi
SH
chmod +x "$tmp/bin/docker"

log="$tmp/docker.log"
if ! (
  cd "$tmp/Docker"
  export DOCKER_VOLUME_EXISTS=0 DOCKER_LOG="$log" PATH="$tmp/bin:$PATH"
  bash ./docker-bootstrap.sh --all --no-build > "$tmp/stdout" 2>&1
); then
  if [ -f "$log" ]; then cat "$log" >&2; fi
  # Bootstrap output is expected to be secret-free; still redact generated
  # secret-shaped strings before surfacing diagnostics from this test.
  sed -E 's/[0-9a-f]{64}/[redacted]/g' "$tmp/stdout" >&2
  echo 'bootstrap failed during generated-secret startup path' >&2
  exit 1
fi

grep -Fxq 'info' "$log" || { echo 'docker availability check missing' >&2; exit 1; }
grep -Fxq 'compose --env-file .env --profile mythic --profile adaptix up -d' "$log" || { echo 'all-profile startup invocation missing' >&2; exit 1; }
grep -Fq 'compose --env-file .env exec -T portal python -c' "$log" || { echo 'authenticated portal config sync missing' >&2; exit 1; }
grep -Fxq 'compose --env-file .env restart redirector havoc meridian' "$log" || { echo 'rendered-config consumer restart missing' >&2; exit 1; }
grep -Fxq 'compose --env-file .env ps' "$log" || { echo 'compose status query missing' >&2; exit 1; }
! grep -Fq -- '--build' "$log"
for name in HAVOC_OPERATOR_PASSWORD HAVOC_SERVICE_PASSWORD ADAPTIX_TEAMSERVER_PASSWORD MYTHIC_ADMIN_PASSWORD MYTHIC_DB_PASSWORD MYTHIC_RABBITMQ_PASSWORD KALI_VNC_PASSWORD C2STACK_API_KEY; do
  grep -Eq "^${name}=[0-9a-f]{64}$" "$tmp/Docker/.env" || { echo "$name was not securely generated" >&2; exit 1; }
done
rabbit_password="$(sed -n 's/^MYTHIC_RABBITMQ_PASSWORD=//p' "$tmp/Docker/.env")"
python3 - "$tmp/Docker/mythic/apollo/rabbitmq_config.generated.json" "$rabbit_password" <<'PY'
import json
import sys
from pathlib import Path

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if config["rabbitmq_password"] != sys.argv[2]:
    raise SystemExit("Apollo runtime config does not use the generated RabbitMQ password")
PY
! grep -Eq '(HAVOC_(OPERATOR|SERVICE)_PASSWORD|ADAPTIX_TEAMSERVER_PASSWORD|MYTHIC_(ADMIN|DB|RABBITMQ)_PASSWORD|KALI_VNC_PASSWORD|C2STACK_API_KEY)=[0-9a-f]{64}' "$tmp/stdout"
! grep -Fq "$rabbit_password" "$tmp/stdout"
sed -i -E '/^MYTHIC_(ADMIN|DB|RABBITMQ)_PASSWORD=/d' "$tmp/Docker/.env"
if (
  cd "$tmp/Docker"
  export DOCKER_VOLUME_EXISTS=1 DOCKER_LOG="$log" PATH="$tmp/bin:$PATH"
  bash ./docker-bootstrap.sh --mythic --no-build > "$tmp/legacy-stdout" 2>&1
); then
  echo 'bootstrap should refuse an initialized Mythic volume with missing credentials' >&2
  exit 1
fi
grep -Fq 'An initialized Mythic volume exists' "$tmp/legacy-stdout"
! grep -Eq '^MYTHIC_(ADMIN|DB|RABBITMQ)_PASSWORD=.+$' "$tmp/Docker/.env"
echo 'bootstrap argument and secret checks passed'
