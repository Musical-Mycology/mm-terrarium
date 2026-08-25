"""The Console's scripts, tested as the browser actually loads them.

console/static/*.js load as plain scripts into one shared global scope.
Nothing tested that combination before: room_panel_behavior.test.js loads
room.js with console.js, trigger_panel_behavior.test.js loads triggers.js,
and no test loaded room.js and triggers.js together -- which is the only
pair that collided. See
docs/superpowers/specs/2026-08-19-wire-json-and-console-script-isolation-design.md.

No build step: node is used here only as a test runner for plain scripts,
never as a shipped dependency. Skips cleanly if node is unavailable.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    fallback = Path("/opt/homebrew/bin/node")
    return str(fallback) if fallback.exists() else None


NODE = _find_node()


JS_TESTS = sorted(p.name for p in (ROOT / "tests" / "js").glob("*.test.js"))


@pytest.mark.skipif(NODE is None, reason="node not found on this box")
@pytest.mark.parametrize("script", JS_TESTS)
def test_console_scripts(script):
    result = subprocess.run(
        [NODE, str(ROOT / "tests" / "js" / script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"{script} failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
