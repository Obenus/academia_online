#!/bin/sh
set -e
# Escucha en la red Docker y habla con Postfix del host (127.0.0.1:587).
exec socat TCP-LISTEN:2525,fork,reuseaddr,keepalive \
  EXEC:"nsenter -t 1 -n socat STDIO TCP:127.0.0.1:587"
