#!/usr/bin/env bash
# Instala un timer systemd que detecta si el puerto 8080 del host se queda
# colgado (docker-proxy) aunque la app dentro de Docker siga viva.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
UNIT_DIR=/etc/systemd/system

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Hay que ejecutarlo como root."
  exit 1
fi
if [[ ! -x "${ROOT}/watchdog_host.sh" ]]; then
  echo "No encuentro ${ROOT}/watchdog_host.sh"
  exit 1
fi

chmod +x "${ROOT}/watchdog_host.sh"
mkdir -p "${ROOT}/backups"

sed "s|/opt/academia_online|${ROOT}|g" \
  "${ROOT}/deploy/academia-watchdog.service" > "${UNIT_DIR}/academia-watchdog.service"
cp "${ROOT}/deploy/academia-watchdog.timer" "${UNIT_DIR}/academia-watchdog.timer"

systemctl daemon-reload
systemctl enable --now academia-watchdog.timer
systemctl list-timers academia-watchdog.timer --no-pager
echo
echo "Watchdog activo. Log: ${ROOT}/backups/watchdog.log"
echo "Desactivar: systemctl disable --now academia-watchdog.timer"
