#!/usr/bin/env bash
# Comprueba el puerto 8080 del HOST. Si la app vive dentro del contenedor y el
# host no responde, el puente Docker/iptables está roto: recrear SOLO la app
# no sirve (bucle de 502). Hay que tumbar y levantar el stack SIN -v.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$ROOT"

HOST_URL="${WATCHDOG_URL:-http://127.0.0.1:8080/healthz}"
FAILS_NEEDED="${WATCHDOG_FAILS:-3}"
COOLDOWN_SEC="${WATCHDOG_COOLDOWN:-900}"
STATE_DIR="${WATCHDOG_STATE_DIR:-/var/tmp}"
STATE="${STATE_DIR}/academia-watchdog.fails"
COOLDOWN_FILE="${STATE_DIR}/academia-watchdog.cooldown"
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

in_cooldown() {
  [[ -f "$COOLDOWN_FILE" ]] || return 1
  local last now
  last="$(tr -cd '0-9' < "$COOLDOWN_FILE" || true)"
  last="${last:-0}"
  now="$(date +%s)"
  [[ $((now - last)) -lt "$COOLDOWN_SEC" ]]
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

if in_cooldown; then
  log "en cooldown (${COOLDOWN_SEC}s tras el último repair); no recreo el stack otra vez"
  exit 0
fi

if inner_ok; then
  log "la app responde DENTRO: el puente 8080/iptables está colgado. Recreo la red (down+up, sin -v)"
else
  log "ni el host ni el contenedor responden. Recreo el stack (down+up, sin -v)"
fi

# Plesk a veces deja FORWARD en DROP y Docker deja de enrutar al contenedor.
if command -v iptables >/dev/null 2>&1; then
  iptables -P FORWARD ACCEPT 2>/dev/null || true
fi

"${COMPOSE[@]}" down
"${COMPOSE[@]}" up -d
date +%s > "$COOLDOWN_FILE"
echo 0 > "$STATE"
log "stack recreado; cooldown ${COOLDOWN_SEC}s"
