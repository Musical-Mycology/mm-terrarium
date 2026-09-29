"""pty_popen / _PtyProcess: the opt-in headless Arco spawn.

Exercised against /bin/echo rather than Arco -- these cover the Popen
work-alike contract ArcoProcess depends on (poll / send_signal / wait), not
Arco itself, so they stay offline and need no audio hardware.
"""
from __future__ import annotations

import os
import signal
import sys
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


def test_pty_popen_changes_directory_for_the_child(tmp_path):
    """Arco reads arco_server_prefs.json from its cwd, so the pty spawn has
    to honor cwd the way subprocess.Popen(cwd=...) does. /bin/pwd reports
    the physical directory, hence resolve()."""
    proc = pty_popen(["/bin/pwd"], cwd=str(tmp_path))
    _wait_for_exit(proc)
    proc.wait()
    assert str(tmp_path.resolve()).encode() in bytes(proc.output)


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


# --- continuous drain: the pty holds only ~19.6 KB, so a child that writes
# more blocks until somebody reads the master. Arco does exactly that (~40 KB
# of ALSA errors on WSL) while the boot is in a settle sleep or a blocking
# pyarco probe, neither of which calls poll(). ---

_WRITER = ("import sys; sys.stdout.write('x' * 65535 + '\\n'); "
           "sys.stdout.flush()")


def _spawn_writer(tmp_path=None):
    pytest.importorskip("pty")
    log = tmp_path / "arco.log" if tmp_path else None
    return pty_popen([sys.executable, "-c", _WRITER],
                     log_path=str(log) if log else None), log


def test_a_child_writing_past_the_pty_capacity_finishes_without_poll():
    """The root cause of the slow/failed Arco boot: nothing reads the master
    between poll() calls, so the child blocks mid-write. Sleep without
    polling (a blocking probe) and check the child got to exit anyway."""
    proc, _ = _spawn_writer()
    try:
        time.sleep(1.0)                       # no poll(): simulated probe
        pid, _status = os.waitpid(proc.pid, os.WNOHANG)
        assert pid == proc.pid, "child still blocked writing to the pty"
        proc.returncode = 0                   # we reaped it ourselves
    finally:
        proc.close()


def test_every_byte_reaches_the_log_exactly_once(tmp_path):
    proc, log = _spawn_writer(tmp_path)
    try:
        time.sleep(1.0)
        _wait_for_exit(proc)
    finally:
        proc.wait()
    # pty output translation turns the newline into \r\n
    assert log.read_bytes() == b"x" * 65535 + b"\r\n"


def test_output_tail_stays_bounded_under_the_drain_thread(monkeypatch):
    from control import arco_process

    monkeypatch.setattr(arco_process, "_OUTPUT_TAIL_BYTES", 1024)
    proc, _ = _spawn_writer()
    _wait_for_exit(proc)
    proc.wait()
    assert 0 < len(proc.output) <= 1024
    assert bytes(proc.output).endswith(b"x\r\n")


def test_close_returns_promptly_and_joins_the_drain_thread():
    proc = pty_popen(["/bin/sleep", "30"])
    thread = proc._drain_thread
    assert thread.is_alive() and thread.daemon
    start = time.monotonic()
    proc.send_signal(signal.SIGKILL)
    proc.close()
    assert time.monotonic() - start < 1.0
    assert not thread.is_alive()
    _wait_for_exit(proc)
    proc.close()                              # idempotent
