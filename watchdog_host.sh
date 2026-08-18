#!/usr/bin/env bash
# Comprueba 127.0.0.1:8080 (Plesk). Con la app en red del host ya no hay
# puente iptables: si falla, se reinicia SOLO gunicorn, no todo el stack.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$ROOT"

HOST_URL="${WATCHDOG_URL:-http://127.0.0.1:8080/healthz}"
FAILS_NEEDED="${WATCHDOG_FAILS:-3}"
COOLDOWN_SEC="${WATCHDOG_COOLDOWN:-600}"
GRACE_SEC="${WATCHDOG_GRACE:-90}"
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

seconds_since() {
  local f="$1"
  [[ -f "$f" ]] || { echo 99999; return; }
  local last now
  last="$(tr -cd '0-9' < "$f" || true)"
  last="${last:-0}"
  now="$(date +%s)"
  echo $((now - last))
}

exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

if host_ok; then
  echo 0 > "$STATE"
  exit 0
fi

if [[ "$(seconds_since "$COOLDOWN_FILE")" -lt "$GRACE_SEC" ]]; then
  log "host no responde, pero estamos en gracia post-reinicio (${GRACE_SEC}s); espero"
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

if [[ "$(seconds_since "$COOLDOWN_FILE")" -lt "$COOLDOWN_SEC" ]]; then
  log "en cooldown (${COOLDOWN_SEC}s); no reinicio otra vez"
  exit 0
fi

log "reinicio solo miacademia-app (sin docker compose down)"
"${COMPOSE[@]}" restart app
date +%s > "$COOLDOWN_FILE"
echo 0 > "$STATE"
log "app reiniciada; gracia ${GRACE_SEC}s"
