"""Pace a loop to absolute deadlines.

A loop that does its work and then sleeps a fixed period runs at
1 / (work + actual sleep), and the actual sleep is not the requested one:
measured 2026-09-23 on a dev Mac, time.sleep(1/44) returned after ~26.8 ms,
so the 44 Hz render tick ran ~37 Hz and the Art-Net output with it. See
docs/superpowers/specs/2026-09-25-tick-pacing-design.md.

TickPacer.wait() sleeps until the next deadline and then advances it by one
period, so a tick that ran long, or a sleep that overshot, is repaid on the
next tick and the MEAN rate holds at 1 / period. It fixes the mean, not the
per-tick jitter the platform's sleep adds.

A stall is lost time, never a burst: if a wait arrives more than one period
past its deadline, the schedule restarts from now instead of firing
back-to-back ticks to catch up.
"""

from __future__ import annotations

import time
from typing import Callable


class TickPacer:
    def __init__(self, period: float, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period
        self._clock = clock
        self._sleep = sleep
        # When the upcoming wait() should return; None until the first
        # wait() anchors the schedule.
        self._deadline: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._deadline is None:
            self._deadline = now + self.period
        elif now - self._deadline > self.period:
            # More than a whole period late: resync rather than burst.
            self._deadline = now + self.period
            return
        delay = self._deadline - now
        if delay > 0:
            self._sleep(delay)
        self._deadline += self.period
