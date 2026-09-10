"""./terrarium.sh: the clean-standup wrapper. Structural checks only; the
script execs run_stack, which needs o2litepy and an Arco checkout."""
import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "terrarium.sh"


def test_the_wrapper_exists_and_is_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_the_wrapper_runs_run_stack_bitless_in_serve_mode_on_8772():
    text = SCRIPT.read_text()
    assert ".venv/bin/python -m harness.run_stack" in text
    assert "--no-bit" in text
    assert "--serve" in text
    assert "--devices 0" in text
    assert "--console-port 8772" in text
    assert text.rstrip().endswith('"$@"'), \
        "user flags must come last so they override the defaults"
    assert "PYTHONPATH=" in text
