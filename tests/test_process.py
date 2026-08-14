"""control/process.py's stop_process: the bounded signal/escalate/reap
cycle every spawned subprocess in this repo now shares."""
from __future__ import annotations

import signal

from control.arco_process import FakePopen
from control.process import stop_process


def _started(popen: FakePopen) -> FakePopen:
    """FakePopen is both the factory and the process, like the real seam."""
    return popen(["some-command"])


def test_sigterm_is_enough_for_a_well_behaved_child():
    popen = FakePopen()
    process = _started(popen)

    assert stop_process(process) is not None
    assert popen.signals == [signal.SIGTERM]


def test_an_already_exited_child_is_not_signalled_again():
    """Teardown runs unconditionally; a child that already exited must not
    turn it into a second signal or an error."""
    popen = FakePopen()
    process = _started(popen)
    process.send_signal(signal.SIGTERM)
    popen.signals.clear()

    assert stop_process(process) is not None
    assert popen.signals == []


def test_a_child_that_ignores_sigterm_is_killed_and_reaped():
    """The whole reason this function exists. A venue box restarting into a
    still-running Arco cannot bind its ports, and an orphaned o2lite client
    re-claims its dev name on the next hub, so teardown must escalate rather
    than return with the child alive."""
    popen = FakePopen(ignores=(signal.SIGTERM,))
    process = _started(popen)

    code = stop_process(process, timeout=0.05, kill_timeout=0.05,
                        sleep=lambda _s: None)

    assert popen.signals == [signal.SIGTERM, signal.SIGKILL]
    assert code is not None
    assert process.poll() is not None


def test_an_unkillable_child_returns_none_rather_than_hanging():
    """A child stuck in uninterruptible sleep is the one real way SIGKILL
    fails to take effect promptly. Bounded means bounded: report it and let
    the caller carry on tearing the rest down."""
    popen = FakePopen(ignores=(signal.SIGTERM, signal.SIGKILL))
    process = _started(popen)

    code = stop_process(process, timeout=0.05, kill_timeout=0.05,
                        sleep=lambda _s: None)

    assert code is None
    assert popen.signals == [signal.SIGTERM, signal.SIGKILL]


def test_the_clock_and_sleep_are_injectable_so_tests_spend_no_real_time():
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0, 20.0])
    slept = []
    popen = FakePopen(ignores=(signal.SIGTERM, signal.SIGKILL))
    process = _started(popen)

    stop_process(process, timeout=5.0, kill_timeout=5.0,
                 clock=lambda: next(ticks), sleep=slept.append)

    assert slept                       # it did loop
    assert all(s > 0 for s in slept)   # and it would have slept for real
