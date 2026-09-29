import signal

import pytest

from control.arco_process import ArcoProcess, ArcoReadyTimeout, FakePopen


def make_clock():
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    return clock, sleep


def test_start_launches_the_configured_command():
    popen = FakePopen()
    process = ArcoProcess(["arco-server", "--flag"], popen=popen)
    process.start()
    assert popen.commands == [["arco-server", "--flag"]]


def test_wait_ready_returns_once_probe_succeeds():
    clock, sleep = make_clock()
    calls = []

    def probe(_remaining):
        calls.append(1)
        return len(calls) >= 3   # ready on the third check

    process = ArcoProcess(["arco-server"], popen=FakePopen(), probe=probe,
                          clock=clock, sleep=sleep)
    process.start()
    process.wait_ready(timeout=5.0)   # must not raise
    assert len(calls) == 3


def test_wait_ready_raises_when_probe_never_succeeds():
    clock, sleep = make_clock()
    process = ArcoProcess(["arco-server"], popen=FakePopen(),
                          probe=lambda _r: False, clock=clock, sleep=sleep)
    process.start()
    with pytest.raises(ArcoReadyTimeout):
        process.wait_ready(timeout=1.0)


def test_shutdown_sends_sigterm_and_reaps():
    popen = FakePopen()
    process = ArcoProcess(["arco-server"], popen=popen)
    process.start()

    process.shutdown()

    assert popen.signals == [signal.SIGTERM]
    assert popen.returncode is not None      # signalled AND reaped


def test_shutdown_before_start_is_a_noop():
    process = ArcoProcess(["arco-server"], popen=FakePopen())
    process.shutdown()   # must not raise


def test_shutdown_twice_only_signals_once():
    popen = FakePopen()
    process = ArcoProcess(["arco-server"], popen=popen)
    process.start()
    process.shutdown()
    process.shutdown()
    assert popen.signals == [signal.SIGTERM]


def test_poll_reports_a_dead_subprocess():
    class DeadPopen:
        def poll(self):
            return 1

    proc = ArcoProcess(["fake"], popen=lambda *a, **k: DeadPopen())
    proc.start()
    assert proc.poll() == 1


def test_poll_reports_none_before_start():
    process = ArcoProcess(["arco-server"], popen=FakePopen())
    assert process.poll() is None


def test_fake_popen_poll_is_none_until_the_child_exits():
    """Boundary rule 5: Popen.poll() returns None while the child runs and
    its exit code after. A double whose poll() always returned a code would
    let stop_process's wait loop terminate instantly in every test, so the
    bounded-wait path would never actually be exercised."""
    popen = FakePopen()
    process = popen(["cmd"])

    assert process.poll() is None
    process.send_signal(signal.SIGTERM)
    assert process.poll() is not None


def test_fake_popen_wait_raises_timeout_expired_while_the_child_lives():
    """Popen.wait(timeout=...) raises TimeoutExpired rather than returning.
    A double that returned instead would let a caller believe it had reaped
    a process that never died."""
    import subprocess

    popen = FakePopen()
    process = popen(["cmd"])

    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=0.01)


def test_fake_popen_send_signal_after_exit_is_a_no_op():
    """Popen.send_signal checks returncode first and does nothing if the
    child is gone."""
    popen = FakePopen()
    process = popen(["cmd"])
    process.send_signal(signal.SIGTERM)
    popen.signals.clear()

    process.send_signal(signal.SIGTERM)
    assert popen.signals == []


def test_fake_popen_records_the_keyword_arguments_it_was_given():
    """harness/run_stack.py spawns with stdout=, stderr= and
    start_new_session=True, and the ordering tests assert on them."""
    popen = FakePopen()
    popen(["cmd"], start_new_session=True)

    assert popen.kwargs["start_new_session"] is True


def test_wait_ready_reports_a_child_that_exited_before_the_first_probe():
    """A pty_popen child whose exec fails does os._exit(127) and used to be
    invisible: the probe just never succeeded and the caller saw a generic
    ArcoReadyTimeout after the full 15 s, with a 0-byte arco.log. The
    process is now polled before every probe so an already-dead child
    fails fast and names its exit status and command."""
    from control.arco_process import ArcoExited

    clock, sleep = make_clock()
    popen = FakePopen()
    probes = []
    process = ArcoProcess(["/no/such/arco"], popen=popen,
                          probe=lambda _r: probes.append(1) or False,
                          clock=clock, sleep=sleep)
    process.start()
    popen.returncode = 127

    with pytest.raises(ArcoExited) as info:
        process.wait_ready(timeout=15.0)

    assert probes == []                      # failed before probing at all
    assert "127" in str(info.value)
    assert "/no/such/arco" in str(info.value)
    assert clock() < 15.0                    # did not burn the whole budget


