"""One copy of a Python gotcha that costs an exit report every time it is
forgotten.

Python's finally blocks do NOT run on a bare SIGTERM. The default
disposition terminates the process immediately, with no unwinding. So a
module whose cleanup, its measurement summary, or its backend.close() lives
in a finally loses all of it the moment a supervisor signals it.

Three modules in this repo are signalled with SIGTERM:
control/simulator_process.py sends it to the Room simulator (whichever of
harness/room_simulator.py or harness/o2_shroom.py is playing that role),
and harness/run_stack.py sends it to harness/terrarium_boot.py. A bare
`kill <pid>` sends it to any of them.

This lived as an identical six-line copy in harness/led_smoke.py and
harness/room_simulator.py, and was about to become a third and fourth. The
docstring is most of the value, so one copy means one place to record why.
"""

from __future__ import annotations

import signal


def _raise_keyboard_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def sigterm_as_keyboard_interrupt() -> None:
    """Make `kill <pid>` clean up the same way Ctrl-C already does.

    Call once, at the top of main(), before anything with a finally block
    that matters.
    """
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
