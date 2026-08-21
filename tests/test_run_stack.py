"""harness/run_stack.py: the supervisor, driven entirely against fake
children so it stays in the offline suite."""
from __future__ import annotations

import io
import signal
import time

import pytest

from control.arco_process import FakePopen
from harness import markers
from harness.run_stack import (StackConfig, _failed_marker, _hold, control_command,
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
    command = device_command(_cfg(tmp_path, node="TEST_PLAYER_NODE"), 1, 99)

    assert "--join-retry" in command
    assert "--samples-out" in command
    assert "--control-horizon" in command
    assert "ie1" in command
    assert "TEST_PLAYER_NODE" in command


def test_device_command_uses_the_configs_node_verbatim(tmp_path):
    """Node derivation from the Bit manifest happens once, in
    config_from_args (BitRegistry.resolve_config(...).join_node()) -- by
    the time device_command runs, cfg.node is already the resolved value,
    and this function just forwards it."""
    command = device_command(_cfg(tmp_path, node="SOME_OTHER_NODE"), 1, 99)

    assert command[command.index("--node") + 1] == "SOME_OTHER_NODE"


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


def test_the_setup_window_clears_a_measured_device_cold_start():
    """A live run on 2026-08-14 measured device cold start at about 22s:
    Control connected at O2time 7.806 and the device clock-synced at 30.04.
    run_stack spawns devices only AFTER Control reports SETUP, so that whole
    cold start burns the window. At the old 20s default the window closed
    first and the device was refused -- `player` is a scored role, and
    RegistrationState.join() refuses scored roles once RUNNING.

    Asserted on both declaration sites, because the dataclass default and
    the argparse default are separate and only the argparse one reaches a
    CLI run."""
    from harness.run_stack import StackConfig, config_from_args, parse_args

    measured_cold_start = 22.0

    assert StackConfig(log_dir="x").setup_seconds > measured_cold_start
    assert config_from_args(parse_args([])).setup_seconds > measured_cold_start


def test_control_command_omits_console_port_by_default():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x")
    assert "--console-port" not in control_command(cfg, ppid=1)


def test_control_command_passes_console_port_when_set():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x", console_port=8772)
    cmd = control_command(cfg, ppid=1)
    assert "--console-port" in cmd
    assert cmd[cmd.index("--console-port") + 1] == "8772"


def test_console_port_defaults_to_none():
    from harness.run_stack import StackConfig
    assert StackConfig(log_dir="/tmp/x").console_port is None


def test_control_command_defaults_room_type_to_test():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x")
    cmd = control_command(cfg, ppid=1)
    assert cmd[cmd.index("--room-type") + 1] == "TEST"


def test_control_command_passes_room_type_when_set():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x", room_type="DEMO")
    cmd = control_command(cfg, ppid=1)
    assert cmd[cmd.index("--room-type") + 1] == "DEMO"


def test_config_from_args_forwards_room_type():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--room-type", "DEMO"])
    assert config_from_args(args).room_type == "DEMO"


def test_control_command_defaults_bit_to_test_bit():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x")
    cmd = control_command(cfg, ppid=1)
    assert cmd[cmd.index("--bit") + 1] == "TestBit"


def test_control_command_passes_bit_when_set():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x", bit="MetronomeBit")
    cmd = control_command(cfg, ppid=1)
    assert cmd[cmd.index("--bit") + 1] == "MetronomeBit"


def test_config_from_args_forwards_bit():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--bit", "MetronomeBit"])
    assert config_from_args(args).bit == "MetronomeBit"


def test_config_from_args_defaults_bit_to_test_bit():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args([])
    assert config_from_args(args).bit == "TestBit"


def test_config_from_args_derives_node_from_test_bit_manifest():
    """No --node and the default Bit (TestBit): the node comes from
    bits/test/bit.toml's launch.default_join_role ("player") resolved
    against launch.nodes, via BitConfig.join_node()."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args([])
    assert config_from_args(args).node == "TEST_PLAYER_NODE"


def test_config_from_args_derives_node_from_metronome_bit_manifest():
    """bits/metronome/bit.toml's launch.nodes maps player -> METRO_PLAYER_NODE
    and default_join_role is "player", so join_node() resolves it without
    any --node."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--bit", "MetronomeBit"])
    assert config_from_args(args).node == "METRO_PLAYER_NODE"


