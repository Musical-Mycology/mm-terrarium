"""Behavioral test for console/static/triggers.js and console.js's dispatch.

A grep over browser source is not a test of behavior: that is exactly how two
Important defects reached a live browser run during the Room-panel slice past
843 passing tests. This drives the real shipped triggers.js against a DOM stub
under Node, and its load-bearing scenario is that a trigger_fired event does
NOT rebuild the card list, which is the same defect class as the Room strip's.

No build step: node is used here only as a test runner for a plain script,
never as a shipped dependency. Skips cleanly if node is not available rather
than failing the whole suite on a box without it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_SCRIPT = ROOT / "tests" / "js" / "trigger_panel_behavior.test.js"


def _find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    fallback = Path("/opt/homebrew/bin/node")
    return str(fallback) if fallback.exists() else None


NODE = _find_node()


@pytest.mark.skipif(NODE is None, reason="node not found on this box")
def test_trigger_panel_behavior():
    result = subprocess.run(
        [NODE, str(TEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        "trigger_panel_behavior.test.js failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
