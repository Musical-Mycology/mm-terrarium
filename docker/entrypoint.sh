#!/usr/bin/env bash
# terrarium-dev image entrypoint: maps launcher commands onto the in-repo
# scripts, in the container's working dir (/work for a mounted checkout,
# /opt/mm/terrarium for the baked snapshot). Spec:
# docs/superpowers/specs/2026-09-29-terrarium-dev-container-design.md
set -euo pipefail

TD_LAUNCHER_PATH="${TD_LAUNCHER_PATH:-/usr/local/bin/terrarium-dev}"
TD_SELFCHECK_PATH="${TD_SELFCHECK_PATH:-/usr/local/lib/terrarium-dev/selfcheck.sh}"
STAMP=".venv/.td-req-hash"

req_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    cat requirements.txt requirements-dev.txt | sha256sum | cut -d' ' -f1
  else
    cat requirements.txt requirements-dev.txt | shasum -a 256 | cut -d' ' -f1
  fi
}

# A mounted branch may need Python deps the image lacks: install them into
# the venv volume once, keyed on the requirements files' hash.
prepare_venv() {
  [ -f "$STAMP" ] || return 0
  [ -f requirements.txt ] && [ -f requirements-dev.txt ] || return 0
  local want
  want="$(req_hash)"
  if [ "$(cat "$STAMP")" != "$want" ]; then
    echo "terrarium-dev: requirements changed; installing into the venv volume" >&2
    .venv/bin/python -m pip install -q -r requirements-dev.txt
    printf '%s\n' "$want" > "$STAMP"
  fi
}

usage() {
  echo "this image is driven by the terrarium-dev launcher; install it with:" >&2
  echo "  docker run --rm ghcr.io/musical-mycology/terrarium-dev:main launcher > terrarium-dev && chmod +x terrarium-dev" >&2
}

cmd="${1:-help}"
if [ $# -gt 0 ]; then shift; fi
case "$cmd" in
  launcher)   exec cat "$TD_LAUNCHER_PATH" ;;
  selfcheck)  exec "$TD_SELFCHECK_PATH" ;;
  stamp-venv) req_hash > "$STAMP" ;;
  run)        prepare_venv; exec ./terrarium.sh "$@" ;;
  smoke)      prepare_venv; exec ./smoke-test.sh "$@" ;;
  clean)      prepare_venv; exec ./terrarium.sh --clean ;;
  shell)      prepare_venv; exec bash "$@" ;;
  test)
    prepare_venv
    .venv/bin/python -m pytest tests -q "$@"
    exec node --test tests/js/*.test.js
    ;;
  help)       usage ;;
  *)          usage; exit 2 ;;
esac