def test_config_from_args_forwards_node():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--node", "SOME_OTHER_NODE"])
    assert config_from_args(args).node == "SOME_OTHER_NODE"


def test_config_from_args_devices_defaults_from_metronome_bit_manifest():
    """bits/metronome/bit.toml's launch.default_devices is 2."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--bit", "MetronomeBit"])
    assert config_from_args(args).devices == 2


def test_config_from_args_devices_defaults_from_test_bit_manifest():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args([])
    assert config_from_args(args).devices == 1


def test_config_from_args_forwards_explicit_devices_over_the_manifest():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--bit", "MetronomeBit", "--devices", "5"])
    assert config_from_args(args).devices == 5


def test_config_from_args_room_type_defaults_from_metronome_bit_manifest():
    """bits/metronome/bit.toml's launch.default_room_type is DEMO."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--bit", "MetronomeBit"])
    assert config_from_args(args).room_type == "DEMO"


def test_config_from_args_forwards_explicit_room_type_over_the_manifest():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--bit", "MetronomeBit", "--room-type", "TEST"])
    assert config_from_args(args).room_type == "TEST"


def test_ci_bound_uses_the_forwarded_setup_seconds_when_it_exceeds_the_manifest():
    """--ci with no --seconds and no --setup-seconds: --setup-seconds keeps
    its own 90s default (the controller ruling -- it is forwarded to
    terrarium_boot unchanged regardless of the manifest), and that is what
    actually governs how long Control holds SETUP. bits/test/bit.toml's
    launch.setup_seconds is 0, so deriving the bound from the manifest
    alone (0+45+15=60) would undercut the real 90s hold -- the bound must
    be max(0, 90) + 45 + 15 = 150."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--ci"])
    assert config_from_args(args).seconds == 90.0 + 45.0 + 15.0


def test_ci_bound_uses_an_explicit_setup_seconds_when_it_exceeds_the_manifest():
    """An explicit --setup-seconds still wins the max() over the
    manifest's own (here larger) setup_seconds is NOT the point here --
    MetronomeBit's manifest setup_seconds is 20, so an explicit
    --setup-seconds 5 is smaller than the manifest and must NOT shrink the
    bound below what the manifest itself needs: max(20, 5) + 45 + 15 = 80."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--ci", "--bit", "MetronomeBit", "--setup-seconds", "5"])
    assert config_from_args(args).seconds == 20.0 + 45.0 + 15.0


def test_ci_bound_uses_the_larger_of_manifest_and_forwarded_setup_seconds():
    """MetronomeBit's manifest setup_seconds (20) is smaller than the
    forwarded --setup-seconds default (90), so the bound must use 90:
    max(20, 90) + 45 + 15 = 150."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--ci", "--bit", "MetronomeBit"])
    assert config_from_args(args).seconds == 90.0 + 45.0 + 15.0


def test_ci_bound_falls_back_to_45s_when_the_manifest_has_no_expected_run_seconds():
    """bits/test/bit.toml sets no launch.expected_run_seconds, so the bound
    falls back to the historical 45s default; setup contributes the
    forwarded --setup-seconds default (90, larger than the manifest's 0),
    plus the 15s grace: 90+45+15=150."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--ci"])
    assert config_from_args(args).seconds == 90.0 + 45.0 + 15.0


def test_ci_bound_with_an_explicit_setup_seconds_that_exceeds_the_manifest():
    """An explicit --setup-seconds larger than TestBit's manifest
    setup_seconds (0): max(0, 5) + 45 + 15 = 65."""
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--ci", "--setup-seconds", "5"])
    assert config_from_args(args).seconds == 5.0 + 45.0 + 15.0


def test_ci_bound_is_overridden_by_an_explicit_seconds():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--ci", "--bit", "MetronomeBit", "--seconds", "5"])
    assert config_from_args(args).seconds == 5.0


def test_an_unresolvable_node_is_a_config_error_before_anything_spawns():
    """A Bit with no launch.nodes and no --node has nothing for a spawned
    device to join -- that has to fail loud before any child is spawned,
    not surface as a device-join timeout later."""
    from harness.run_stack import config_from_args, parse_args
    from control.bit_config import BitConfig, BitIdentity, ConsoleBlock, LaunchConfig, StartCondition

    class _NodelessRegistry:
        packages = {"NodelessBit": object()}

        def resolve_config(self, name, overrides=None):
            return BitConfig(
                identity=BitIdentity(name="NodelessBit"),
                launch=LaunchConfig(nodes=(), default_join_role=""),
                start=StartCondition(), console=ConsoleBlock())

    args = parse_args(["--bit", "NodelessBit"])
    with pytest.raises(SystemExit) as exc_info:
        config_from_args(args, registry=_NodelessRegistry())
    assert exc_info.value.code != 0


