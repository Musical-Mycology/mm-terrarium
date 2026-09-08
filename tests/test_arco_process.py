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

    def probe():
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
                          probe=lambda: False, clock=clock, sleep=sleep)
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
                          probe=lambda: probes.append(1) or False,
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

    def probe():
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
