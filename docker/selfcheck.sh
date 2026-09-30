#!/usr/bin/env bash
# Hermetic image check (spec section 7): in the container's own network
# namespace there is no host mDNS daemon to contend with, so start a private
# dbus + avahi-daemon, then run both test suites and a --ci smoke run on the
# baked snapshot. Must run as root: docker run --rm IMAGE selfcheck
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "selfcheck: must run as root (docker run --rm IMAGE selfcheck)" >&2; exit 1; }

mkdir -p /run/dbus
dbus-uuidgen --ensure
dbus-daemon --system --fork
avahi-daemon --daemonize --no-chroot
for _ in $(seq 50); do
  [ -e /run/avahi-daemon/socket ] && break
  sleep 0.1
done
[ -e /run/avahi-daemon/socket ] || { echo "selfcheck: avahi-daemon did not start" >&2; exit 1; }

cd /opt/mm/terrarium
echo "== pytest"
.venv/bin/python -m pytest tests -q
echo "== node --test"
node --test tests/js/*.test.js
echo "== smoke-test --ci"
./smoke-test.sh --ci
echo "SELFCHECK_OK"
