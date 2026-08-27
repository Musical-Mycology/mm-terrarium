"""Pytest wrapper running every tests/js/*.test.js under node.

The Console front end is six ES modules under console/static/, each covered
by a node test file in tests/js/. Those files only run when a node process
executes them, and nothing in the offline suite did that after the ES-module
rewrite retired tests/test_room_panel_behavior.py -- so pytest and CI were
green while the front-end tests never ran. This wrapper restores the old
pattern: one parametrized test globbing tests/js/*.test.js, so a newly added
test file is picked up with no wrapper change.

No build step: node is used here only as a test runner for plain scripts,
never as a shipped dependency. Skips cleanly if node is not available
rather than failing the whole suite on a box without it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "tests" / "js"
JS_TESTS = sorted(JS_DIR.glob("*.test.js"))


def _find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    # Node isn't always on PATH in every environment this suite runs in,
    # but the box this test was written on has it here.
    fallback = Path("/opt/homebrew/bin/node")
    return str(fallback) if fallback.exists() else None


NODE = _find_node()


def test_js_test_files_exist():
    assert JS_TESTS, f"no *.test.js files found under {JS_DIR}"


@pytest.mark.skipif(NODE is None, reason="node not found on this box")
@pytest.mark.parametrize("script", JS_TESTS, ids=lambda p: p.name)
def test_console_js(script: Path):
    result = subprocess.run(
        [NODE, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{script.name} failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
