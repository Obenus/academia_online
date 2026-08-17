#!/usr/bin/env bash
# Comprueba el puerto publicado en el HOST (8080), no el de dentro del contenedor.
# El healthcheck de Docker no detecta un docker-proxy colgado.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$ROOT"

HOST_URL="${WATCHDOG_URL:-http://127.0.0.1:8080/healthz}"
FAILS_NEEDED="${WATCHDOG_FAILS:-3}"
STATE_DIR="${WATCHDOG_STATE_DIR:-/var/tmp}"
STATE="${STATE_DIR}/academia-watchdog.fails"
LOG="${ROOT}/backups/watchdog.log"
LOCK="${STATE_DIR}/academia-watchdog.lock"
COMPOSE=(docker compose)

mkdir -p backups "$(dirname "$STATE")"

log() {
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" | tee -a "$LOG"
}

host_ok() {
  curl -fsS -o /dev/null --max-time 5 "$HOST_URL"
}

inner_ok() {
  docker exec miacademia-app python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=3)" \
    >/dev/null 2>&1
}

exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

if host_ok; then
  echo 0 > "$STATE"
  exit 0
fi

fails=0
if [[ -f "$STATE" ]]; then
  fails="$(tr -cd '0-9' < "$STATE" || true)"
  fails="${fails:-0}"
fi
fails=$((fails + 1))
echo "$fails" > "$STATE"
log "host ${HOST_URL} no responde (fallo ${fails}/${FAILS_NEEDED})"

if [[ "$fails" -lt "$FAILS_NEEDED" ]]; then
  exit 0
fi

if inner_ok; then
  log "la app responde DENTRO del contenedor: recreo app para renovar docker-proxy"
else
  log "ni el host ni el contenedor responden: recreo app"
fi

"${COMPOSE[@]}" up -d --force-recreate --no-deps app
echo 0 > "$STATE"
log "recreate app hecho; el siguiente ciclo volverá a comprobar 8080"
