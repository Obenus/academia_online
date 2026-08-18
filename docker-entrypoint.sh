#!/bin/sh
set -e
# En el VPS de Plesk la app usa la red del host (puerto 8080 directo).
# Postgres sigue en Docker publicado en 127.0.0.1:5433.
if [ "${ACADEMIA_HOST_NETWORK:-}" = "1" ]; then
  export PORT="${PORT:-8080}"
  if [ -n "${DATABASE_URL:-}" ]; then
    DATABASE_URL=$(printf '%s' "$DATABASE_URL" | sed -E 's/@db(:5432)?/@127.0.0.1:5433/')
    export DATABASE_URL
  fi
  echo "[entrypoint] red del host: PORT=${PORT} DB vía 127.0.0.1:5433"
fi
exec "$@"
