# Terrarium Dev Container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pre-built linux/amd64 image plus a `terrarium-dev` launcher so a teammate can pull one image and run the Terrarium, its tests and its smoke run with no builds and no sibling clones.

**Architecture:** A three-stage `docker/Dockerfile` (base, build, runtime) bakes o2, the patched Arco server, the venv and a snapshot of mm-terrarium. An image-side `docker/entrypoint.sh` maps commands onto the in-repo scripts; a host-side bash launcher `docker/terrarium-dev` builds the `docker run` (host network, host Avahi via bind mounts, PulseAudio, checkout mount plus a per-checkout venv volume). `docker/build.sh` builds from `docker/pins.env`.

**Tech Stack:** Docker (buildx), bash (must stay Bash 3.2 compatible: the Mac's `/bin/bash` runs the tests), Ubuntu 26.04, Python 3 venv, pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-terrarium-dev-container-design.md` (read it first; this plan argues from it).

## Global Constraints

- Image: `ghcr.io/musical-mycology/terrarium-dev`, platform `linux/amd64` only.
- Base: `ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78`.
- Pins (full SHAs, fetch-by-SHA needs them): arco `c8092e292a2a75fb344ee16c4c9369010fed1ee6` from `https://github.com/Musical-Mycology/arco.git`; o2 `f21499e1780783a484831023b900212ea9bfb500` from `https://github.com/rbdannenberg/o2.git`; luxaeterna `9e2eb6205d4db36f8088472c3d254eda6ae6f433` from `https://github.com/Musical-Mycology/luxaeterna.git`.
- The Arco Linux patch is applied only inside the image build; never commit it to any arco checkout.
- In-image paths: `/opt/mm/arco` (arco tree, `MM_ARCO_PATH` and `ARCO_ROOT`), `/opt/mm/terrarium` (snapshot), `/work/.venv` (the one venv; `/opt/mm/terrarium/.venv` is a symlink to it), `/usr/local/bin/terrarium-dev`, `/usr/local/lib/terrarium-dev/{entrypoint.sh,selfcheck.sh}`, `/etc/asound.conf`.
- Container label `mm.terrarium-dev=1`; venv volumes are named `terrarium-venv-<cksum of abs checkout path>` and labelled `mm.terrarium-dev.venv=1` and `mm.terrarium-dev.image=<image id>`.
- Ports that collide under host networking: 8080 (Arco), 8788 (guest page), 8772 (Console).
- Every launcher refusal names its fix. No em dashes in any user-facing text or docs.
- Tests: `.venv/bin/python -m pytest tests -v` (never bare `python3`; a fresh worktree needs `ln -s "$HOME/projects/mm-terrarium/.venv" .venv` first).

---

### Task 1: `harness/clean.py` falls back to the current checkout when git fails

**Files:**
- Modify: `harness/clean.py:37-44` (`list_checkouts`)
- Test: `tests/test_clean.py`

**Interfaces:**
- Produces: `list_checkouts(*, run=subprocess.run, isdir=os.path.isdir, cwd=os.getcwd) -> list[str]`. Returns `[cwd()]` when `git worktree list` raises `subprocess.CalledProcessError` or `FileNotFoundError`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_clean.py`)

```python
def test_list_checkouts_falls_back_to_cwd_when_git_fails():
    def run(cmd, **kw):
        raise subprocess.CalledProcessError(128, cmd, stderr="not a git repo")

    got = clean.list_checkouts(run=run, isdir=lambda p: True,
                               cwd=lambda: "/opt/mm/terrarium")
    assert got == ["/opt/mm/terrarium"]


def test_list_checkouts_falls_back_to_cwd_when_git_missing():
    def run(cmd, **kw):
        raise FileNotFoundError("git")

    got = clean.list_checkouts(run=run, isdir=lambda p: True,
                               cwd=lambda: "/work")
    assert got == ["/work"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_clean.py -v -k falls_back`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'cwd'`)

- [ ] **Step 3: Implement**

Replace `list_checkouts` in `harness/clean.py` with:

```python
def list_checkouts(*, run=subprocess.run, isdir=os.path.isdir,
                   cwd=os.getcwd) -> list[str]:
    """Every checkout of this repo (the main clone first), minus any whose
    directory no longer exists (a prunable worktree). Where git cannot list
    worktrees (the dev container's snapshot has no .git, and a mounted git
    worktree's .git file points at a host path), only the current checkout
    is swept."""
    try:
        result = run(["git", "worktree", "list", "--porcelain"],
                     capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [cwd()]
    paths = [line[len("worktree "):] for line in result.stdout.splitlines()
             if line.startswith("worktree ")]
    return [p for p in paths if isdir(p)]
```

- [ ] **Step 4: Run the file's tests**

Run: `.venv/bin/python -m pytest tests/test_clean.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness/clean.py tests/test_clean.py
git commit -m "fix(clean): sweep the current checkout when git cannot list worktrees"
```

---

### Task 2: Image-side scripts (entrypoint, selfcheck, ALSA config)

**Files:**
- Create: `docker/entrypoint.sh` (executable)
- Create: `docker/selfcheck.sh` (executable)
- Create: `docker/asound.conf`
- Test: `tests/test_docker_entrypoint.py`

**Interfaces:**
- Produces: `entrypoint.sh <command> [args]` with commands `run`, `smoke`, `test`, `shell`, `clean`, `launcher`, `selfcheck`, `stamp-venv`, `help`. Runs in the container's working dir (`/work` or `/opt/mm/terrarium`).
- Produces: env override `TD_LAUNCHER_PATH` (default `/usr/local/bin/terrarium-dev`) and `TD_SELFCHECK_PATH` (default `/usr/local/lib/terrarium-dev/selfcheck.sh`), for tests.
- Produces: venv stamp file `.venv/.td-req-hash` holding the sha256 of `requirements.txt` followed by `requirements-dev.txt`. `stamp-venv` writes it (Task 5's Dockerfile calls it). Before `run`, `smoke`, `test`, `shell`, `clean`, if the stamp exists and differs, run `.venv/bin/python -m pip install -q -r requirements-dev.txt` and restamp.

- [ ] **Step 1: Write the failing tests** (`tests/test_docker_entrypoint.py`)

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_docker_entrypoint.py -v`
Expected: FAIL (entrypoint.sh does not exist)

- [ ] **Step 3: Implement `docker/entrypoint.sh`**

```bash
#!/usr/bin/env bash
# terrarium-dev image entrypoint: maps launcher commands onto the in-repo
# scripts, in the container's working dir (/work for a mounted checkout,
# /opt/mm/terrarium for the baked snapshot). Spec:
# docs/superpowers/specs/2026-09-29-terrarium-dev-container-design.md
set -euo pipefail

TD_LAUNCHER_PATH="${TD_LAUNCHER_PATH:-/usr/local/bin/terrarium-dev}"
TD_SELFCHECK_PATH="${TD_SELFCHECK_PATH:-/usr/local/lib/terrarium-dev/selfcheck.sh}"
STAMP=".venv/.td-req-hash"

req_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    cat requirements.txt requirements-dev.txt | sha256sum | cut -d' ' -f1
  else
    cat requirements.txt requirements-dev.txt | shasum -a 256 | cut -d' ' -f1
  fi
}

# A mounted branch may need Python deps the image lacks: install them into
# the venv volume once, keyed on the requirements files' hash.
prepare_venv() {
  [ -f "$STAMP" ] || return 0
  [ -f requirements.txt ] && [ -f requirements-dev.txt ] || return 0
  local want
  want="$(req_hash)"
  if [ "$(cat "$STAMP")" != "$want" ]; then
    echo "terrarium-dev: requirements changed; installing into the venv volume" >&2
    .venv/bin/python -m pip install -q -r requirements-dev.txt
    printf '%s\n' "$want" > "$STAMP"
  fi
}

usage() {
  echo "this image is driven by the terrarium-dev launcher; install it with:" >&2
  echo "  docker run --rm ghcr.io/musical-mycology/terrarium-dev:main launcher > terrarium-dev && chmod +x terrarium-dev" >&2
}

cmd="${1:-help}"
if [ $# -gt 0 ]; then shift; fi
case "$cmd" in
  launcher)   exec cat "$TD_LAUNCHER_PATH" ;;
  selfcheck)  exec "$TD_SELFCHECK_PATH" ;;
  stamp-venv) req_hash > "$STAMP" ;;
  run)        prepare_venv; exec ./terrarium.sh "$@" ;;
  smoke)      prepare_venv; exec ./smoke-test.sh "$@" ;;
  clean)      prepare_venv; exec ./terrarium.sh --clean ;;
  shell)      prepare_venv; exec bash "$@" ;;
  test)
    prepare_venv
    .venv/bin/python -m pytest tests -q "$@"
    exec node --test tests/js/*.test.js
    ;;
  help)       usage ;;
  *)          usage; exit 2 ;;
esac
```

Note the `"$@"` after `pytest tests -q` is empty in the tested call, so the logged line is exactly `python -m pytest tests -q`.

- [ ] **Step 4: Implement `docker/selfcheck.sh`**

```bash
#!/usr/bin/env bash
# Hermetic image check (spec section 7): in the container's own network
# namespace there is no host mDNS daemon to contend with, so start a private
# dbus + avahi-daemon, then run both test suites and a --ci smoke run on the
# baked snapshot. Must run as root: docker run --rm IMAGE selfcheck
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "selfcheck: must run as root (docker run --rm IMAGE selfcheck)" >&2; exit 1; }

mkdir -p /run/dbus
dbus-uuidgen --ensure
dbus-daemon --system --fork
avahi-daemon --daemonize --no-chroot
for _ in $(seq 50); do
  [ -e /run/avahi-daemon/socket ] && break
  sleep 0.1
done
[ -e /run/avahi-daemon/socket ] || { echo "selfcheck: avahi-daemon did not start" >&2; exit 1; }

cd /opt/mm/terrarium
echo "== pytest"
.venv/bin/python -m pytest tests -q
echo "== node --test"
node --test tests/js/*.test.js
echo "== smoke-test --ci"
./smoke-test.sh --ci
echo "SELFCHECK_OK"
```

- [ ] **Step 5: Create `docker/asound.conf`**

```
# Route ALSA's default device (PortAudio inside Arco) to PulseAudio: WSLg's
# server, or the host's native one, via PULSE_SERVER set by terrarium-dev.
pcm.!default { type pulse }
ctl.!default { type pulse }
```

- [ ] **Step 6: Make the scripts executable and run the tests**

Run: `chmod +x docker/entrypoint.sh docker/selfcheck.sh && .venv/bin/python -m pytest tests/test_docker_entrypoint.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add docker/entrypoint.sh docker/selfcheck.sh docker/asound.conf tests/test_docker_entrypoint.py
git commit -m "feat(docker): image entrypoint, hermetic selfcheck and ALSA-to-pulse config"
```

---

### Task 3: The `terrarium-dev` launcher

**Files:**
- Create: `docker/terrarium-dev` (executable)
- Create: `tests/launcher_fakes.py`
- Test: `tests/test_terrarium_dev.py`

**Interfaces:**
- Consumes: entrypoint commands from Task 2 (`run`, `smoke`, `test`, `shell`, `clean`, `selfcheck`), passed as the first argument after the image.
- Produces: CLI `terrarium-dev [--tag TAG] [--checkout PATH] [--headless] [COMMAND] [ARGS...]`, commands `run` (default), `smoke`, `test`, `shell`, `clean`, `update`, `use TAG`, `selfcheck`, `help`.
- Produces: env overrides (defaults in brackets): `TD_IMAGE_REPO` [`ghcr.io/musical-mycology/terrarium-dev`], `TD_CONFIG_DIR` [`${XDG_CONFIG_HOME:-$HOME/.config}/terrarium-dev`], `TD_AVAHI_DIR` [`/run/avahi-daemon`], `TD_DBUS_SOCKET` [`/run/dbus/system_bus_socket`], `TD_WSLG_DIR` [`/mnt/wslg`], `TD_RUNS_DIR` [`$HOME/terrarium-runs`], `TD_UNAME` [`$(uname -s)`].
- Produces: shell functions Task 4 extends: `venv_volume <abs checkout>` (prints the volume name) and `build_run_args <host|plain> <checkout-or-empty> <wslg|native|none>` (fills the global array `args`).

- [ ] **Step 1: Write the fakes** (`tests/launcher_fakes.py`)

```python
"""Fake `docker` and `wslinfo` for driving docker/terrarium-dev offline.

The fake docker appends each argv as one JSON line to $FAKE_DOCKER_LOG.
Behaviour knobs (env):
  FAKE_DOCKER_INFO_FAIL=1       `docker info` exits 1
  FAKE_DOCKER_PS=<ids>          `docker ps -q ...` prints this
  FAKE_DOCKER_IMAGE_ID=<id>     `docker image inspect` prints this (default sha256:cur)
  FAKE_DOCKER_VOLUMES=a=l1,b=l2 volumes and their mm.terrarium-dev.image labels:
                                `volume ls -q` prints names; `volume inspect NAME`
                                prints the label, or exits 1 if NAME is absent
Everything else exits 0.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

_DOCKER = '''\
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
vols = dict(p.split("=", 1) for p in
            os.environ.get("FAKE_DOCKER_VOLUMES", "").split(",") if p)
if argv[:1] == ["info"]:
    sys.exit(1 if os.environ.get("FAKE_DOCKER_INFO_FAIL") else 0)
if argv[:1] == ["ps"]:
    print(os.environ.get("FAKE_DOCKER_PS", ""))
elif argv[:2] == ["image", "inspect"]:
    print(os.environ.get("FAKE_DOCKER_IMAGE_ID", "sha256:cur"))
elif argv[:2] == ["volume", "ls"]:
    print("\\n".join(vols))
elif argv[:2] == ["volume", "inspect"]:
    name = argv[-1]
    if name not in vols:
        sys.exit(1)
    print(vols[name])
'''


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_fake_docker(bindir: Path) -> None:
    _exe(bindir / "docker", f"#!{sys.executable}\n{_DOCKER}")


def install_fake_wslinfo(bindir: Path, mode: str) -> None:
    _exe(bindir / "wslinfo", f"#!/bin/sh\necho {mode}\n")
```

- [ ] **Step 2: Write the failing tests** (`tests/test_terrarium_dev.py`)

```python
"""docker/terrarium-dev: the docker run it builds, and every preflight
refusal and warning. Drives the real script against a fake docker
(tests/launcher_fakes.py), so no Docker is needed."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.launcher_fakes import install_fake_docker, install_fake_wslinfo

LAUNCHER = Path(__file__).resolve().parent.parent / "docker" / "terrarium-dev"
IMAGE = "ghcr.io/musical-mycology/terrarium-dev"


@pytest.fixture
def h(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    install_fake_docker(bindir)
    avahi = tmp_path / "avahi"
    avahi.mkdir()
    (avahi / "socket").touch()
    wslg = tmp_path / "wslg"
    wslg.mkdir()
    (wslg / "PulseServer").touch()
    co = tmp_path / "co"
    (co / "control").mkdir(parents=True)
    (co / "terrarium.sh").touch()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "TD_CONFIG_DIR": str(tmp_path / "config"),
        "TD_AVAHI_DIR": str(avahi),
        "TD_DBUS_SOCKET": "/fake/dbus.sock",
        "TD_WSLG_DIR": str(wslg),
        "TD_RUNS_DIR": str(tmp_path / "runs"),
        "TD_UNAME": "Linux",
        "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
    }
    return SimpleNamespace(tmp=tmp_path, bin=bindir, avahi=avahi, wslg=wslg,
                           co=co.resolve(), elsewhere=elsewhere, env=env)


def launch(h, *args, cwd=None, **extra_env):
    env = {**h.env, **extra_env}
    return subprocess.run(["bash", str(LAUNCHER), *args], env=env,
                          cwd=cwd or h.co, capture_output=True, text=True)


def calls(h):
    log = Path(h.env["FAKE_DOCKER_LOG"])
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def run_call(h):
    runs = [c for c in calls(h) if c[:1] == ["run"]]
    assert len(runs) == 1, calls(h)
    return runs[0]


def pairs(argv, flag):
    """Every value following `flag` in argv."""
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


def test_run_in_checkout_mounts_checkout_and_venv_volume(h):
    r = launch(h, "run", "--room", "TEST")
    assert r.returncode == 0, r.stderr
    argv = run_call(h)
    vols = pairs(argv, "-v")
    assert f"{h.co}:/work" in vols
    assert any(v.startswith("terrarium-venv-") and v.endswith(":/work/.venv")
               for v in vols)
    assert pairs(argv, "-w") == ["/work"]
    assert argv[-4:] == [f"{IMAGE}:main", "run", "--room", "TEST"]


def test_run_outside_checkout_uses_snapshot_and_persists_runs(h):
    r = launch(h, "run", cwd=h.elsewhere)
    assert r.returncode == 0, r.stderr
    argv = run_call(h)
    assert f"{h.env['TD_RUNS_DIR']}:/opt/mm/terrarium/runs" in pairs(argv, "-v")
    assert pairs(argv, "-w") == ["/opt/mm/terrarium"]
    assert not any(v.endswith(":/work") for v in pairs(argv, "-v"))


def test_run_uses_host_network_label_user_and_host_avahi(h):
    launch(h, "run")
    argv = run_call(h)
    assert argv[:3] == ["run", "--rm", "--init"]
    assert pairs(argv, "--network") == ["host"]
    assert pairs(argv, "--label") == ["mm.terrarium-dev=1"]
    assert len(pairs(argv, "--user")) == 1
    assert "HOME=/tmp" in pairs(argv, "-e")
    vols = pairs(argv, "-v")
    assert "/fake/dbus.sock:/run/dbus/system_bus_socket" in vols
    assert f"{h.avahi}:/run/avahi-daemon" in vols


def test_run_wires_wslg_audio(h):
    launch(h, "run")
    argv = run_call(h)
    assert f"{h.wslg}:/mnt/wslg" in pairs(argv, "-v")
    assert "PULSE_SERVER=unix:/mnt/wslg/PulseServer" in pairs(argv, "-e")


def test_run_wires_native_pulse_when_no_wslg(h):
    (h.wslg / "PulseServer").unlink()
    xdg = h.tmp / "xdg"
    (xdg / "pulse").mkdir(parents=True)
    (xdg / "pulse" / "native").touch()
    launch(h, "run", XDG_RUNTIME_DIR=str(xdg))
    argv = run_call(h)
    assert f"{xdg}/pulse/native:/run/pulse/native" in pairs(argv, "-v")
    assert "PULSE_SERVER=unix:/run/pulse/native" in pairs(argv, "-e")


def test_refuses_without_pulse_and_suggests_headless(h):
    (h.wslg / "PulseServer").unlink()
    r = launch(h, "run")
    assert r.returncode != 0
    assert "--headless" in r.stderr
    assert not [c for c in calls(h) if c[:1] == ["run"]]


def test_headless_skips_pulse_check_and_audio_mounts(h):
    (h.wslg / "PulseServer").unlink()
    r = launch(h, "--headless", "run")
    assert r.returncode == 0, r.stderr
    argv = run_call(h)
    assert not any("PULSE_SERVER" in e for e in pairs(argv, "-e"))
    assert not any(":/mnt/wslg" in v for v in pairs(argv, "-v"))


def test_refuses_without_avahi_socket_and_names_the_fix(h):
    (h.avahi / "socket").unlink()
    r = launch(h, "run")
    assert r.returncode != 0
    assert "avahi-daemon" in r.stderr and "systemctl enable" in r.stderr


def test_refuses_when_docker_unreachable(h):
    r = launch(h, "run", FAKE_DOCKER_INFO_FAIL="1")
    assert r.returncode != 0
    assert "Docker Engine" in r.stderr and "docker/README.md" in r.stderr


def test_refuses_when_a_stack_is_already_running(h):
    r = launch(h, "run", FAKE_DOCKER_PS="abc123")
    assert r.returncode != 0
    assert "8080" in r.stderr and "8788" in r.stderr and "8772" in r.stderr


def test_shell_ignores_a_running_stack(h):
    r = launch(h, "shell", FAKE_DOCKER_PS="abc123")
    assert r.returncode == 0, r.stderr
    assert run_call(h)[-1] == "shell"


def test_warns_on_wsl_nat(h):
    install_fake_wslinfo(h.bin, "nat")
    r = launch(h, "run")
    assert r.returncode == 0
    assert "NAT" in r.stderr and "mirrored" in r.stderr


def test_no_nat_warning_in_mirrored_mode(h):
    install_fake_wslinfo(h.bin, "mirrored")
    r = launch(h, "run")
    assert "NAT" not in r.stderr


def test_warns_on_macos(h):
    r = launch(h, "--headless", "test", TD_UNAME="Darwin")
    assert r.returncode == 0
    assert "amd64" in r.stderr


def test_tag_flag_selects_image(h):
    launch(h, "--tag", "v2026-10-01", "run")
    assert f"{IMAGE}:v2026-10-01" in run_call(h)


def test_use_saves_default_tag(h):
    assert launch(h, "use", "sha-abc1234").returncode == 0
    launch(h, "run")
    assert f"{IMAGE}:sha-abc1234" in run_call(h)


def test_explicit_tag_beats_saved_tag(h):
    launch(h, "use", "sha-abc1234")
    launch(h, "--tag", "local", "run")
    assert f"{IMAGE}:local" in run_call(h)


def test_test_command_runs_without_host_network_or_label(h):
    (h.avahi / "socket").unlink()
    (h.wslg / "PulseServer").unlink()
    r = launch(h, "test")
    assert r.returncode == 0, r.stderr
    argv = run_call(h)
    assert "--network" not in argv and "--label" not in argv
    assert argv[-1] == "test"


def test_selfcheck_runs_the_image_hermetically(h):
    r = launch(h, "selfcheck")
    assert r.returncode == 0, r.stderr
    assert run_call(h) == ["run", "--rm", f"{IMAGE}:main", "selfcheck"]


def test_checkout_flag_refuses_a_non_checkout(h):
    r = launch(h, "--checkout", str(h.elsewhere), "run")
    assert r.returncode != 0
    assert "not an mm-terrarium checkout" in r.stderr


def test_checkout_flag_mounts_that_checkout(h):
    r = launch(h, "--checkout", str(h.co), "run", cwd=h.elsewhere)
    assert r.returncode == 0, r.stderr
    assert f"{h.co}:/work" in pairs(run_call(h), "-v")


def test_unknown_command_fails(h):
    r = launch(h, "bogus")
    assert r.returncode != 0
    assert "unknown command: bogus" in r.stderr
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_dev.py -v`
Expected: FAIL (launcher does not exist)

- [ ] **Step 4: Implement `docker/terrarium-dev`**

```bash
#!/usr/bin/env bash
# terrarium-dev: run the pre-built Terrarium dev image. Setup and usage:
# docker/README.md. Spec:
# docs/superpowers/specs/2026-09-29-terrarium-dev-container-design.md
# Keep this Bash 3.2 compatible (macOS /bin/bash runs its tests).
set -euo pipefail

TD_IMAGE_REPO="${TD_IMAGE_REPO:-ghcr.io/musical-mycology/terrarium-dev}"
TD_CONFIG_DIR="${TD_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/terrarium-dev}"
TD_AVAHI_DIR="${TD_AVAHI_DIR:-/run/avahi-daemon}"
TD_DBUS_SOCKET="${TD_DBUS_SOCKET:-/run/dbus/system_bus_socket}"
TD_WSLG_DIR="${TD_WSLG_DIR:-/mnt/wslg}"
TD_RUNS_DIR="${TD_RUNS_DIR:-$HOME/terrarium-runs}"
TD_UNAME="${TD_UNAME:-$(uname -s)}"
LABEL="mm.terrarium-dev=1"

die()  { echo "terrarium-dev: $*" >&2; exit 1; }
warn() { echo "terrarium-dev: WARNING: $*" >&2; }

usage() {
  cat <<'EOF'
usage: terrarium-dev [--tag TAG] [--checkout PATH] [--headless] [COMMAND] [ARGS...]

commands:
  run [ARGS]    ./terrarium.sh [ARGS] (the default)
  smoke [ARGS]  ./smoke-test.sh [ARGS]
  test          pytest, then node --test
  shell         an interactive bash in the container
  clean         ./terrarium.sh --clean
  update        pull the selected tag and prune stale venv volumes
  use TAG       save TAG as the default (main, sha-<short>, v<date>, local)
  selfcheck     hermetic image check; needs no host setup

--checkout defaults to the current directory when it is an mm-terrarium
checkout; otherwise the snapshot of main baked into the image runs.
EOF
}

tag=""; checkout=""; headless=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tag)      [ $# -ge 2 ] || die "--tag needs a value"; tag="$2"; shift 2 ;;
    --checkout) [ $# -ge 2 ] || die "--checkout needs a value"; checkout="$2"; shift 2 ;;
    --headless) headless=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) break ;;
  esac
done
cmd="${1:-run}"
if [ $# -gt 0 ]; then shift; fi

resolve_tag() {
  if [ -n "$tag" ]; then echo "$tag"
  elif [ -f "$TD_CONFIG_DIR/tag" ]; then cat "$TD_CONFIG_DIR/tag"
  else echo main
  fi
}

is_checkout() { [ -f "$1/terrarium.sh" ] && [ -d "$1/control" ]; }

# Absolute checkout path, or nothing for snapshot mode.
resolve_checkout() {
  if [ -n "$checkout" ]; then
    is_checkout "$checkout" || die "--checkout $checkout is not an mm-terrarium checkout (needs terrarium.sh and control/)"
    (cd "$checkout" && pwd -P)
  elif is_checkout "$PWD"; then
    pwd -P
  fi
}

venv_volume() { echo "terrarium-venv-$(printf '%s' "$1" | cksum | awk '{print $1}')"; }

check_macos() {
  if [ "$TD_UNAME" = Darwin ]; then
    warn "macOS: the image is linux/amd64 only and Docker Desktop's network is its own VM, so real devices cannot reach it. A native setup (docs/MM_TERRARIUM.md, Running it) is the better choice here."
  fi
}

check_docker() {
  docker info >/dev/null 2>&1 || die "Docker is not reachable. Install Docker Engine inside your WSL distro, not Docker Desktop (docker/README.md, Docker Engine in WSL2)."
}

check_avahi() {
  [ -e "$TD_AVAHI_DIR/socket" ] || die "avahi-daemon is not running on this host, so Arco can't advertise over mDNS. Fix: sudo apt install avahi-daemon && sudo systemctl enable --now avahi-daemon (on WSL, systemd must be enabled in /etc/wsl.conf; see docker/README.md)."
}

# wslg | native | none
audio_mode() {
  if [ -e "$TD_WSLG_DIR/PulseServer" ]; then echo wslg
  elif [ -n "${XDG_RUNTIME_DIR:-}" ] && [ -e "$XDG_RUNTIME_DIR/pulse/native" ]; then echo native
  else echo none
  fi
}

check_wsl_nat() {
  command -v wslinfo >/dev/null 2>&1 || return 0
  if [ "$(wslinfo --networking-mode 2>/dev/null || true)" = nat ]; then
    warn "WSL is in NAT networking mode. Simulated devices work, but real devices cannot reach Arco. Set networkingMode=mirrored (docker/README.md)."
  fi
}

check_one_stack() {
  [ -z "$(docker ps -q --filter "label=$LABEL")" ] || die "a terrarium-dev stack is already running (docker ps --filter label=$LABEL). Under host networking two stacks collide on ports 8080, 8788 and 8772; stop it first."
}

# Fills the global array `args` with everything between `docker` and the image.
# $1: host (run/smoke/shell) or plain (test/clean); $2: checkout or ""; $3: audio mode.
build_run_args() {
  local mode="$1" co="$2" audio="$3"
  args=(run --rm --init -i)
  if [ -t 0 ] && [ -t 1 ]; then args+=(-t); fi
  args+=(--user "$(id -u):$(id -g)" -e HOME=/tmp)
  if [ "$mode" = host ]; then
    args+=(--network host --label "$LABEL"
           -v "$TD_DBUS_SOCKET:/run/dbus/system_bus_socket"
           -v "$TD_AVAHI_DIR:/run/avahi-daemon")
    case "$audio" in
      wslg)   args+=(-v "$TD_WSLG_DIR:/mnt/wslg" -e PULSE_SERVER=unix:/mnt/wslg/PulseServer) ;;
      native) args+=(-v "$XDG_RUNTIME_DIR/pulse/native:/run/pulse/native" -e PULSE_SERVER=unix:/run/pulse/native) ;;
    esac
  fi
  if [ -n "$co" ]; then
    args+=(-v "$co:/work" -v "$(venv_volume "$co"):/work/.venv" -w /work)
  else
    mkdir -p "$TD_RUNS_DIR"
    args+=(-v "$TD_RUNS_DIR:/opt/mm/terrarium/runs" -w /opt/mm/terrarium)
  fi
}

image="$TD_IMAGE_REPO:$(resolve_tag)"

case "$cmd" in
  use)
    [ $# -eq 1 ] || die "usage: terrarium-dev use TAG"
    mkdir -p "$TD_CONFIG_DIR"
    printf '%s\n' "$1" > "$TD_CONFIG_DIR/tag"
    echo "terrarium-dev: default tag is now $1"
    ;;
  update)
    check_docker
    docker pull "$image"
    ;;
  selfcheck)
    check_docker
    exec docker run --rm "$image" selfcheck
    ;;
  run|smoke|shell)
    check_macos
    check_docker
    check_avahi
    audio=none
    if [ "$headless" -eq 0 ]; then
      audio="$(audio_mode)"
      [ "$audio" != none ] || die "no PulseAudio socket found ($TD_WSLG_DIR/PulseServer or \$XDG_RUNTIME_DIR/pulse/native). Pass --headless to run silent."
    fi
    check_wsl_nat
    if [ "$cmd" != shell ]; then check_one_stack; fi
    co="$(resolve_checkout)" || exit 1
    build_run_args host "$co" "$audio"
    exec docker "${args[@]}" "$image" "$cmd" "$@"
    ;;
  test|clean)
    check_macos
    check_docker
    co="$(resolve_checkout)" || exit 1
    build_run_args plain "$co" none
    exec docker "${args[@]}" "$image" "$cmd" "$@"
    ;;
  help) usage ;;
  *) usage >&2; die "unknown command: $cmd" ;;
esac
```

Note: the macOS warning prints first (the spec lists it sixth). It is a warning, and printed first it explains the refusals that may follow on a Mac.

- [ ] **Step 5: Make it executable and run the tests**

Run: `chmod +x docker/terrarium-dev && .venv/bin/python -m pytest tests/test_terrarium_dev.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add docker/terrarium-dev tests/launcher_fakes.py tests/test_terrarium_dev.py
git commit -m "feat(docker): terrarium-dev launcher with preflight checks"
```

---

### Task 4: Venv volume lifecycle (image-digest stamping and `update` pruning)

**Files:**
- Modify: `docker/terrarium-dev` (add `image_id`, `ensure_venv_volume`, `prune_venv_volumes`; call them)
- Test: `tests/test_terrarium_dev.py` (append)

**Interfaces:**
- Consumes: `venv_volume`, `build_run_args`, the global `image`, fake docker knobs `FAKE_DOCKER_IMAGE_ID` and `FAKE_DOCKER_VOLUMES` (Task 3).
- Produces: in checkout mode, before `docker run`, the venv volume exists and carries label `mm.terrarium-dev.image=<image id>`; a mismatched or missing one is removed and recreated (Docker then seeds the empty volume from the image's `/work/.venv`). `update` removes every `mm.terrarium-dev.venv=1` volume whose image label differs from the pulled image's id.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_terrarium_dev.py`)

```python
def _vol_name(h):
    argv = run_call(h)
    return next(v.split(":")[0] for v in pairs(argv, "-v")
                if v.endswith(":/work/.venv"))


def test_creates_labelled_venv_volume_when_missing(h):
    r = launch(h, "run")
    assert r.returncode == 0, r.stderr
    vol = _vol_name(h)
    creates = [c for c in calls(h) if c[:2] == ["volume", "create"]]
    assert creates == [["volume", "create",
                        "--label", "mm.terrarium-dev.venv=1",
                        "--label", "mm.terrarium-dev.image=sha256:cur", vol]]


def test_keeps_venv_volume_stamped_with_current_image(h):
    launch(h, "run")                       # learn the volume name
    vol = _vol_name(h)
    Path(h.env["FAKE_DOCKER_LOG"]).unlink()
    launch(h, "run", FAKE_DOCKER_VOLUMES=f"{vol}=sha256:cur")
    assert not [c for c in calls(h) if c[:2] in (["volume", "create"],
                                                 ["volume", "rm"])]


def test_recreates_venv_volume_from_an_older_image(h):
    launch(h, "run")
    vol = _vol_name(h)
    Path(h.env["FAKE_DOCKER_LOG"]).unlink()
    launch(h, "run", FAKE_DOCKER_VOLUMES=f"{vol}=sha256:old")
    ops = [c[:2] for c in calls(h) if c[:1] == ["volume"]]
    assert ["volume", "rm"] in ops and ["volume", "create"] in ops
    assert ops.index(["volume", "rm"]) < ops.index(["volume", "create"])


def test_snapshot_mode_touches_no_volume(h):
    launch(h, "run", cwd=h.elsewhere)
    assert not [c for c in calls(h) if c[:1] == ["volume"]]


def test_update_pulls_then_prunes_volumes_from_other_images(h):
    r = launch(h, "update",
               FAKE_DOCKER_VOLUMES="terrarium-venv-1=sha256:old,terrarium-venv-2=sha256:cur")
    assert r.returncode == 0, r.stderr
    c = calls(h)
    assert ["pull", f"{IMAGE}:main"] in c
    rms = [x for x in c if x[:2] == ["volume", "rm"]]
    assert rms == [["volume", "rm", "terrarium-venv-1"]]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_dev.py -v -k "volume or update"`
Expected: FAIL (no `volume create` / `volume rm` calls)

- [ ] **Step 3: Implement**

In `docker/terrarium-dev`, add after `venv_volume`:

```bash
image_id() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || true; }

# The venv volume must match the image it was seeded from: an older image's
# venv would carry stale luxaeterna and pins. Docker seeds an EMPTY named
# volume from the image's /work/.venv on first mount, so recreating is enough.
ensure_venv_volume() {
  local vol="$1" want have
  want="$(image_id "$image")"
  if [ -z "$want" ]; then
    docker pull "$image" >&2
    want="$(image_id "$image")"
  fi
  have="$(docker volume inspect --format '{{index .Labels "mm.terrarium-dev.image"}}' "$vol" 2>/dev/null || true)"
  if [ "$have" != "$want" ]; then
    docker volume rm -f "$vol" >/dev/null 2>&1 || true
    docker volume create --label mm.terrarium-dev.venv=1 \
      --label "mm.terrarium-dev.image=$want" "$vol" >/dev/null
  fi
}

prune_venv_volumes() {
  local want vol have
  want="$(image_id "$image")"
  for vol in $(docker volume ls -q --filter label=mm.terrarium-dev.venv=1); do
    have="$(docker volume inspect --format '{{index .Labels "mm.terrarium-dev.image"}}' "$vol" 2>/dev/null || true)"
    if [ "$have" != "$want" ]; then
      docker volume rm "$vol" >/dev/null 2>&1 || warn "venv volume $vol is in use; left in place"
    fi
  done
}
```

Note `docker volume rm -f` is logged by the fake as `["volume","rm","-f",vol]`; `c[:2]` still matches `["volume","rm"]`. The prune uses plain `docker volume rm <vol>`, which is what `test_update_pulls_then_prunes_volumes_from_other_images` asserts.

Change the `update` branch to:

```bash
  update)
    check_docker
    docker pull "$image"
    prune_venv_volumes
    ;;
```

In both the `run|smoke|shell` and `test|clean` branches, insert after `co="$(resolve_checkout)" || exit 1`:

```bash
    if [ -n "$co" ]; then ensure_venv_volume "$(venv_volume "$co")"; fi
```

- [ ] **Step 4: Run the whole launcher file**

Run: `.venv/bin/python -m pytest tests/test_terrarium_dev.py -v`
Expected: all PASS (Task 3's tests too)

- [ ] **Step 5: Commit**

```bash
git add docker/terrarium-dev tests/test_terrarium_dev.py
git commit -m "feat(docker): stamp venv volumes with their image and prune stale ones on update"
```

---

### Task 5: Dockerfile, pins, `build.sh`, and a real build plus self-check

**Files:**
- Create: `docker/pins.env`
- Create: `docker/Dockerfile`
- Create: `docker/build.sh` (executable)
- Create: `.dockerignore`
- Test: `tests/test_docker_build.py`

**Interfaces:**
- Consumes: `docker/entrypoint.sh`, `docker/selfcheck.sh`, `docker/asound.conf` (Task 2); `docker/terrarium-dev` (Tasks 3-4); `docs/upstream/arco-linux-build.patch`, `docs/upstream/arco-libraries-ubuntu.txt` (existing).
- Produces: `docker/build.sh [--tag TAG] [--dry-run]`, env overrides `TD_IMAGE_REPO` and `TD_PINS_FILE` (default `docker/pins.env`). Builds `$TD_IMAGE_REPO:$TAG` (default tag `local`) for `linux/amd64` with `--load`. `--dry-run` prints the command (shell-quoted, one line) and exits 0. Phase 2's workflow will call this script.

- [ ] **Step 1: Write `docker/pins.env`**

```bash
# Every external version the terrarium-dev image builds from. Bump one line
# per PR; docker/build.sh passes each as a --build-arg. Full SHAs only:
# fetch-by-SHA needs them.
BASE_IMAGE=ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78
ARCO_REPO=https://github.com/Musical-Mycology/arco.git
# The commit docs/upstream/arco-linux-build.patch was made against.
ARCO_SHA=c8092e292a2a75fb344ee16c4c9369010fed1ee6
O2_REPO=https://github.com/rbdannenberg/o2.git
O2_SHA=f21499e1780783a484831023b900212ea9bfb500
LUXAETERNA_REPO=https://github.com/Musical-Mycology/luxaeterna.git
LUXAETERNA_SHA=9e2eb6205d4db36f8088472c3d254eda6ae6f433
```

- [ ] **Step 2: Write the failing tests** (`tests/test_docker_build.py`)

```python
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
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_docker_build.py -v`
Expected: `test_pins_file_has_every_key_with_full_shas` PASS, the rest FAIL (build.sh missing)

- [ ] **Step 4: Implement `docker/build.sh`**

```bash
#!/usr/bin/env bash
# Build the terrarium-dev image from docker/pins.env (docker/README.md).
#   docker/build.sh                 -> ghcr.io/musical-mycology/terrarium-dev:local
#   docker/build.sh --tag sha-XXXX  -> a named tag (Phase 2 CI)
#   docker/build.sh --dry-run       -> print the build command only
# Keep this Bash 3.2 compatible.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TD_IMAGE_REPO="${TD_IMAGE_REPO:-ghcr.io/musical-mycology/terrarium-dev}"
TD_PINS_FILE="${TD_PINS_FILE:-docker/pins.env}"
die() { echo "build.sh: $*" >&2; exit 1; }

tag=local; dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tag)     [ $# -ge 2 ] || die "--tag needs a value"; tag="$2"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -f "$TD_PINS_FILE" ] || die "no pins file at $TD_PINS_FILE"
set -a
# shellcheck disable=SC1090
. "$TD_PINS_FILE"
set +a

cmd=(docker buildx build --platform linux/amd64 --load
     -f docker/Dockerfile -t "$TD_IMAGE_REPO:$tag")
for key in BASE_IMAGE ARCO_REPO ARCO_SHA O2_REPO O2_SHA LUXAETERNA_REPO LUXAETERNA_SHA; do
  [ -n "${!key:-}" ] || die "$TD_PINS_FILE is missing $key"
  cmd+=(--build-arg "$key=${!key}")
done
cmd+=(.)

if [ "$dry" -eq 1 ]; then
  printf '%q ' "${cmd[@]}"
  echo
  exit 0
fi
exec "${cmd[@]}"
```

Note the test runs with `PATH=/usr/bin:/bin` and never calls docker (dry run), so a missing docker binary cannot fail it.

- [ ] **Step 5: Run the build-script tests**

Run: `chmod +x docker/build.sh && .venv/bin/python -m pytest tests/test_docker_build.py -v`
Expected: all PASS

- [ ] **Step 6: Write `.dockerignore`**

```
.git
.venv
runs/
**/__pycache__
**/.pytest_cache
.claude/
*.pyc
```

- [ ] **Step 7: Write `docker/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
# terrarium-dev: a pre-built Terrarium dev and test box (docker/README.md).
# Build with docker/build.sh, which passes every ARG from docker/pins.env.
# Spec: docs/superpowers/specs/2026-09-29-terrarium-dev-container-design.md
ARG BASE_IMAGE

