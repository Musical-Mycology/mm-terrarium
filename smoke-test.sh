#!/usr/bin/env bash
# Thin wrapper around harness.run_stack: sets the PYTHONPATH pyarco/o2litepy
# need and always uses .venv/bin/python (a bare `python3` collects a
# misleading import error -- see README.md). All arguments are forwarded
# to run_stack.py verbatim, e.g.:
#   ./smoke-test.sh --open --devices 2
#   ./smoke-test.sh --profile profiles/dev-metronome.toml
#   ./smoke-test.sh --ci --seconds 10 --devices 1
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PYTHONPATH=/Users/chris/projects/arco
exec .venv/bin/python -m harness.run_stack "$@"
