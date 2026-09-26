"""One copy of a Python gotcha that costs an exit report every time it is
forgotten.

Python's finally blocks do NOT run on a bare SIGTERM. The default
disposition terminates the process immediately, with no unwinding. So a
module whose cleanup, its measurement summary, or its backend.close() lives
in a finally loses all of it the moment a supervisor signals it.

Three modules in this repo are signalled with SIGTERM:
control/simulator_process.py sends it to the Room simulator
(harness/o2_shroom.py, playing that role), and harness/run_stack.py sends
it to harness/terrarium_boot.py. A bare `kill <pid>` sends it to any of
them.

This lived as an identical six-line copy in two harness scripts (both since
deleted), and was about to become a third and fourth. The
docstring is most of the value, so one copy means one place to record why.

This module also holds the parent-gone predicate shared by the launchers
(harness/o2_shroom.py and harness/terrarium_boot.py).
"""

from __future__ import annotations

import os
import signal


def parent_is_gone(expected_ppid, getppid=os.getppid) -> bool:
    """True once this process's parent is no longer the one that spawned it.

    The Room simulator is spawned by harness/terrarium_boot.py and, with
    --no-join, never exits on its own: o2_shroom.main()'s loop waits for a
    /release that only a live Control sends. So a Terrarium that dies
    without running its shutdown leaves this process running forever, and
    o2litepy reconnects it to the NEXT Arco that starts (o2lite.py:912
    connects whenever _tcp_socket is None, and _id_handler at :601
    re-announces every service on connect). There it claims this same dev
    name, and O2 refuses the new run's own simulator with "not from service
    provider" (o2/src/bridge.cpp:231-237) -- silently, since /_o2/*/sv is
    fire-and-forget. See docs/superpowers/specs/
    2026-08-14-room-simulator-service-collision-design.md.

    Compares against the pid the parent stamped in rather than watching
    getppid() for a change: if the parent died before this process read its
    argv, getppid() is ALREADY 1 and a change detector would wait forever.
    Comparison against a recorded value is correct in either order.

    expected_ppid None means the caller did not ask for this guard -- the
    default for a hand-run device -- and it never fires.
    """
    return expected_ppid is not None and getppid() != expected_ppid


def _raise_keyboard_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def sigterm_as_keyboard_interrupt() -> None:
    """Make `kill <pid>` clean up the same way Ctrl-C already does.

    Call once, at the top of main(), before anything with a finally block
    that matters.

    SIGHUP gets the same mapping (2026-09-12 run_stack parent-watch spec):
    a terminal closing over a supervisor otherwise kills it with no
    unwinding, and its children, in their own sessions, never see the
    hangup and play on. Left alone when SIGHUP is already ignored: that is
    what `nohup` sets, and a deliberately detached run must keep working.
    """
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is not None and signal.getsignal(sighup) is not signal.SIG_IGN:
        signal.signal(sighup, _raise_keyboard_interrupt)
