# Tick Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hold the Control tick loops and `render_bench.measure()` at a true 44 Hz mean by pacing to absolute deadlines instead of sleeping a fixed interval after each tick.

**Architecture:** A tiny `TickPacer` (new `harness/tick_pacer.py`) sleeps until the next absolute deadline, repays oversleep and work time on the following tick, and resyncs instead of bursting after a stall. The three 44 Hz loops in `harness/terrarium_boot.py` and `harness/render_bench.py`'s `measure()` use it in place of `sleep(...)`.

**Tech Stack:** Python 3.14, pytest, `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-09-25-tick-pacing-design.md`

## Global Constraints

- Suite command: `.venv/bin/python -m pytest tests -q`. The whole suite runs offline; no test may need Arco, luxaeterna's renderer, or the network.
- The pacer in the three loops uses its OWN default clock (`time.monotonic`), never the loop's `clock=` argument (existing tests script that clock with fixed-length iterators).
- `gs.tick(1.0 / 44.0)` keeps its nominal `dt`; do not change it.
- The 20 Hz `sleep(1.0 / 20.0)` in `_wait_for_room_ready` is out of scope; leave it.
- No em dashes in any prose (comments, docstrings, docs, commit messages). Use `--`, commas, or colons, matching the surrounding code.
- Match surrounding comment density and docstring style.

---

### Task 1: `TickPacer`

**Files:**
- Create: `harness/tick_pacer.py`
- Test: `tests/test_tick_pacer.py`

**Interfaces:**
- Produces: `harness.tick_pacer.TickPacer(period: float, *, clock=time.monotonic, sleep=time.sleep)` with method `wait() -> None` and attribute `period: float`. Raises `ValueError` if `period <= 0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tick_pacer.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tick_pacer.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'harness.tick_pacer'`

- [ ] **Step 3: Write the implementation**

Create `harness/tick_pacer.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tick_pacer.py -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add harness/tick_pacer.py tests/test_tick_pacer.py
git commit -m "feat(harness): TickPacer, deadline pacing that repays sleep slack"
```

---

### Task 2: Pace the three 44 Hz loops in `terrarium_boot`

**Files:**
- Modify: `harness/terrarium_boot.py` (`_wait_in_setup` ~line 498, `_serve_until_done` ~line 640, `_wait_for_load` ~line 709; the three `sleep(1.0 / 44.0)` lines at ~637, ~706, ~753)
- Test: `tests/test_terrarium_boot.py` (append at end of file)

**Interfaces:**
- Consumes: `harness.tick_pacer.TickPacer(period, *, clock=time.monotonic, sleep=time.sleep)`, `.wait()`.
- Produces: `_wait_in_setup(..., pacer=None)`, `_serve_until_done(..., pacer=None)`, `_wait_for_load(..., pacer=None)` (new keyword-only-by-convention parameter, appended last in each signature). Any object with a `wait()` method is accepted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium_boot.py`:

```python
class _CountingPacer:
    """Stands in for TickPacer: counts wait() calls."""

    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1


def _no_fixed_sleep(sleeps):
    def sleep(s):
        sleeps.append(s)
    return sleep


def test_wait_in_setup_paces_each_iteration_with_the_pacer():
    """The 2026-09-23 Art-Net run received ~33 fps against the 44 Hz tick:
    a fixed sleep(1/44) after each tick runs at 1 / (work + actual sleep),
    and macOS oversleeps 1/44 by ~4 ms. Each loop must pace to deadlines
    (harness/tick_pacer.py) instead."""
    from harness.terrarium_boot import _wait_in_setup

    polls = []

    class FakeAgent:
        def poll(self):
            polls.append(1)

    pacer, sleeps = _CountingPacer(), []
    ticks = iter([0.0, 0.1, 0.2, 0.3, 5.0])
    reason = _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                            sleep=_no_fixed_sleep(sleeps), pacer=pacer)
    assert reason == "expired"
    assert pacer.waits == len(polls) == 3
    assert sleeps == []


def test_serve_until_done_paces_each_iteration_with_the_pacer():
    from harness.terrarium_boot import _serve_until_done

    class FakeGS:
        def __init__(self):
            self.state = State.RUNNING
            self.ticks = 0

        def tick(self, dt):
            self.ticks += 1
            if self.ticks >= 3:
                self.state = State.IDLE

    class FakeAgent:
        closing = 0

        def poll(self):
            pass

    pacer, sleeps = _CountingPacer(), []
    reason = _serve_until_done(FakeGS(), FakeAgent(), _FakeArco(),
                               sleep=_no_fixed_sleep(sleeps), pacer=pacer)
    assert reason == "completed"
    assert pacer.waits == 2              # no wait after the completing tick
    assert sleeps == []


