#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

PYTHONPATH="$repo_root/Docker/portal" python - "$repo_root" "$tmp/c2stack.conf" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(root / "Docker" / "portal"))
import lagrender

template = (root / "Docker" / "redirector" / "apache" / "c2stack.conf.template")
output.write_text(lagrender.render_redirector(template.read_text(encoding="utf-8")), encoding="utf-8")
PY

sudo apt-get update -qq
sudo apt-get install -y -qq apache2
sudo a2enmod rewrite proxy proxy_http headers >/dev/null
sudo a2dissite 000-default >/dev/null || true
sudo install -m 0644 "$tmp/c2stack.conf" /etc/apache2/sites-available/c2stack.conf
sudo a2ensite c2stack >/dev/null
sudo apache2ctl configtest
