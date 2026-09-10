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
# Same venv + PYTHONPATH handling as smoke-test.sh (a bare python3
# collects a misleading import error; see README.md).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PYTHONPATH=/Users/chris/projects/arco
exec .venv/bin/python -m harness.run_stack --no-bit --serve --devices 0 \
  --console-port 8772 "$@"
