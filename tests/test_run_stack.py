"""harness/run_stack.py: the supervisor, driven entirely against fake
children so it stays in the offline suite."""
from __future__ import annotations

import io
import signal
import time

import pytest

from control.arco_process import FakePopen
from harness import markers
from harness.run_stack import (StackConfig, _failed_marker, control_command,
                               device_command, run)


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


class _CrashingControlPopen(ScriptedPopen):
    """Models a Control that has already exited (crashed, or reaped dead)
    by the time the runner's hold loop looks at it -- the exact scenario
    the final review verified live: with a fake `control` child marked
    exited, run() still returned ok=True, stage="complete". Marking the
    child dead at construction time, rather than trying to time a crash
    mid-hold, keeps this deterministic: readiness still passes off the
    scripted stdout (readiness never inspects process state), so this only
    exercises the hold's own polling."""

    def __call__(self, command, **kwargs):
        child = super().__call__(command, **kwargs)
        if "harness.terrarium_boot" in command:
            child.returncode = 1
        return child


def test_a_crashed_child_fails_the_hold_instead_of_reporting_success(
        tmp_path):
    """Design spec section 3.4 promises --ci "exits non-zero on any unmet
    marker or non-zero child exit". Before this fix, _hold took a
    `control` ProcTee parameter and never read it, and nothing else polled
    any child's exit status during the hold, so this stayed ok=True,
    stage="complete" even with Control already dead.

    A finite, growing tick list rather than a constant clock: the old,
    unfixed _hold only ever watched clock() against the deadline, so a
    constant clock combined with a no-op sleep spun forever with this
    test's crashed-but-never-detected child -- confirmed by hand, and
    exactly the failure mode a committed regression test must not itself
    be vulnerable to. This list is generous enough that the FIXED code
    (which returns on the very first _dead_child check, before the
    deadline is even relevant) never comes close to exhausting it, while
    an unfixed _hold still terminates once the clock outruns cfg.seconds.

    Real time.sleep, not a no-op: this run has to win FOUR readiness
    races (control-ready, control-setup, device-sync, device-join) before
    it ever reaches _hold, and test_a_device_that_never_syncs_fails_
    bounded_and_names_the_defect (above) already measured that a no-op
    sleep never yields the GIL, needing a median ~170,000 busy-loop
    iterations for the reader thread to be scheduled at all -- no fixed
    tick list survives that. A real time.sleep yields every time.
    """
    popen = _CrashingControlPopen([_CONTROL_OK, _DEVICE_OK])
    cfg = StackConfig(arco_command="/bin/true", log_dir=str(tmp_path),
                      echo=False, seconds=5.0)
    ticks = iter([0.0] * 20 + [1_000_000.0 * (i + 1) for i in range(30)])
    result = run(cfg, popen=popen, clock=lambda: next(ticks),
                 sleep=time.sleep)

    assert result.ok is False
    assert result.stage == "child-exited"
    assert "control" in result.detail


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


def test_control_carries_exit_with_parent(tmp_path):
    """Symmetric with the device flag above, and for the same reason one
    layer up: terrarium_boot -- and through it Arco and the Room
    simulator -- must not outlive a SIGKILLed or OOM-killed run_stack
    un-signalled in their own session. See F5 in the final review."""
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None, getpid=lambda: 4242)

    assert "--exit-with-parent" in popen.commands[0]
    assert "4242" in popen.commands[0]


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


def test_a_marker_added_to_failure_markers_without_a_remedy_fails_loud(
        monkeypatch):
    """_failed_marker used to hand-check markers.DEVICE_JOIN_DENIED and
    markers.DEVICE_SERVICE_CONFLICT by value. A third marker landing in
    markers.FAILURE_MARKERS -- the dict that exists to be the single
    source of truth -- would have silently done nothing, regressing every
    device-join wait back to the full cfg.join_timeout: the exact bug
    class _wait_for_marker was already fixed for once on this branch, one
    level up.

    Now _failed_marker iterates markers.FAILURE_MARKERS itself, so a new
    entry with no matching message in _FAILURE_REMEDIES fails LOUD
    (KeyError) the moment a tee reports seeing it, instead of quietly
    falling through to None. monkeypatch.setitem mutates the real dict in
    place and restores it after the test, so this exercises the exact
    object _failed_marker reads."""
    monkeypatch.setitem(markers.FAILURE_MARKERS, "DEVICE_NEW_FAILURE",
                        "SOMETHING NEW WENT WRONG:")

    class _FakeTee:
        name = "ie1"

        def seen(self, marker):
            return marker == "SOMETHING NEW WENT WRONG:"

    with pytest.raises(KeyError):
        _failed_marker(_FakeTee())


