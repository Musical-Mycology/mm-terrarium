#!/usr/bin/env bash
# Clean Terrarium standup: Arco stack supervisor, Console on 8772, guest
# page on 8788, NO Bit and NO spawned Testshrooms. Without --room it boots
# to NO_ROOM and the Console loads a Room; with --room NAME that Room (and
# Arco) come up first. Any harness.run_stack flag may follow and overrides
# these defaults (argparse: the last occurrence wins), e.g.:
#   ./terrarium.sh
#   ./terrarium.sh --room TEST
#   ./terrarium.sh --room DEMO --console-port 9000
#   ./terrarium.sh --web-build /path/to/mm-tuneshroom/build/web
# Same venv + arco resolution as smoke-test.sh (a bare python3
# collects a misleading import error; see README.md).
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
exec .venv/bin/python -m harness.run_stack --no-bit --serve --devices 0 \
  --console-port 8772 "$@"