def test_wait_for_load_paces_each_iteration_with_the_pacer():
    from harness.terrarium_boot import _wait_for_load

    class FakeGS:
        def __init__(self):
            self.state = State.IDLE
            self.ticks = 0

        def tick(self, dt):
            self.ticks += 1
            if self.ticks >= 3:
                self.state = State.LOADED

    class FakeAgent:
        def poll(self):
            pass

    pacer, sleeps = _CountingPacer(), []
    reason = _wait_for_load(FakeGS(), FakeAgent(), _FakeArco(),
                            sleep=_no_fixed_sleep(sleeps), pacer=pacer)
    assert reason == "loaded"
    assert pacer.waits == 2
    assert sleeps == []


def test_the_default_pacer_routes_through_the_loops_sleep_seam():
    """No pacer given: the loop builds a TickPacer on its own `sleep`, so
    a test's no-op sleep still keeps the loop from really sleeping."""
    from harness.terrarium_boot import _serve_until_done

    class FakeGS:
        def __init__(self):
            self.state = State.RUNNING
            self.ticks = 0

        def tick(self, dt):
            self.ticks += 1
            if self.ticks >= 3:
                self.state = State.IDLE

    class FakeAgent:
        closing = 0

        def poll(self):
            pass

    sleeps = []
    reason = _serve_until_done(FakeGS(), FakeAgent(), _FakeArco(),
                               sleep=_no_fixed_sleep(sleeps))
    assert reason == "completed"
    # Two paced waits, both through the injected seam. (With a no-op sleep
    # the real clock barely moves, so the second deadline is ~2/44 away:
    # only positivity is asserted, not the value.)
    assert len(sleeps) == 2
    assert all(s > 0 for s in sleeps)
```

Note: `State` and `_FakeArco` are already defined at module scope in this test file (`from control.state import State` near the top; `class _FakeArco` near line 1130).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -q -k "pacer or pace"`
Expected: 3 FAIL with `TypeError: ... got an unexpected keyword argument 'pacer'`; `test_the_default_pacer_routes_through_the_loops_sleep_seam` may already pass (the old code sleeps exactly 1/44). That is fine: it pins the seam, not the bug.

- [ ] **Step 3: Implement**

In `harness/terrarium_boot.py`:

1. Add the import beside the other `harness` imports (alphabetical, after `from harness.signals import sigterm_as_keyboard_interrupt`):

```python
from harness.tick_pacer import TickPacer
```

2. Add a module constant near the top-level definitions (above `_wait_in_setup`):

```python
# Control's render tick. Paced to deadlines by TickPacer, never a fixed
# sleep after the work: see harness/tick_pacer.py for the measured cost.
_TICK_PERIOD = 1.0 / 44.0
```

3. `_wait_in_setup`: append `pacer=None` as the last parameter (after `uplink=None`). Immediately before its `while True:` (after `next_countdown = start + 15.0`), add:

```python
    if pacer is None:
        pacer = TickPacer(_TICK_PERIOD, sleep=sleep)
```

and replace its trailing `sleep(1.0 / 44.0)` with `pacer.wait()`.

4. `_serve_until_done`: append `pacer=None` as the last parameter (after `uplink=None`). Immediately before its `while True:`, add the same two lines, and replace its trailing `sleep(1.0 / 44.0)` with `pacer.wait()`.

5. `_wait_for_load`: append `pacer=None` as the last parameter (after `uplink=None`). After the early `if gs.state is not State.IDLE: return "loaded"` and before `while True:`, add the same two lines, and replace its trailing `sleep(1.0 / 44.0)` with `pacer.wait()`.

6. In each of the three docstrings, add one sentence at the end: `pacer, when given, replaces the default TickPacer(1/44) built on this function's own `sleep` (tests inject one); see harness/tick_pacer.py.`

Leave `gs.tick(1.0 / 44.0)` and `_wait_for_room_ready`'s `sleep(1.0 / 20.0)` unchanged.

- [ ] **Step 4: Verify**

Run: `grep -n "sleep(1.0 / 44.0)" harness/terrarium_boot.py`
Expected: no output.

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -q`
Expected: all pass (new and existing).

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "fix(boot): pace the 44 Hz tick loops to deadlines, not a fixed sleep"
```

---

### Task 3: Pace `render_bench.measure()`

**Files:**
- Modify: `harness/render_bench.py` (`measure()` and module docstring)
- Test: `tests/test_render_bench.py` (append at end)

