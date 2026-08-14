"""harness/run_stack.py: the supervisor, driven entirely against fake
children so it stays in the offline suite."""
from __future__ import annotations

import io
import signal
import time

from control.arco_process import FakePopen
from harness import markers
from harness.run_stack import (StackConfig, control_command, device_command,
                               run)


class ScriptedPopen(FakePopen):
    """A FakePopen whose children also SPEAK. Boundary rule 5 again: the
    real Popen we pass stdout=PIPE hands back an object with a readable
    .stdout, and a double without one would let every test pass while the
    runner's entire readiness path went unexercised."""

    def __init__(self, scripts, *, ignores=()):
        super().__init__(ignores=ignores)
        self._scripts = list(scripts)
        self.children: list[FakePopen] = []

    def __call__(self, command, **kwargs):
        child = FakePopen(ignores=self._ignores)
        child(command, **kwargs)
        text = self._scripts.pop(0) if self._scripts else ""
        child.stdout = io.StringIO(text)
        self.commands.append(command)
        self.kwargs = kwargs
        self.children.append(child)
        return child


def _cfg(tmp_path, **kwargs):
    return StackConfig(arco_command="/bin/true", log_dir=str(tmp_path),
                       echo=False, seconds=0.0, **kwargs)


_CONTROL_OK = (f"{markers.CONTROL_TRANSPORT_READY} 'arco'\n"
               f"{markers.CONTROL_SETUP_HOLD} for 20s\n")
_DEVICE_OK = (f"{markers.DEVICE_CLOCK_SYNCED} 12.345\n"
              f"{markers.DEVICE_ROLE_GRANTED} 1 join(s)\n")


def test_a_clean_run_reports_success(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    result = run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert result.ok is True
    assert result.stage == "complete"


def test_control_is_spawned_before_any_device(tmp_path):
    """A device that joins before registration opens is refused outright:
    TestBit's `player` is a SCORED role and RegistrationState.join()
    refuses those once RUNNING."""
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert "harness.terrarium_boot" in popen.commands[0]
    assert "harness.o2_shroom" in popen.commands[1]


def test_devices_are_stopped_before_control(tmp_path):
    """The ordering this whole slice exists for, at the runner's layer:
    every o2lite client stops before the process that owns the hub."""
    order = []

    class _RecordingPopen(ScriptedPopen):
        def __call__(self, command, **kwargs):
            child = super().__call__(command, **kwargs)
            label = "control" if "harness.terrarium_boot" in command else "device"
            original = child.send_signal

            def send_signal(sig, _label=label, _orig=original):
                if child.returncode is None:
                    order.append(_label)
                _orig(sig)

            child.send_signal = send_signal
            return child

    popen = _RecordingPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert order == ["device", "control"]


def test_children_are_spawned_in_their_own_session(tmp_path):
    """Without start_new_session, Ctrl-C in an interactive terminal is
    delivered to the whole foreground process group at once, every child
    gets SIGINT simultaneously, and the runner has no ordering left to
    enforce."""
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    for child in popen.children:
        assert child.kwargs["start_new_session"] is True


def test_a_device_carries_exit_with_parent(tmp_path):
    """A SIGKILLed runner would otherwise leave ie1 claimed on the hub, and
    the next run's own ie1 is refused by O2 silently."""
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None, getpid=lambda: 4242)

    assert "--exit-with-parent" in popen.commands[1]
    assert "4242" in popen.commands[1]


def test_control_never_becoming_ready_fails_bounded(tmp_path):
    popen = ScriptedPopen(["nothing useful\n"])
    ticks = iter([0.0] + [1000.0] * 20)
    result = run(_cfg(tmp_path), popen=popen, clock=lambda: next(ticks),
                 sleep=lambda _s: None)

    assert result.ok is False
    assert result.stage == "control-ready"