# ---- base: everything a run needs. The -dev packages from the verified
# Ubuntu 26.04 setup list (docs/MM_TERRARIUM.md, Linux / WSL host setup,
# step 1) are installed as-is: their runtime library names carry t64 and
# version suffixes, and the -dev list is the one known to resolve.
# build-essential + python3-dev: netifaces (o2litepy) builds from source.
FROM ${BASE_IMAGE} AS base
ENV DEBIAN_FRONTEND=noninteractive \
    TERM=xterm-256color \
    LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
      portaudio19-dev libavahi-client-dev libsndfile1-dev libfluidsynth-dev \
      libportmidi-dev fluid-soundfont-gm libncurses-dev libogg-dev \
      libvorbis-dev libflac-dev libopus-dev libglib2.0-dev \
      libasound2-plugins dbus avahi-daemon \
      python3 python3-venv python3-dev build-essential \
      nodejs git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---- build: o2 and the patched Arco server at their pins.
FROM base AS build
RUN apt-get update && apt-get install -y --no-install-recommends cmake \
    && rm -rf /var/lib/apt/lists/*
ARG O2_REPO
ARG O2_SHA
ARG ARCO_REPO
ARG ARCO_SHA
WORKDIR /build
RUN git init -q o2 && git -C o2 fetch -q --depth 1 "$O2_REPO" "$O2_SHA" \
    && git -C o2 checkout -q FETCH_HEAD \
    && cmake -S o2 -B o2/Release -DCMAKE_BUILD_TYPE=Release -DTESTS_BUILD=OFF \
         -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    && cmake --build o2/Release -j"$(nproc)"
RUN git init -q arco && git -C arco fetch -q --depth 1 "$ARCO_REPO" "$ARCO_SHA" \
    && git -C arco checkout -q FETCH_HEAD
COPY docs/upstream/arco-linux-build.patch docs/upstream/arco-libraries-ubuntu.txt /build/upstream/
# The Linux fixes are pending upstream with Roger Dannenberg: applied here
# only, never committed to the arco mirror.
RUN if git -C arco apply --check /build/upstream/arco-linux-build.patch; then \
      git -C arco apply /build/upstream/arco-linux-build.patch; \
    elif git -C arco apply --reverse --check /build/upstream/arco-linux-build.patch; then \
      echo "arco-linux-build.patch is already in ARCO_SHA: the fixes landed upstream. Delete the patch step here and docs/upstream/arco-linux-build.patch." >&2; exit 1; \
    else \
      echo "arco-linux-build.patch no longer applies to ARCO_SHA: rebase the patch onto the new pin." >&2; exit 1; \
    fi \
    && cp /build/upstream/arco-libraries-ubuntu.txt arco/apps/common/libraries.txt
# The SECOND configure is required: arcoserver.cmakeinclude tests USE_MIDI
# before option(USE_MIDI) defines it (docs/MM_TERRARIUM.md, setup step 6).
WORKDIR /build/arco/apps/pytest
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    && cmake -S . -B build \
    && cmake --build build -j"$(nproc)" \
    && ln -s pytestserver server \
    && test -x server

# ---- runtime: base + Arco + the venv + the snapshot + the scripts.
FROM base AS runtime
COPY --from=build /build/arco /opt/mm/arco
ENV MM_ARCO_PATH=/opt/mm/arco \
    ARCO_ROOT=/opt/mm/arco
ARG LUXAETERNA_REPO
ARG LUXAETERNA_SHA
# One venv, at /work/.venv: a mounted checkout's venv volume is seeded from
# it, and the snapshot reaches it through a symlink.
COPY requirements.txt requirements-dev.txt /opt/mm/terrarium/
RUN python3 -m venv /work/.venv \
    && /work/.venv/bin/python -m pip install --no-cache-dir -r /opt/mm/terrarium/requirements-dev.txt \
    && /work/.venv/bin/python -m pip install --no-cache-dir \
         "luxaeterna[websim] @ git+${LUXAETERNA_REPO}@${LUXAETERNA_SHA}"
COPY . /opt/mm/terrarium/
COPY docker/terrarium-dev /usr/local/bin/terrarium-dev
COPY docker/entrypoint.sh docker/selfcheck.sh /usr/local/lib/terrarium-dev/
COPY docker/asound.conf /etc/asound.conf
WORKDIR /opt/mm/terrarium
# The launcher runs as the host uid (--user), so the venv (pip installs on
# requirements drift) and the snapshot (runs/, caches) are world-writable.
RUN ln -s /work/.venv /opt/mm/terrarium/.venv \
    && mkdir -p /opt/mm/terrarium/runs \
    && /usr/local/lib/terrarium-dev/entrypoint.sh stamp-venv \
    && chmod -R a+rwX /work /opt/mm/terrarium
ENTRYPOINT ["/usr/local/lib/terrarium-dev/entrypoint.sh"]
CMD ["help"]
```

Note on `stamp-venv`: it writes `.venv/.td-req-hash` relative to the working dir `/opt/mm/terrarium`, whose `.venv` is the symlink to `/work/.venv`, so the stamp lands in the one venv.

- [ ] **Step 8: Build the image for real** (long: amd64 under emulation on an Apple Silicon Mac; run in the background and wait)

Run: `docker/build.sh 2>&1 | tee "$TMPDIR/terrarium-dev-build.log"`
Expected: ends with the image `ghcr.io/musical-mycology/terrarium-dev:local` loaded. If a step fails, fix the Dockerfile (for example an apt package name that does not resolve on 26.04, such as `libasound2-plugins`) and rebuild. Report every Dockerfile change made to get a green build.

- [ ] **Step 9: Check the launcher round-trip from the image**

Run: `docker run --rm ghcr.io/musical-mycology/terrarium-dev:local launcher | diff - docker/terrarium-dev && echo LAUNCHER_OK`
Expected: `LAUNCHER_OK`

- [ ] **Step 10: Run the hermetic self-check**

Run: `docker/terrarium-dev --tag local selfcheck 2>&1 | tail -40`
Expected: ends with `SELFCHECK_OK`. If the smoke stage fails as stage `device-sync`, retry once (a known intermittent upstream cause, docs/MM_TERRARIUM.md, *Not yet built / deferred*). If it fails in a way that looks like emulation timing (readiness or clock-sync timeouts, with pytest and node passing), do NOT loosen timeouts: report the task as DONE_WITH_CONCERNS naming the failing stage, because the authoritative check needs a native amd64 host.

- [ ] **Step 11: Check checkout mode, venv seeding and the non-root user**

Run: `docker/terrarium-dev --tag local shell -c 'id -u; test -x /work/.venv/bin/python && cat /work/.venv/.td-req-hash && ls /opt/mm/arco/apps/pytest/server'`
Expected: prints the host uid (not 0), the requirements hash, and the server path. This proves Docker seeded the pre-created, labelled volume from the image. On this Mac the launcher refuses `shell` without a host Avahi socket; run it as `TD_AVAHI_DIR=<a temp dir containing an empty file named socket> docker/terrarium-dev --tag local --headless shell -c '...'`, and state in the report that the Avahi mount was faked for this check.

- [ ] **Step 12: Run the full offline suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all PASS

- [ ] **Step 13: Commit**

```bash
git add docker/pins.env docker/Dockerfile docker/build.sh .dockerignore tests/test_docker_build.py
git commit -m "feat(docker): terrarium-dev image, pins and build script"
```

---

### Task 6: Documentation

**Files:**
- Create: `docker/README.md`
- Modify: `README.md` (add a *Dev container* quickstart section near the top, before the first run instructions)
- Modify: `docs/MM_TERRARIUM.md` (new `### Running in the container` subsection inside `## Running it`, placed immediately before `### Linux / WSL host setup`; add it to the Contents list next to *Linux / WSL host setup*)

**Interfaces:**
- Consumes: the commands, flags, image name and tags from Tasks 3-5; setup steps 1 and 9 of `docs/MM_TERRARIUM.md` for the host prerequisites.

- [ ] **Step 1: Write `docker/README.md`** with these sections, in this order:
  1. **What it is**: one paragraph. A pre-built linux/amd64 dev and test box: o2, the patched Arco server, the venv and a snapshot of mm-terrarium `main`, driven by `terrarium-dev`. Not a venue image, and nothing here runs on an ESP32.
  2. **Host setup (Docker Engine in WSL2)**:
     - enable systemd (`/etc/wsl.conf`: `[boot]` then `systemd=true`, then `wsl --shutdown` from Windows);
     - install Docker Engine inside the Ubuntu distro from Docker's official apt repository (link `https://docs.docker.com/engine/install/ubuntu/`), NOT Docker Desktop, and add yourself to the `docker` group;
     - `sudo apt install avahi-daemon && sudo systemctl enable --now avahi-daemon`;
     - for real devices, Windows 11 mirrored networking and the Hyper-V firewall rule, copied verbatim from `docs/MM_TERRARIUM.md` setup step 9 (Windows 10 cannot do this; simulated devices only).
     Label each command block with the machine it runs on (**RUN ON: WSL UBUNTU** or **RUN ON: WINDOWS (admin PowerShell)**).
  3. **Install the launcher**: the one-liner `docker run --rm ghcr.io/musical-mycology/terrarium-dev:main launcher > terrarium-dev && chmod +x terrarium-dev`, then move it onto `PATH` (`sudo mv terrarium-dev /usr/local/bin/`). From a clone, `docker/terrarium-dev` works directly.
  4. **Everyday use**: the command table from the spec (section 4), `--tag`, `--checkout`, `--headless`, `terrarium-dev use v<date>` for a pinned known-good image, and checkout mode versus snapshot mode (logs in `~/terrarium-runs`).
  5. **Networking**: what works where (native Linux and WSL2 mirrored: real devices; WSL2 NAT: simulated only, the launcher warns; macOS Docker Desktop: simulated only). One stack per host (ports 8080, 8788, 8772).
  6. **Audio**: on by default through WSLg's PulseAudio (or the native pulse socket); `--headless` to run silent; the launcher refuses rather than silently muting.
  7. **Building and pins**: `docker/build.sh` makes `:local`; `docker/pins.env` holds every version; bump one line per PR; what the two patch-step failure messages mean.
  8. **Self-check**: `terrarium-dev selfcheck`, hermetic, and what `SELFCHECK_OK` proves.

- [ ] **Step 2: Add the `README.md` quickstart** (five to eight lines): install Docker Engine in WSL (link `docker/README.md`), the launcher one-liner, `terrarium-dev run --room TEST`, `terrarium-dev test`, and "see docker/README.md for networking, audio and pins".

- [ ] **Step 3: Add `### Running in the container` to `docs/MM_TERRARIUM.md`**: a short paragraph that the pre-built image is now the recommended Linux/WSL dev path, the launcher one-liner and two example commands, a pointer to `docker/README.md`, the networking limit in one sentence (a container needs a host already on the LAN for real devices: native Linux or WSL2 mirrored), and that the native steps below remain the reference for what the image does and for building without Docker. Update the Contents line to list it.

- [ ] **Step 4: Check for em dashes and broken paths**

Run: `grep -n "—" docker/README.md README.md docs/MM_TERRARIUM.md; grep -o 'docker/[a-zA-Z.-]*' docker/README.md README.md | sort -u`
Expected: no em-dash hits introduced by this task (compare with `git diff`); every `docker/...` path listed exists on disk.

- [ ] **Step 5: Commit**

```bash
git add docker/README.md README.md docs/MM_TERRARIUM.md
git commit -m "docs: terrarium-dev container setup, usage and deep-dive section"
```

---

## Phase 1 acceptance (by hand, not a subagent task)

Run on a Windows 11 WSL2 box in mirrored mode after the image is available (built there with `docker/build.sh`, or pulled once Phase 2 publishes it):

1. `terrarium-dev test` passes.
2. `terrarium-dev run --room TEST --seconds 45` exits 0 with "room loaded: TEST".
3. The Room drone is audible without `--headless` (verifies the ALSA-to-pulse chain; fallback if not: build PortAudio's PulseAudio host API into the image).
4. A real ESP32 dev shroom discovers Arco over mDNS and joins (verifies the D-Bus Avahi mount).
5. On a machine with no repo clone: the `launcher` install, then `terrarium-dev run --room TEST --seconds 45` in snapshot mode, works.
