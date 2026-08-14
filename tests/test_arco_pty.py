"""pty_popen / _PtyProcess: the opt-in headless Arco spawn.

Exercised against /bin/echo rather than Arco -- these cover the Popen
work-alike contract ArcoProcess depends on (poll / send_signal / wait), not
Arco itself, so they stay offline and need no audio hardware.
"""
from __future__ import annotations

import signal
import time

import pytest

from control.arco_process import ArcoProcess, pty_popen


def _wait_for_exit(proc, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            return rc
        time.sleep(0.02)
    pytest.fail("child never exited")


def test_a_pty_child_runs_and_reports_its_exit_code():
    proc = pty_popen(["/bin/echo", "hello"])
    assert _wait_for_exit(proc) == 0
    proc.wait()


def test_poll_is_none_while_the_child_is_alive():
    proc = pty_popen(["/bin/sleep", "5"])
    assert proc.poll() is None
    proc.send_signal(signal.SIGKILL)
    _wait_for_exit(proc)
    proc.wait()


def test_the_child_output_is_drained_rather_than_filling_the_pty():
    """Arco is a curses app redrawing continuously. An undrained pty buffer
    fills and blocks the server on its own screen writes, so draining is
    what keeps it alive, not bookkeeping."""
    proc = pty_popen(["/bin/echo", "sentinel"])
    _wait_for_exit(proc)
    proc.wait()
    assert b"sentinel" in bytes(proc.output)


def test_send_signal_on_an_already_dead_child_does_not_raise():
    """shutdown() SIGTERMs unconditionally; a child that already exited must
    not turn teardown into an error."""
    proc = pty_popen(["/bin/echo", "x"])
    _wait_for_exit(proc)
    proc.wait()
    proc.send_signal(signal.SIGTERM)          # must be a no-op


def test_stop_process_escalates_to_sigkill_on_a_real_pty_child():
    """Escalation now lives in control/process.py, but it is worth keeping
    one test of it against a REAL child that really ignores SIGTERM rather
    than only against FakePopen. A venue box restarting into a still-running
    Arco cannot bind its ports, so teardown must not return with the child
    alive."""
    from control.process import stop_process

    proc = pty_popen(["/bin/sh", "-c", "trap '' TERM; sleep 30"])
    time.sleep(0.3)

    assert stop_process(proc, timeout=1.0, kill_timeout=5.0) is not None
    assert proc.poll() is not None
    proc.close()


def test_arco_process_accepts_pty_popen_through_its_existing_popen_seam():
    """The whole point of the design: no new ArcoProcess parameter, and the
    default subprocess.Popen path is untouched."""
    proc = ArcoProcess(["/bin/echo", "hi"], popen=pty_popen)
    proc.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.02)
    assert proc.poll() == 0
    proc.shutdown()


def test_the_child_output_is_teed_to_a_log_file(tmp_path):
    """Arco's output was drained into an in-memory bytearray that nothing
    ever wrote anywhere, so 'Arco never came up' was the least diagnosable
    failure in the stack: the runner is a separate process and could not
    reach the buffer even in principle."""
    log = tmp_path / "arco.log"
    proc = pty_popen(["/bin/echo", "sentinel"], log_path=str(log))
    _wait_for_exit(proc)
    proc.wait()

    assert b"sentinel" in log.read_bytes()


def test_the_in_memory_buffer_is_bounded(monkeypatch):
    """A curses app redrawing continuously for a long --hold run grew this
    without bound. Keep a tail for diagnostics, not the whole run."""
    from control import arco_process

    monkeypatch.setattr(arco_process, "_OUTPUT_TAIL_BYTES", 64)
    proc = pty_popen(["/bin/sh", "-c", "for i in $(seq 1 500); do echo aaaaaaaaaa; done"])
    _wait_for_exit(proc)
    proc.wait()

    assert len(proc.output) <= 64
