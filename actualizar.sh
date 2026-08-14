#!/usr/bin/env bash
# Actualiza la academia a la última versión de GitHub (rama main)
# sin borrar volúmenes ni contenidos.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

echo "=== Academia: actualizar a última versión ==="
echo "Directorio: $(pwd)"

# Solo aborta si hay cambios en archivos YA versionados (no por .sql o logo locales)
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Hay cambios locales en archivos del proyecto. Revisa con: git status"
  echo "No se continúa para no pisar nada."
  exit 1
fi

mkdir -p backups
STAMP="$(date +%Y%m%d_%H%M%S)"
DUMP="backups/pre_update_${STAMP}.sql"
echo "→ Copia de seguridad de la base de datos: ${DUMP}"
docker compose exec -T db pg_dump -U postgres miacademia > "${DUMP}"

echo "→ git pull origin main"
git fetch --tags origin
git pull origin main

echo "→ Permitiendo SMTP desde redes Docker (si el firewall lo bloquea)"
if command -v iptables >/dev/null 2>&1; then
  iptables -C INPUT -s 172.16.0.0/12 -p tcp --dport 587 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT -s 172.16.0.0/12 -p tcp --dport 587 -j ACCEPT \
    || true
  iptables -C INPUT -s 172.16.0.0/12 -p tcp --dport 465 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT -s 172.16.0.0/12 -p tcp --dport 465 -j ACCEPT \
    || true
fi

echo "→ Parando workers (billing, reminder, backup)"
docker compose stop billing reminder backup

echo "→ Rebuild y arranque de la app y relé SMTP"
docker compose up -d --build app smtp-relay

echo "→ Esperando a que /login responda..."
OK=0
for i in $(seq 1 20); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/login || true)"
  if [[ "${CODE}" == "200" ]]; then
    echo "   /login → 200"
    OK=1
    break
  fi
  echo "   intento ${i}/20 → ${CODE:-sin respuesta}"
  sleep 3
done

if [[ "${OK}" != "1" ]]; then
  echo "La app no respondió 200. Los workers no se arrancan."
  echo "Revisa: docker logs miacademia-app --tail 80"
  exit 1
fi

echo "→ Rebuild y arranque de workers"
docker compose up -d --build billing reminder backup

echo
echo "Listo. Versión: $(git describe --tags --always)"
echo "Copia de seguridad: ${DUMP}"
echo "No se ha usado docker compose down -v (los datos se conservan)."
