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
