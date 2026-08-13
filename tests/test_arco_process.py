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
