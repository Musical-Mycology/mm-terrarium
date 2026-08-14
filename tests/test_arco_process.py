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


def test_shutdown_sends_sigterm_and_waits():
    popen = FakePopen()
    process = ArcoProcess(["arco-server"], popen=popen)
    process.start()

    process.shutdown()

    assert popen.signals == [signal.SIGTERM]
    assert popen.waited is True


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
