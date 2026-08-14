#!/bin/sh
set -e
# En network_mode:host, 127.0.0.1 es Postfix del VPS.
exec socat TCP-LISTEN:2525,fork,reuseaddr,keepalive TCP:127.0.0.1:587
