#!/bin/sh
set -e
# Docker → :2525 → Postfix :25 en localhost (mynetworks, sin SASL).
# range evita que Internet use el relé como open relay.
exec socat TCP-LISTEN:2525,fork,reuseaddr,keepalive,range=172.16.0.0/12 TCP:127.0.0.1:25