def test_wait_ready_reports_a_child_that_died_between_probes():
    from control.arco_process import ArcoExited

    clock, sleep = make_clock()
    popen = FakePopen()
    probes = []

    def probe(_remaining):
        probes.append(1)
        if len(probes) == 2:
            popen.returncode = 1             # dies after the second probe
        return False

    process = ArcoProcess(["arco-server"], popen=popen, probe=probe,
                          clock=clock, sleep=sleep)
    process.start()
    with pytest.raises(ArcoExited):
        process.wait_ready(timeout=15.0)
    assert len(probes) == 2


def test_arco_exited_is_an_arco_ready_timeout_for_existing_handlers():
    """Every caller today catches ArcoReadyTimeout (or Exception); the new
    failure must land in the same handlers rather than escape past them."""
    from control.arco_process import ArcoExited

    assert issubclass(ArcoExited, ArcoReadyTimeout)


def test_pty_popen_exec_failure_is_loud(tmp_path):
    """A child that cannot exec used to _exit(127) silently, racing the
    parent's first poll(). pty_popen now detects the failure synchronously
    over a close-on-exec pipe, writes the reason into the log (a 0-byte
    arco.log was the old symptom), reaps the child, and raises: the
    failure surfaces from ArcoProcess.start(), before any probe runs."""
    from control.arco_process import ArcoExecFailed, pty_popen

    log = tmp_path / "arco.log"
    with pytest.raises(ArcoExecFailed) as info:
        pty_popen([str(tmp_path / "no-such-binary")], log_path=str(log))

    assert "no-such-binary" in str(info.value)
    assert b"no-such-binary" in log.read_bytes()


def test_pty_popen_exec_success_leaves_no_error(tmp_path):
    from control.arco_process import pty_popen

    process = pty_popen(["/bin/sh", "-c", "exit 3"])
    assert process.wait(timeout=5.0) == 3


def test_start_propagates_a_spawn_failure_to_the_caller():
    """Terrarium.load_room wraps whatever start() raises as "Arco failed
    to start: ...", so the exec error must propagate, not be swallowed."""
    def popen(command):
        raise OSError(f"cannot exec {command[0]}")

    process = ArcoProcess(["/no/such/arco"], popen=popen)
    with pytest.raises(OSError, match="/no/such/arco"):
        process.start()


# --- readiness probe: deadline honoured, no false ready, half-reset recovery.
# All fakes: no pyarco, no o2lite, no network. ---

from control.arco_process import (  # noqa: E402
    STAGE_CONNECT, STAGE_RESET, _probe_engine)


class FakeO2:
    """Stand-in for o2litepy's o2lite: time_get() < 0 until connected;
    poll() runs an optional hook (the fake 'reset reply')."""

    def __init__(self, connected=False, on_poll=None):
        self.connected = connected
        self.on_poll = on_poll
        self.polls = 0

    def time_get(self):
        return 1.0 if self.connected else -1.0

    def poll(self):
        self.polls += 1
        if self.on_poll:
            self.on_poll()


class FakeEngine:
    """Stand-in for pyarco's ArcoEngine: initialize() connects and (unless
    told otherwise) completes the reset by setting zero."""

    def __init__(self, o2, *, connect=True, reset=True):
        self.o2 = o2
        self.zero = None
        self.connect = connect
        self.reset_ok = reset
        self.init_timeouts = []
        self.resets = 0

    def initialize(self, timeout=30):
        self.init_timeouts.append(timeout)
        if self.o2.time_get() > 0:
            return None                    # pyarco's "already started"
        if not self.connect:
            raise TimeoutError("Could not connect to Arco server")
        self.o2.connected = True
        if not self.reset_ok:
            raise TimeoutError("Could not reset Arco server")
        self.zero = object()

    def reset(self):
        self.resets += 1


def _probe(engine, o2, remaining, clock_sleep=None):
    clock, sleep = clock_sleep or make_clock()
    return _probe_engine(engine, o2, remaining, clock=clock, sleep=sleep)


