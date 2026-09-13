#!/usr/bin/env bash
# Thin wrapper around harness.run_stack: resolves the arco checkout pyarco/o2litepy
# need and always uses .venv/bin/python (a bare `python3` collects a
# misleading import error -- see README.md). All arguments are forwarded
# to run_stack.py verbatim, e.g.:
#   ./smoke-test.sh --open --devices 2
#   ./smoke-test.sh --profile profiles/dev-metronome.toml
#   ./smoke-test.sh --ci --seconds 10 --devices 1
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# pyarco/o2litepy come from an arco checkout. Resolve it, in order: an
# explicit ARCO_ROOT; an already-set PYTHONPATH (left as is); else a
# sibling `arco` checkout next to the main clone (a git worktree resolves
# to the same place). Refuse to start otherwise, rather than let pyarco's
# import error masquerade as something else.
if [ -n "${ARCO_ROOT:-}" ]; then
  _arco="$ARCO_ROOT"
elif [ -n "${PYTHONPATH:-}" ]; then
  _arco=""
else
  _arco="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/../arco"
fi
if [ -n "$_arco" ]; then
  if [ ! -d "$_arco/pyarco" ]; then
    echo "$(basename "$0"): no arco checkout at $_arco (need $_arco/pyarco);" \
         "set ARCO_ROOT=/path/to/arco or PYTHONPATH" >&2
    exit 1
  fi
  export PYTHONPATH="$_arco${PYTHONPATH:+:$PYTHONPATH}"
fi
exec .venv/bin/python -m harness.run_stack "$@"