**Interfaces:**
- Consumes: `harness.tick_pacer.TickPacer`.
- Produces: `measure(loop, seconds: float, *, clock=time.monotonic, sleep=time.sleep) -> FrameStats`. `loop` needs `frame_interval`, `backend.open()`, `backend.close()`, `_loop_once()` (unchanged contract).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_render_bench.py`:

```python
class _OversleepingTime:
    """A clock that moves only when slept on or worked, with a sleep that
    overshoots 4 ms: the dev-Mac figure for time.sleep(1/44) (2026-09-23)."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds + 0.004


def test_measure_holds_the_nominal_rate_despite_sleep_overshoot():
    """Pre-fix, measure() slept `frame_interval - elapsed` and never repaid
    the overshoot, so on a Mac it reported ~37.8 fps for a loop that could
    run at 44: the bench itself was slow."""
    t = _OversleepingTime()
    loop = FakeLoop()

    def work() -> int:
        t.now += 0.001
        loop.ticks += 1
        return 7

    loop._loop_once = work
    stats = measure(loop, 2.0, clock=t.clock, sleep=t.sleep)
    assert stats.mean_fps == pytest.approx(44.0, rel=0.01)
    assert stats.frames == loop.ticks
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_render_bench.py -q`
Expected: the new test FAILS with `TypeError: measure() got an unexpected keyword argument 'clock'`; the others pass.

- [ ] **Step 3: Implement**

In `harness/render_bench.py`, add `from harness.tick_pacer import TickPacer` after `from typing import Sequence`, and replace `measure()` with:

```python
def measure(loop, seconds: float, *, clock=time.monotonic,
            sleep=time.sleep) -> FrameStats:
    """Drive *loop* synchronously for *seconds*, timing every tick.

    Synchronous on purpose: the loop's own background thread reports a smoothed
    once-per-second FPS, which is exactly the averaging that hides a stall.

    Paced by TickPacer (deadlines, not `frame_interval - elapsed`), so the
    platform's sleep overshoot is repaid rather than read as a slow loop;
    each interval is start-of-tick to start-of-next-tick.
    """
    pacer = TickPacer(loop.frame_interval, clock=clock, sleep=sleep)
    intervals: list[float] = []
    loop.backend.open()
    try:
        deadline = clock() + seconds
        prev: float | None = None
        while True:
            tick = clock()
            if prev is not None:
                intervals.append(tick - prev)
            if tick >= deadline:
                break
            prev = tick
            loop._loop_once()
            pacer.wait()
    finally:
        loop.backend.close()
    return summarise(intervals)
```

Add to the module docstring, after the paragraph beginning "MM_TERRARIUM.md is explicit":

```
measure() paces its own ticks to deadlines (harness/tick_pacer.py). It times
the render path at a correctly paced rate; it does NOT time luxaeterna's own
threaded MultiUniverseOutputLoop._loop (luxaeterna/universeset.py), which
slept `frame_interval - elapsed` and so ran slow wherever sleep overshoots
until luxaeterna#23 paced it to deadlines too.
```

- [ ] **Step 4: Verify**

Run: `.venv/bin/python -m pytest tests/test_render_bench.py tests/test_tick_pacer.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/render_bench.py tests/test_render_bench.py
git commit -m "fix(render_bench): pace measure() to deadlines so sleep slack is not read as a slow loop"
```

---

### Task 4: Full suite, live re-measure, deep-dive (controller-run)

Needs a live Arco and the scratch probe, so the controller runs it, not a subagent.

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (the `devicelink/artnet_sink.py` entry's MEASURED bullet; the *Host platform* section's `render_bench.py` paragraph; `harness/` tooling list if it enumerates modules)

- [ ] **Step 1:** `.venv/bin/python -m pytest tests -q`; record the pass count.
- [ ] **Step 2:** Re-run the spec section 1 live measurement (listener on 16454, `run_stack --ci --no-bit --room DEMO` with the scratch `[[artnet]]` config and timing probe, 70 s) and `render_bench --host 127.0.0.1 --pixels 864 --seconds 25` against a listener on 6454. Record tick / sink / listener / bench figures as dev-box figures.
- [ ] **Step 3:** Update `docs/MM_TERRARIUM.md`: replace "the cause has not been investigated" with the diagnosis, the before/after figures, and a pointer to the spec; add the `TickPacer` note to *Host platform*; update the slice's test baseline line.
- [ ] **Step 4:** `.venv/bin/python -m tools.render_diagrams --check`; commit `docs(terrarium): diagnose the ~33 fps Art-Net rate; record TickPacer figures`.
