#!/usr/bin/env bash
# Quita docker-proxy (userland-proxy) para que 8080 use iptables DNAT.
# Eso evita el cuelgue "la app responde dentro, el host no".
# Reinicia Docker; los volúmenes NO se borran.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Hay que ejecutarlo como root."
  exit 1
fi

python3 - <<'PY'
import json
from pathlib import Path
path = Path('/etc/docker/daemon.json')
data = {}
if path.exists() and path.stat().st_size:
    data = json.loads(path.read_text())
if not isinstance(data, dict):
    data = {}
data['userland-proxy'] = False
path.write_text(json.dumps(data, indent=2) + '\n')
print('Escrito', path, '->', data)
PY

echo "→ Reiniciando Docker (los contenedores vuelven con unless-stopped)"
systemctl restart docker
sleep 8
cd "$(dirname "$(readlink -f "$0")")"
docker compose up -d
sleep 8
curl -sS -o /dev/null -w 'healthz:%{http_code} t=%{time_total}\n' --max-time 8 http://127.0.0.1:8080/healthz || true
echo "Listo. userland-proxy=false"
