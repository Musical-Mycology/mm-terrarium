"""harness/host_preflight.py: the fail-fast Avahi check and the WSL NAT
warning. Every seam (platform, socket path, osrelease, env, wslinfo runner)
is injected, so nothing here touches the real host."""
import subprocess

import pytest

from harness import host_preflight as hp


def _run(out="", code=0, exc=None):
    def run(cmd, **kw):
        assert cmd == ["wslinfo", "--networking-mode"]
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, code, stdout=out)
    return run


def test_macos_skips_avahi_check(tmp_path):
    hp.check_avahi(platform="darwin", socket_path=str(tmp_path / "nope"))


def test_linux_with_socket_passes(tmp_path):
    sock = tmp_path / "socket"
    sock.write_text("")
    hp.check_avahi(platform="linux", socket_path=str(sock))


def test_linux_missing_socket_refuses_with_fix(tmp_path):
    with pytest.raises(hp.HostPreflightError) as ei:
        hp.check_avahi(platform="linux", socket_path=str(tmp_path / "nope"))
    msg = str(ei.value)
    assert "avahi-daemon is not running" in msg
    assert "sudo apt install avahi-daemon" in msg
    assert "systemctl enable --now avahi-daemon" in msg


def test_default_socket_path_is_avahi_socket():
    assert hp.AVAHI_SOCKET == "/run/avahi-daemon/socket"


def test_is_wsl_by_osrelease_or_env():
    assert hp.is_wsl(osrelease="6.1.0-microsoft-standard-WSL2", env={})
    assert hp.is_wsl(osrelease="6.1.0-generic", env={"WSL_DISTRO_NAME": "U"})
    assert not hp.is_wsl(osrelease="6.1.0-generic", env={})
    assert not hp.is_wsl(osrelease=None, env={})


def test_wsl_nat_warns_never_refuses():
    w = hp.wsl_nat_warning(wsl=True, runner=_run("nat\n"))
    assert w and "WARNING" in w and "NAT" in w
    assert "Linux / WSL host setup" in w


@pytest.mark.parametrize("out", ["mirrored\n", "bridged\n", "none\n"])
def test_wsl_other_modes_no_warning(out):
    assert hp.wsl_nat_warning(wsl=True, runner=_run(out)) is None


def test_not_wsl_never_runs_wslinfo():
    def boom(*a, **k):
        raise AssertionError("must not run")
    assert hp.wsl_nat_warning(wsl=False, runner=boom) is None


@pytest.mark.parametrize("runner", [
    _run(exc=FileNotFoundError()),
    _run(exc=subprocess.TimeoutExpired("wslinfo", 5)),
    _run(exc=OSError("x")),
    _run("nat\n", code=1),
])
def test_wslinfo_missing_or_failing_tolerated(runner):
    assert hp.wsl_nat_warning(wsl=True, runner=runner) is None


def test_run_preflight_prints_warning_and_returns(tmp_path, capsys):
    sock = tmp_path / "s"
    sock.write_text("")
    env = {}
    hp.run_preflight(platform="linux", socket_path=str(sock), wsl=True,
                     runner=_run("nat"), env=env)
    assert "WARNING" in capsys.readouterr().err
    assert env.get(hp.DONE_ENV) == "1"


def test_run_preflight_exits_1_when_avahi_missing(tmp_path, capsys):
    with pytest.raises(SystemExit) as ei:
        hp.run_preflight(platform="linux", socket_path=str(tmp_path / "n"),
                         wsl=False, env={})
    assert ei.value.code == 1
    assert "avahi-daemon is not running" in capsys.readouterr().err


def test_run_preflight_skipped_when_parent_already_did_it(tmp_path):
    hp.run_preflight(platform="linux", socket_path=str(tmp_path / "n"),
                     wsl=False, env={hp.DONE_ENV: "1"})


def test_run_stack_main_refuses_before_spawning(monkeypatch, tmp_path, capsys):
    """run_stack.main() runs the preflight before run() spawns anything."""
    import sys
    import harness.run_stack as rs
    monkeypatch.delenv(hp.DONE_ENV, raising=False)
    real = hp.run_preflight
    monkeypatch.setattr(rs, "run_preflight", lambda: real(
        platform="linux", socket_path=str(tmp_path / "nope"), wsl=False))
    monkeypatch.setattr(rs, "ensure_o2litepy", lambda: True)
    monkeypatch.setattr(
        rs, "run", lambda cfg: pytest.fail("spawned despite no avahi"))
    monkeypatch.setattr(
        sys, "argv", ["run_stack.py", "--log-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as ei:
        rs.main()
    assert ei.value.code == 1
    assert "avahi-daemon is not running" in capsys.readouterr().err


def test_terrarium_boot_main_calls_preflight(monkeypatch):
    import sys
    import harness.terrarium_boot as tb
    calls = []
    monkeypatch.setattr(tb, "run_preflight", lambda: calls.append(1))
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py", "--list-bits"])
    with pytest.raises(SystemExit):
        tb.main()
    assert calls == []  # --list-bits needs no Arco
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py", "--no-bit"])
    with pytest.raises(SystemExit):
        tb.main()  # --no-bit with no console exits, but after the preflight
    assert calls == [1]
