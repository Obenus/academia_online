#!/bin/sh
set -e
# Túnel TCP: Docker → :2525 → submission del host :587 (AUTH/TLS se conservan).
# range evita que Internet use el puerto como relé abierto.
exec socat TCP-LISTEN:2525,fork,reuseaddr,keepalive,range=172.16.0.0/12 TCP:127.0.0.1:587