def test_an_unknown_bit_is_a_config_error_before_anything_spawns():
    from harness.run_stack import config_from_args, parse_args
    args = parse_args(["--bit", "NoSuchBit"])
    with pytest.raises(SystemExit) as exc_info:
        config_from_args(args)
    assert exc_info.value.code != 0


def test_list_bits_prints_every_discovered_bit_and_exits_zero(capsys):
    from harness.run_stack import main
    import sys as _sys

    argv = ["run_stack.py", "--list-bits"]
    old_argv = _sys.argv
    _sys.argv = argv
    try:
        with pytest.raises(SystemExit) as exc_info:
            main()
    finally:
        _sys.argv = old_argv

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "TestBit" in out
    assert "MetronomeBit" in out


_CONTROL_OK_WITH_URLS = (
    f"{markers.CONTROL_TRANSPORT_READY} 'arco'\n"
    f"{markers.BROWSE_URL} Terrarium Console at http://127.0.0.1:8901/\n"
    f"{markers.BROWSE_URL} Watch the Room at http://127.0.0.1:8902/\n"
    f"{markers.CONTROL_SETUP_HOLD} for 20s\n")
_DEVICE_OK_WITH_URL = (
    f"{markers.BROWSE_URL} Watch the Shroom at http://127.0.0.1:8903/\n"
    f"{markers.DEVICE_CLOCK_SYNCED} 12.345\n"
    f"{markers.DEVICE_ROLE_GRANTED} 1 join(s)\n")


def test_browse_urls_are_collected_from_every_child(tmp_path):
    """The Console URL and the Room fixture URLs arrive on Control's own
    stream (the Room simulator inherits terrarium_boot's stdout); each
    device canvas URL arrives on that device's stream. All of them land in
    the result, so even a non---open run's summary can print them."""
    popen = ScriptedPopen([_CONTROL_OK_WITH_URLS, _DEVICE_OK_WITH_URL])
    result = run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert result.ok is True
    assert result.urls == ["http://127.0.0.1:8901/",
                           "http://127.0.0.1:8902/",
                           "http://127.0.0.1:8903/"]


def test_open_urls_opens_each_url_exactly_once(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK_WITH_URLS, _DEVICE_OK_WITH_URL])
    opened = []
    result = run(_cfg(tmp_path, open_urls=True), popen=popen,
                 sleep=lambda _s: None, opener=opened.append)

    assert result.ok is True
    assert opened == ["http://127.0.0.1:8901/",
                      "http://127.0.0.1:8902/",
                      "http://127.0.0.1:8903/"]


def test_without_open_urls_no_tab_is_opened(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK_WITH_URLS, _DEVICE_OK_WITH_URL])
    opened = []
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None,
        opener=opened.append)

    assert opened == []


def test_a_marker_line_without_a_url_is_ignored_not_crashed(tmp_path):
    """A future emit site that prints the marker but garbles the URL must
    degrade to a missing tab, never take the whole stack down."""
    control_script = (f"{markers.CONTROL_TRANSPORT_READY} 'arco'\n"
                      f"{markers.BROWSE_URL} (port not known yet)\n"
                      f"{markers.CONTROL_SETUP_HOLD} for 20s\n")
    popen = ScriptedPopen([control_script, _DEVICE_OK])
    result = run(_cfg(tmp_path, open_urls=True), popen=popen,
                 sleep=lambda _s: None, opener=lambda _u: None)

    assert result.ok is True
    assert result.urls == []


def test_open_flag_sets_open_urls_and_implies_a_console():
    """--open with no --console-port asks for port 0: ConsoleServer binds
    an ephemeral port and terrarium_boot prints the real URL, so the
    implied console can never collide with anything."""
    from harness.run_stack import config_from_args, parse_args

    cfg = config_from_args(parse_args(["--open"]))

    assert cfg.open_urls is True
    assert cfg.console_port == 0


