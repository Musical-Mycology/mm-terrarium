"""stop_process: the bounded signal/escalate/reap cycle every spawned
subprocess in this repo shares.

Before this existed, ArcoProcess.shutdown() and SimulatorProcess.shutdown()
both sent SIGTERM and then called self._process.wait() with NO timeout. A
plain subprocess.Popen.wait() blocks forever, and the Room simulator is
always a plain Popen, so one client that ignored or was slow to handle its
stop signal hung teardown indefinitely. control/arco_process.py's
_PtyProcess.wait() had the right discipline and was the only place that did;
this is that discipline, factored out.

Why it polls in a loop rather than calling Popen.wait(timeout=...): a
_PtyProcess DRAINS ITS PTY inside poll(). Arco is a curses app redrawing
continuously, so an undrained pty buffer fills and blocks the server on its
own screen writes. A poll loop is the one shape that serves both a plain
Popen and the pty; Popen.wait() would starve the drain.
"""

from __future__ import annotations

import signal
import time


def stop_process(process, *, sig=signal.SIGTERM, timeout: float = 5.0,
                 kill_timeout: float = 5.0, clock=time.monotonic,
                 sleep=time.sleep):
    """Stop `process` and reap it. Returns its exit code, or None if it
    survived even SIGKILL.

    `process` needs only the two-method slice poll()/send_signal() that both
    subprocess.Popen and control/arco_process.py's _PtyProcess offer. It is
    deliberately NOT given the fd-closing responsibility: a plain Popen has
    no fd of its own, so the caller closes what it owns.

    None is a real return value, not a failure to report: a child stuck in
    uninterruptible sleep is the one way SIGKILL does not take effect
    promptly, and bounded has to mean bounded. The caller carries on tearing
    the rest down and says so.
    """
    code = process.poll()
    if code is not None:
        return code                     # already gone; do not signal a corpse
    process.send_signal(sig)
    code = _wait_bounded(process, timeout, clock, sleep)
    if code is not None:
        return code
    process.send_signal(signal.SIGKILL)
    # SIGKILL is delivered asynchronously: polling immediately can still see
    # the child unreaped and would report "still running" for a process that
    # is already dying.
    return _wait_bounded(process, kill_timeout, clock, sleep)


def _wait_bounded(process, timeout: float, clock, sleep):
    """Poll until the child exits or the budget runs out. Polls BEFORE
    checking the deadline, so a zero timeout still gives the child one look
    rather than none."""
    deadline = clock() + timeout
    while True:
        code = process.poll()
        if code is not None:
            return code
        if clock() >= deadline:
            return None
        sleep(0.02)
