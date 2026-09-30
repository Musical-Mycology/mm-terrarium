"""docker/build.sh --dry-run: the buildx command it would run."""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "docker" / "build.sh"
PINS = ROOT / "docker" / "pins.env"


def _dry(*args, **env):
    r = subprocess.run(["bash", str(BUILD), "--dry-run", *args],
                       env={"PATH": "/usr/bin:/bin", **env},
                       capture_output=True, text=True)
    return r


def _pins() -> dict[str, str]:
    out = {}
    for line in PINS.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value
    return out


def test_pins_file_has_every_key_with_full_shas():
    pins = _pins()
    assert set(pins) == {"BASE_IMAGE", "ARCO_REPO", "ARCO_SHA", "O2_REPO",
                         "O2_SHA", "LUXAETERNA_REPO", "LUXAETERNA_SHA"}
    for key in ("ARCO_SHA", "O2_SHA", "LUXAETERNA_SHA"):
        assert len(pins[key]) == 40, key
    assert "@sha256:" in pins["BASE_IMAGE"]


def test_dry_run_builds_amd64_local_with_every_pin():
    r = _dry()
    assert r.returncode == 0, r.stderr
    argv = shlex.split(r.stdout)
    assert argv[:3] == ["docker", "buildx", "build"]
    assert "--platform" in argv and argv[argv.index("--platform") + 1] == "linux/amd64"
    assert "--load" in argv
    assert argv[argv.index("-t") + 1] == "ghcr.io/musical-mycology/terrarium-dev:local"
    assert argv[argv.index("-f") + 1] == "docker/Dockerfile"
    build_args = [argv[i + 1] for i, a in enumerate(argv) if a == "--build-arg"]
    assert sorted(build_args) == sorted(f"{k}={v}" for k, v in _pins().items())
    assert argv[-1] == "."


def test_dry_run_honors_tag():
    argv = shlex.split(_dry("--tag", "sha-abc1234").stdout)
    assert argv[argv.index("-t") + 1] == "ghcr.io/musical-mycology/terrarium-dev:sha-abc1234"


def test_refuses_a_pins_file_missing_a_key(tmp_path):
    bad = tmp_path / "pins.env"
    bad.write_text("\n".join(l for l in PINS.read_text().splitlines()
                             if not l.startswith("O2_SHA=")) + "\n")
    r = _dry(TD_PINS_FILE=str(bad))
    assert r.returncode != 0
    assert "O2_SHA" in r.stderr