def test_open_flag_respects_an_explicit_console_port():
    from harness.run_stack import config_from_args, parse_args

    cfg = config_from_args(parse_args(["--open", "--console-port", "8772"]))

    assert cfg.console_port == 8772


def test_ci_refuses_open():
    """A headless CI run opening browser tabs is never right."""
    from harness.run_stack import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--ci", "--open"])


def test_ci_refuses_serve():
    """Mirrors --ci --open: a headless CI run has nothing to serve for."""
    from harness.run_stack import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--ci", "--serve"])


def test_serve_is_forwarded_when_console_requested_without_ci():
    """A console requested outside --ci implies --serve on the child, even
    without the caller passing --serve explicitly -- an operator who asked
    for a console has nowhere else to look at it from."""
    from harness.run_stack import config_from_args, control_command, parse_args

    cfg = config_from_args(parse_args(["--console-port", "8772"]))

    assert "--serve" in control_command(cfg, ppid=1)


def test_serve_is_not_forwarded_under_ci_even_with_a_console():
    from harness.run_stack import config_from_args, control_command, parse_args

    cfg = config_from_args(parse_args(["--ci", "--console-port", "8772"]))

    assert "--serve" not in control_command(cfg, ppid=1)


def test_serve_flag_is_forwarded_explicitly_without_a_console():
    from harness.run_stack import config_from_args, control_command, parse_args

    cfg = config_from_args(parse_args(["--serve"]))

    assert "--serve" in control_command(cfg, ppid=1)


def test_serve_is_omitted_by_default():
    from harness.run_stack import config_from_args, control_command, parse_args

    cfg = config_from_args(parse_args([]))

    assert "--serve" not in control_command(cfg, ppid=1)


def test_arco_path_fallback_is_a_noop_when_o2litepy_imports():
    from harness.run_stack import _ensure_o2litepy

    syspath, env = [], {}
    ok = _ensure_o2litepy(importer=lambda: None, syspath=syspath, environ=env)

    assert ok is True
    assert syspath == []
    assert env == {}


def test_arco_path_fallback_appends_the_arco_checkout_and_retries():
    """The single-command goal: a bare `run_stack --open` with no
    PYTHONPATH set falls back to the same hardcoded arco checkout
    DEFAULT_ARCO_COMMAND already lives in, for this process (sys.path)
    AND for the children it spawns (PYTHONPATH)."""
    from harness.run_stack import ARCO_PYTHONPATH, _ensure_o2litepy

    calls = []

    def importer():
        calls.append(True)
        if len(calls) == 1:
            raise ImportError("no o2litepy")

    syspath, env = [], {}
    ok = _ensure_o2litepy(importer=importer, syspath=syspath, environ=env)

    assert ok is True
    assert ARCO_PYTHONPATH in syspath
    assert env["PYTHONPATH"] == ARCO_PYTHONPATH


def test_arco_path_fallback_preserves_an_existing_pythonpath():
    from harness.run_stack import ARCO_PYTHONPATH, _ensure_o2litepy

    calls = []

    def importer():
        calls.append(True)
        if len(calls) == 1:
            raise ImportError("no o2litepy")

    env = {"PYTHONPATH": "/somewhere/else"}
    ok = _ensure_o2litepy(importer=importer, syspath=[], environ=env)

    assert ok is True
    assert env["PYTHONPATH"] == f"/somewhere/else:{ARCO_PYTHONPATH}"


def test_arco_path_fallback_reports_failure_when_the_checkout_lacks_it():
    from harness.run_stack import _ensure_o2litepy

    def importer():
        raise ImportError("no o2litepy anywhere")

    ok = _ensure_o2litepy(importer=importer, syspath=[], environ={})

    assert ok is False


class _CompletingControlPopen(ScriptedPopen):
    """Models a Control whose Bit finished on its own: the child prints the
    completion marker and exits ZERO. Same construction-time-death shape as
    _CrashingControlPopen above, and for the same determinism reason."""

    def __call__(self, command, **kwargs):
        child = super().__call__(command, **kwargs)
        if "harness.terrarium_boot" in command:
            child.returncode = 0
        return child


