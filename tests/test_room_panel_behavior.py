"""Behavioral test for console/static/room.js.

tests/test_console_static.py greps room.js's source for substrings, which
is exactly why Defect 1 (the Room strip was rebuilt, and its painted
swatches destroyed, on every room_changed event) reached a live browser run
undetected: a substring grep cannot see what the DOM looks like after the
code runs. This test drives the real shipped room.js against a small
hand-rolled DOM stub under Node and checks that the strip's DOM node
survives an unchanged-capability re-render with its painted backgrounds
intact, that a capability change rebuilds it, that renderRoom(null) resets
cleanly, and that renderRoomFrame decodes the wire's GRB channel order
correctly. See tests/js/room_panel_behavior.test.js for the scenarios.

No build step: node is used here only as a test runner for a plain script,
never as a shipped dependency. Skips cleanly if node is not available
rather than failing the whole suite on a box without it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_SCRIPT = ROOT / "tests" / "js" / "room_panel_behavior.test.js"


def _find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    # Node isn't always on PATH in every environment this suite runs in,
    # but the box this test was written on has it here.
    fallback = Path("/opt/homebrew/bin/node")
    return str(fallback) if fallback.exists() else None


NODE = _find_node()


@pytest.mark.skipif(NODE is None, reason="node not found on this box")
def test_room_panel_behavior():
    result = subprocess.run(
        [NODE, str(TEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        "room_panel_behavior.test.js failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
