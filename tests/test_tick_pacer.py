"""TickPacer: pace a loop to absolute deadlines, so the platform's sleep
slack and the loop's own work are repaid instead of added to every period.

Measured 2026-09-23 on a dev Mac: time.sleep(1/44) returns after ~26.8 ms,
not 22.7, so a loop that sleeps a fixed 1/44 after each tick ran ~37 Hz."""

from __future__ import annotations

import pytest

from harness.tick_pacer import TickPacer

P = 1.0 / 44.0


class FakeTime:
    """A clock that only moves when slept on (or advanced by hand), and a
    sleep that overshoots by a fixed slack, the way macOS's does."""

    def __init__(self, oversleep: float = 0.0) -> None:
        self.now = 0.0
        self.oversleep = oversleep
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds + self.oversleep


def test_oversleep_is_repaid_so_the_mean_rate_holds():
    t = FakeTime(oversleep=0.004)
    pacer = TickPacer(P, clock=t.clock, sleep=t.sleep)
    n = 440
    for _ in range(n):
        pacer.wait()
    # A fixed sleep would reach n * (P + 0.004) ~= 11.76 s here.
    assert abs(t.now - n * P) < P


def test_work_time_is_subtracted_from_the_next_sleep():
    t = FakeTime()
    pacer = TickPacer(P, clock=t.clock, sleep=t.sleep)
    pacer.wait()
    t.now += 0.010                       # the tick's own work
    pacer.wait()
    assert t.sleeps[-1] == pytest.approx(P - 0.010)


def test_first_wait_sleeps_about_one_period():
    t = FakeTime()
    pacer = TickPacer(P, clock=t.clock, sleep=t.sleep)
    pacer.wait()
    assert t.sleeps == [pytest.approx(P)]


def test_lateness_under_one_period_is_repaid_without_a_sleep():
    t = FakeTime()
    pacer = TickPacer(P, clock=t.clock, sleep=t.sleep)
    pacer.wait()                         # now = P, next deadline 2P
    t.now += 1.5 * P                     # now = 2.5P: half a period late
    pacer.wait()
    assert len(t.sleeps) == 1            # no sleep: already past due
    pacer.wait()                         # deadline 3P
    assert t.sleeps[-1] == pytest.approx(0.5 * P)


def test_a_stall_resyncs_instead_of_bursting():
    t = FakeTime()
    pacer = TickPacer(P, clock=t.clock, sleep=t.sleep)
    pacer.wait()                         # now = P, next deadline 2P
    t.now += 3 * P                       # stalled: now = 4P, 2P late
    pacer.wait()                         # resync: returns at once
    assert len(t.sleeps) == 1
    pacer.wait()                         # a full period, not a catch-up burst
    assert t.sleeps[-1] == pytest.approx(P)


def test_sleep_is_never_called_with_a_non_positive_value():
    t = FakeTime(oversleep=0.030)        # worse than a whole period
    pacer = TickPacer(P, clock=t.clock, sleep=t.sleep)
    for _ in range(50):
        pacer.wait()
    assert all(s > 0 for s in t.sleeps)


@pytest.mark.parametrize("period", [0.0, -0.1])
def test_a_non_positive_period_is_rejected(period):
    with pytest.raises(ValueError):
        TickPacer(period)