def test_probe_passes_the_remaining_time_to_initialize():
    o2 = FakeO2()
    engine = FakeEngine(o2)
    assert _probe(engine, o2, 12.5) is True
    assert engine.init_timeouts == [12.5]


def test_probe_floors_the_connect_timeout_at_one_second():
    o2 = FakeO2()
    engine = FakeEngine(o2)
    _probe(engine, o2, 0.05)
    assert engine.init_timeouts == [1.0]


def test_initialize_returning_without_a_reset_is_not_ready():
    """No false ready: pyarco's early return must not count as ready."""
    o2 = FakeO2(connected=True)
    engine = FakeEngine(o2)
    engine.reset = lambda: None            # reset request never answered
    result = _probe(engine, o2, 0.5)
    assert result == STAGE_RESET
    assert engine.zero is None


def test_never_connected_reports_the_connect_stage():
    o2 = FakeO2()
    engine = FakeEngine(o2, connect=False)
    assert _probe(engine, o2, 5.0) == STAGE_CONNECT


def test_timing_out_in_the_reset_phase_reports_the_reset_stage():
    o2 = FakeO2()
    engine = FakeEngine(o2, reset=False)
    assert _probe(engine, o2, 5.0) == STAGE_RESET


def test_connected_but_not_reset_recovers_by_resetting_once_and_polling():
    o2 = FakeO2(connected=True)
    engine = FakeEngine(o2)
    engine.zero = None
    # the third poll is when the fake Arco's /actl/reset reply lands
    o2.on_poll = lambda: (setattr(engine, "zero", object())
                          if o2.polls >= 3 else None)
    assert _probe(engine, o2, 5.0) is True
    assert engine.resets == 1
    assert o2.polls == 3
    assert engine.init_timeouts == []      # recovery bypasses initialize()


def test_recovery_that_never_completes_gives_up_at_the_deadline():
    o2 = FakeO2(connected=True)
    engine = FakeEngine(o2)
    clock_sleep = make_clock()
    assert _probe(engine, o2, 2.0, clock_sleep) == STAGE_RESET
    assert engine.resets == 1
    assert 2.0 <= clock_sleep[0]() < 2.1   # bounded by remaining, not 15 s


def _probe_process(probe):
    clock, sleep = make_clock()
    process = ArcoProcess(["arco-server"], popen=FakePopen(), probe=probe,
                          clock=clock, sleep=sleep)
    process.start()
    return process, clock


def test_wait_ready_hands_the_probe_a_shrinking_remaining_time():
    seen = []
    process, clock = _probe_process(lambda r: seen.append(r) or False)
    with pytest.raises(ArcoReadyTimeout):
        process.wait_ready(timeout=1.0)
    assert seen[0] == 1.0
    assert seen == sorted(seen, reverse=True) and len(set(seen)) == len(seen)
    assert all(0 < r <= 1.0 for r in seen)


def test_wait_ready_never_starts_a_probe_after_the_deadline():
    clock, sleep = make_clock()
    starts = []

    def probe(remaining):
        starts.append(clock())
        return False

    process = ArcoProcess(["arco-server"], popen=FakePopen(), probe=probe,
                          clock=clock, sleep=sleep)
    process.start()
    with pytest.raises(ArcoReadyTimeout):
        process.wait_ready(timeout=1.0)
    assert starts and all(t < 1.0 for t in starts)


def test_timeout_message_names_the_connect_stage():
    process, _ = _probe_process(lambda r: STAGE_CONNECT)
    with pytest.raises(ArcoReadyTimeout) as info:
        process.wait_ready(timeout=1.0)
    message = str(info.value)
    assert message.startswith("Arco did not report ready within 1.0s")
    assert "never connected" in message and "avahi-daemon" in message


def test_timeout_message_names_the_reset_stage():
    process, _ = _probe_process(lambda r: STAGE_RESET)
    with pytest.raises(ArcoReadyTimeout) as info:
        process.wait_ready(timeout=1.0)
    message = str(info.value)
    assert message.startswith("Arco did not report ready within 1.0s")
    assert "connected but its reset never completed" in message


def test_timeout_message_without_a_stage_is_the_plain_one():
    process, _ = _probe_process(lambda r: False)
    with pytest.raises(ArcoReadyTimeout) as info:
        process.wait_ready(timeout=1.0)
    assert str(info.value) == "Arco did not report ready within 1.0s"