def test_device_watch_list_includes_every_failure_marker():
    """The other half of the same fix: a marker only reaches
    _failed_marker's tee.seen() check at all if the device's ProcTee was
    told to watch for it at spawn time. Deriving the watch list from
    markers.FAILURE_MARKERS (see _watch_list) closes that, rather than
    leaving a second hand-maintained list that could drift from the
    first."""
    from harness.run_stack import _watch_list

    watch = _watch_list("DEVICE_")
    for marker in markers.FAILURE_MARKERS.values():
        assert marker in watch


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
    command = control_command(_cfg(tmp_path), 99)

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


def test_ci_mode_turns_echo_off_and_bounds_the_run():
    """One code path, two configurations: CI is the same machinery with the
    terminal echo off and a duration that ends the run."""
    from harness.run_stack import config_from_args, parse_args

    cfg = config_from_args(parse_args(["--ci", "--seconds", "30"]))

    assert cfg.echo is False
    assert cfg.seconds == 30.0


def test_interactive_mode_echoes_and_holds_by_default():
    from harness.run_stack import config_from_args, parse_args

    cfg = config_from_args(parse_args([]))

    assert cfg.echo is True
    assert cfg.seconds is None


def test_ci_mode_requires_a_duration():
    """An unbounded CI run is a hung CI job."""
    from harness.run_stack import config_from_args, parse_args

    cfg = config_from_args(parse_args(["--ci"]))

    assert cfg.seconds is not None


def test_the_failure_summary_names_the_stage_and_points_at_a_log(tmp_path):
    from harness.run_stack import RunResult, format_failure

    log = tmp_path / "ie1.log"
    log.write_text("first\nsecond\nthird\n")
    result = RunResult(False, "device-sync", "never synced",
                       {"ie1": str(log)})

    summary = format_failure(result)

    assert "device-sync" in summary
    assert "never synced" in summary
    assert str(log) in summary
    assert "third" in summary       # the tail is quoted, not just referenced


def test_the_failure_summary_survives_a_missing_log(tmp_path):
    """A stage can fail before its log has any content, and the summary
    must still be the thing that explains the run."""
    from harness.run_stack import RunResult, format_failure

    result = RunResult(False, "control-ready", "never came up",
                       {"control": str(tmp_path / "absent.log")})

    assert "control-ready" in format_failure(result)


def test_the_failure_summary_points_at_the_failing_device_not_the_last_one(
        tmp_path):
    """Both devices are spawned (and so both logged) before either is
    waited on, so by the time ie1 fails to sync, result.logs already
    holds ie2's path too, inserted after ie1's. The summary has to name
    ie1's log -- the one that actually failed -- not whichever log
    happens to have been inserted last."""
    from harness.run_stack import RunResult, format_failure

    ie1_log = tmp_path / "ie1.log"
    ie1_log.write_text("ie1 line\n")
    ie2_log = tmp_path / "ie2.log"
    ie2_log.write_text("ie2 line\n")
    result = RunResult(False, "device-sync", "ie1 never clock-synced.",
                       {"control": str(tmp_path / "control.log"),
                        "ie1": str(ie1_log), "ie2": str(ie2_log)})

    summary = format_failure(result)

    # Both paths are listed (every log is), but only ie1's CONTENT -- the
    # quoted tail -- may appear, because ie1 is the one that failed.
    assert "ie1 line" in summary
    assert "ie2 line" not in summary
