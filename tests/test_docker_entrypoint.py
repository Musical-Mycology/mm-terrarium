"""docker/entrypoint.sh: command mapping and requirements-drift handling.
Runs the script on the host against a stub checkout (stub terrarium.sh,
smoke-test.sh, .venv/bin/python and node), so no Docker is needed."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "docker" / "entrypoint.sh"


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def co(tmp_path):
    root = tmp_path / "co"
    (root / ".venv" / "bin").mkdir(parents=True)
    log = tmp_path / "calls.log"
    _exe(root / "terrarium.sh", f'#!/bin/sh\necho "terrarium.sh $*" >> {log}\n')
    _exe(root / "smoke-test.sh", f'#!/bin/sh\necho "smoke-test.sh $*" >> {log}\n')
    _exe(root / ".venv" / "bin" / "python",
         f'#!/bin/sh\necho "python $*" >> {log}\n')
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _exe(bindir / "node", f'#!/bin/sh\necho "node $*" >> {log}\n')
    (root / "requirements.txt").write_text("websockets>=13\n")
    (root / "requirements-dev.txt").write_text("-r requirements.txt\npytest\n")
    (root / "tests" / "js").mkdir(parents=True)
    (root / "tests" / "js" / "a.test.js").write_text("")
    launcher = tmp_path / "terrarium-dev"
    launcher.write_text("#!/usr/bin/env bash\n# the launcher\n")
    env = {"PATH": f"{bindir}:/usr/bin:/bin",
           "TD_LAUNCHER_PATH": str(launcher)}
    return root, log, env


def _run(co, *args):
    root, log, env = co
    return subprocess.run(["bash", str(ENTRYPOINT), *args], cwd=root, env=env,
                          capture_output=True, text=True)


def _calls(co):
    log = co[1]
    return log.read_text().splitlines() if log.exists() else []


def _req_hash(root: Path) -> str:
    data = (root / "requirements.txt").read_bytes() + \
        (root / "requirements-dev.txt").read_bytes()
    return hashlib.sha256(data).hexdigest()


def test_run_forwards_args_to_terrarium_sh(co):
    assert _run(co, "run", "--room", "TEST").returncode == 0
    assert _calls(co) == ["terrarium.sh --room TEST"]


def test_smoke_forwards_args_to_smoke_test_sh(co):
    assert _run(co, "smoke", "--ci").returncode == 0
    assert _calls(co) == ["smoke-test.sh --ci"]


def test_clean_runs_terrarium_clean(co):
    assert _run(co, "clean").returncode == 0
    assert _calls(co) == ["terrarium.sh --clean"]


def test_test_runs_pytest_then_node(co):
    assert _run(co, "test").returncode == 0
    assert _calls(co) == ["python -m pytest tests -q",
                          "node --test tests/js/a.test.js"]


def test_launcher_prints_the_launcher(co):
    result = _run(co, "launcher")
    assert result.returncode == 0
    assert result.stdout == "#!/usr/bin/env bash\n# the launcher\n"


def test_stamp_venv_writes_requirements_hash(co):
    root = co[0]
    assert _run(co, "stamp-venv").returncode == 0
    assert (root / ".venv" / ".td-req-hash").read_text().strip() == _req_hash(root)


def test_matching_stamp_skips_pip(co):
    root = co[0]
    (root / ".venv" / ".td-req-hash").write_text(_req_hash(root) + "\n")
    assert _run(co, "run").returncode == 0
    assert _calls(co) == ["terrarium.sh "]


def test_drifted_stamp_installs_and_restamps(co):
    root = co[0]
    (root / ".venv" / ".td-req-hash").write_text("stale\n")
    assert _run(co, "run").returncode == 0
    assert _calls(co) == ["python -m pip install -q -r requirements-dev.txt",
                          "terrarium.sh "]
    assert (root / ".venv" / ".td-req-hash").read_text().strip() == _req_hash(root)


def test_no_stamp_skips_pip(co):
    assert _run(co, "smoke").returncode == 0
    assert _calls(co) == ["smoke-test.sh "]


def test_unknown_command_fails_with_usage(co):
    result = _run(co, "bogus")
    assert result.returncode != 0
    assert "terrarium-dev launcher" in result.stderr