def test_a_self_completed_bit_ends_the_hold_as_success(tmp_path):
    """A Bit that signals done (MetronomeBit after its 4th judgment) makes
    terrarium_boot print CONTROL_BIT_COMPLETED and exit 0. That is the run
    ending on its own, not a crash -- observed live 2026-08-21 (runs/
    20260821-094252), where a fully successful interactive run exited 1."""
    script = _CONTROL_OK + f"{markers.CONTROL_BIT_COMPLETED}\n"
    popen = _CompletingControlPopen([script, _DEVICE_OK])
    cfg = StackConfig(arco_command="/bin/true", log_dir=str(tmp_path),
                      echo=False, seconds=5.0)
    ticks = iter([0.0] * 20 + [1_000_000.0 * (i + 1) for i in range(30)])
    result = run(cfg, popen=popen, clock=lambda: next(ticks),
                 sleep=time.sleep)

    assert result.ok is True
    assert result.stage == "bit-completed"


def test_a_zero_exit_without_the_completion_marker_still_fails(tmp_path):
    """Exit 0 alone is not proof the run ended on its own -- a markerless
    zero exit (a child that died quietly) keeps the failure diagnosis."""
    popen = _CompletingControlPopen([_CONTROL_OK, _DEVICE_OK])
    cfg = StackConfig(arco_command="/bin/true", log_dir=str(tmp_path),
                      echo=False, seconds=5.0)
    ticks = iter([0.0] * 20 + [1_000_000.0 * (i + 1) for i in range(30)])
    result = run(cfg, popen=popen, clock=lambda: next(ticks),
                 sleep=time.sleep)

    assert result.ok is False
    assert result.stage == "child-exited"


class _Proc:
    """A child whose poll() answer is fixed at construction."""

    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


def _serve_cfg(tmp_path, **kwargs):
    return StackConfig(arco_command="/bin/true", log_dir=str(tmp_path),
                       echo=False, seconds=1.0, **kwargs)


def test_serve_mode_tolerates_a_clean_device_exit_during_the_hold(tmp_path):
    """Live 2026-08-21: after a Console abort the released o2_shroom exits
    with code 0 by design, and _hold read that as `child-exited`, SIGTERMed
    Control mid-serve and took Arco down with it. In serve mode a clean
    device exit is the round ending, not the stack failing."""
    children = {"control": _Proc(None), "ie1": _Proc(0)}
    ticks = iter([0.0, 0.0, 10.0])
    dead = _hold(_serve_cfg(tmp_path, serve=True), children,
                 clock=lambda: next(ticks), sleep=lambda _s: None)
    assert dead is None


def test_serve_mode_still_fails_loud_on_control_or_nonzero_device_exit(tmp_path):
    ticks = iter([0.0] * 10)
    dead = _hold(_serve_cfg(tmp_path, serve=True),
                 {"control": _Proc(0), "ie1": _Proc(None)},
                 clock=lambda: next(ticks), sleep=lambda _s: None)
    assert dead == ("control", 0)
    ticks = iter([0.0] * 10)
    dead = _hold(_serve_cfg(tmp_path, serve=True),
                 {"control": _Proc(None), "ie1": _Proc(1)},
                 clock=lambda: next(ticks), sleep=lambda _s: None)
    assert dead == ("ie1", 1)


def test_one_shot_mode_still_fails_on_a_clean_device_exit(tmp_path):
    ticks = iter([0.0] * 10)
    dead = _hold(_serve_cfg(tmp_path, serve=False),
                 {"control": _Proc(None), "ie1": _Proc(0)},
                 clock=lambda: next(ticks), sleep=lambda _s: None)
    assert dead == ("ie1", 0)


def test_flutter_sim_is_spawned_after_control_with_serve_args(tmp_path):
    popen = ScriptedPopen(
        [_CONTROL_OK,
         f"{markers.BROWSE_URL} http://127.0.0.1:8780/?dev=ie1\n"])
    cfg = _cfg(tmp_path, devices=0, flutter_sim="/repo/tuneshroom", flutter_devices=1)
    result = run(cfg, popen=popen, sleep=lambda _s: None)
    assert result.ok, result.detail
    assert popen.commands[1] == ["/repo/tuneshroom/tool/sim", "serve", "--devices", "1",
                                 "--link", "ws://127.0.0.1:8771/ws", "--no-open"]
    assert "http://127.0.0.1:8780/?dev=ie1" in result.urls


def test_no_flutter_flags_spawns_nothing_extra(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK])
    run(_cfg(tmp_path, devices=0), popen=popen, sleep=lambda _s: None)
    assert len(popen.commands) == 1
