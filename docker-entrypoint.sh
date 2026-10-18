#!/bin/sh
set -e
if [ "$(id -u)" = "0" ]; then
  chown -R wisedb:wisedb /app/data
  exec runuser -u wisedb -- "$@"
fi
exec "$@"