def test_a_device_that_never_syncs_fails_bounded_and_names_the_defect(tmp_path):
    """The documented headless clock-sync defect. The runner cannot fix it;
    it must not hang on it, and it must say which of the two documented
    halves it hit.

    This test needs Control's stage to succeed for real (ProcTee's reader
    thread has to actually win a race against this test's busy-poll loop)
    and the device stage to fail via the scripted clock, in the same run.
    A no-op sleep defeats the first half: measured directly against
    ProcTee.wait_for, a `sleep=lambda _s: None` busy loop needs a MEDIAN of
    ~170,000 and a MAX of ~347,000 clock() calls across 300 trials before
    the reader thread gets scheduled at all, because nothing in the loop
    ever yields the GIL. No fixed list of scripted ticks can absorb that,
    and short lists (the 3 zeros first tried here) fail outright -- the
    control-ready stage loses the race almost every time and the test
    fails with stage == 'control-ready' instead of 'device-sync'. A real
    `time.sleep` yields the GIL on every poll, and the same measurement
    shows the reader thread then wins within exactly 2 calls, every time
    (300/300 trials). The 10 zeros of headroom below is 5x that, verified
    empirically at 300/300 successful runs; the growing tail after it
    means that however many of the 10 get consumed, the next few ticks
    still blow well past any of this run's timeouts rather than sitting
    flat at a value the deadline could out-crawl.
    """
    popen = ScriptedPopen([_CONTROL_OK, "watch at http://x\n"])
    ticks = iter([0.0] * 10 + [1_000_000.0 * (i + 1) for i in range(30)])
    result = run(_cfg(tmp_path), popen=popen, clock=lambda: next(ticks),
                 sleep=time.sleep)

    assert result.ok is False
    assert result.stage == "device-sync"
    assert "o2debug.log" in result.detail


def test_a_denied_join_fails_immediately(tmp_path):
    """Never recovers, so waiting out the timeout is pure lost time."""
    popen = ScriptedPopen([_CONTROL_OK,
                           f"{markers.DEVICE_CLOCK_SYNCED} 1.0\n"
                           f"{markers.DEVICE_JOIN_DENIED} scored role closed\n"])
    result = run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert result.ok is False
    assert result.stage == "device-join"


def test_a_service_conflict_fails_immediately(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK,
                           f"{markers.DEVICE_CLOCK_SYNCED} 1.0\n"
                           f"{markers.DEVICE_SERVICE_CONFLICT} 'ie1' is taken\n"])
    result = run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert result.ok is False
    assert result.stage == "device-join"


def test_teardown_still_runs_when_a_stage_fails(tmp_path):
    popen = ScriptedPopen(["nothing useful\n"])
    ticks = iter([0.0] + [1000.0] * 20)
    run(_cfg(tmp_path), popen=popen, clock=lambda: next(ticks),
        sleep=lambda _s: None)

    assert popen.children[0].signals == [signal.SIGTERM]


def test_control_command_carries_the_flags_a_headless_run_needs(tmp_path):
    """--arco-pty because a piped-stdio Popen cannot open /dev/tty;
    --arco-settle-seconds and --arco-ready-timeout because the FIRST probe
    against a cold Arco can take ~18s and a failed probe sends a SECOND
    /host/clear that can leave arco.output None."""
    command = control_command(_cfg(tmp_path))

    assert "--transport" in command and "o2lite" in command
    assert "--arco-pty" in command
    assert "--arco-log" in command
    assert "--arco-settle-seconds" in command
    assert "--arco-ready-timeout" in command
    assert "--setup-seconds" in command
    assert "-u" in command


def test_device_command_carries_join_retry_and_samples_out(tmp_path):
    """--join-retry because a join sent before Control is listening is
    dropped with no queue behind it."""
    command = device_command(_cfg(tmp_path), 1, 99)

    assert "--join-retry" in command
    assert "--samples-out" in command
    assert "--control-horizon" in command
    assert "ie1" in command
    assert "TEST_PLAYER_NODE" in command


def test_more_than_one_device_gets_distinct_dev_names(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK, _DEVICE_OK])
    run(_cfg(tmp_path, devices=2), popen=popen, sleep=lambda _s: None)

    assert "ie1" in popen.commands[1]
    assert "ie2" in popen.commands[2]
