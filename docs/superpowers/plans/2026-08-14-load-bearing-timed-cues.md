# Load-Bearing Timed Cues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Terrarium's timed-cue machinery actually drive audio and light, so one gesture produces one shared presentation time `at` that both the Room's light and the Room's drone honor.

**Architecture:** Control computes `at = origin + cue_horizon` once, in `GameServer`, and hands the finished time to a Bit rather than the ingredients. Light sessions (all of which live in Control) are fed as early as possible and the frame they render is stamped `when = at`, so the device holds it; Room audio waits on one `TimedQueue` until `at` because it reaches Arco with no wire in between. pyarco's scheduler is deleted from the path entirely.

**Tech Stack:** Python 3, pytest, stdlib only in `control/`. luxaeterna and pyarco are dev/test-only and reached by PYTHONPATH, never imported from `control/`.

**Spec:** [`docs/superpowers/specs/2026-08-14-load-bearing-timed-cues-design.md`](../specs/2026-08-14-load-bearing-timed-cues-design.md). Read section 2 (the timing model) and section 1.1 (findings F1 to F6) before starting.

## Global Constraints

- **No module under `control/` may import `o2litepy` or `pyarco`**, at module level or anywhere. `control/audio.py` is the exemplar. This is what keeps the whole suite runnable with no Arco, no pyarco and no O2 network.
- **The offline suite must stay fully offline.** Verify with `python -m pytest tests -v` in an environment with no Arco running and no `PYTHONPATH` pointing at the arco checkout.
- **Only Control writes to `/arco`** (boundary rule 1). Nothing in this plan touches that.
- **Lux Aeterna is downstream of Bit cue logic** (boundary rule 3). The Bit decides the consequence; the transport delivers it.
- **An in-process consumer is reached by a Python method call, not by O2** (boundary rule 4).
- **A test double must never be more permissive than the library it stands for** (boundary rule 5). Where this plan touches a double, it says what strictness the double must encode and why.
- **`cue_horizon` is one installation-wide constant**, never per-cue and never a literal in the source. It lives on `BootConfig.cue_horizon` and its current default (`0.060`) is a known-too-small placeholder; measuring it is a separate task and is explicitly not this plan's job.
- **Code comments use `--`, matching the existing repo style.** Do not introduce em dash characters.
- Commit after every task. Run the full suite (`python -m pytest tests -v`) before each commit, not just the new test.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `control/cues.py` | Cue value types and the `ROOM` target constant | Modify: add `ROOM` |
| `control/bit.py` | The `Bit` interface | Modify: handler signature docs, new `cues(at)` hook |
| `control/engine.py` | Lifecycle, `at` computation, cue dispatch, `ROOM` resolution | Modify: `__init__`, `data`, `tick`, new `_dispatch_cues`/`_resolve_dev`/`_origin` |
| `control/room_bridge.py` | Fans a Room's MIDI to its bound sinks | Modify: `feed_midi` replaced by `feed_light`/`feed_audio` |
| `control/audio.py` | Pure Control-side audio, no pyarco | Modify: drop timing API |
| `control/boot.py` | Boot sequence, constructs `GameServer` | Modify: thread `cue_horizon` and `clock` |
| `harness/arco_synth.py` | pyarco-backed pool | Modify: delete `schedule_at`, record why |
| `harness/terrarium_boot.py` | The runnable driver | Modify: thread `clock` into `boot()` |
| `devicelink/agent.py` | Where cue timing meets the wire | Modify: gesture stamp in, `at` out |
| `bits/test_bit.py` | The reference/regression fixture | Modify: Room-targeted tilt cue, `cues(at)` hook |
| `bits/capture_bit.py` | Tool Bit | Modify: handler signatures |
| `tests/test_timed_cues.py` | The end-to-end equality proof (criterion 4) | **Create** |

---

## Task 1: Delete the dead audio-scheduling path

Findings F2 and F3 in the spec: pyarco's scheduler polls on the same 44 Hz tick `TimedQueue` does (so a split buys no accuracy), and its `cause()` **raises** on a past time where `TimedQueue` clamps (so a split adds a contradictory past-time policy that would have fired on 93% of cues in the measured live run). This machinery has zero production callers. Delete it first, so no later task is tempted to reach for it.

**Files:**
- Modify: `control/audio.py:54-59` (`SynthPool` protocol), `control/audio.py:84-109` (`FakePool`), `control/audio.py:194-210` (`AudioBridge.feed_midi`)
- Modify: `harness/arco_synth.py:122-137`
- Test: `tests/test_audio.py:82-105`, `tests/test_arco_synth.py:62-86`

**Interfaces:**
- Consumes: nothing.
- Produces: `AudioBridge.feed_midi(dev: str, status: int, d1: int, d2: int) -> None`, with no `when` parameter. `SynthPool` protocol is `acquire/release/poll/shutdown` only.

- [ ] **Step 1: Write the failing test**

In `tests/test_audio.py`, delete `test_feed_midi_with_a_time_schedules_instead_of_applying` (lines 92-105) entirely and add this in its place:

```python
def test_no_scheduling_api_survives_on_the_audio_path():
    """Cue timing is Control's, never the synth backend's.

    pyarco's sched dispatches ONLY from sched.poll(), which ArcoSynthPool
    drives once per 44 Hz agent tick -- the same granularity
    control/timed_queue.py's TimedQueue already has, so scheduling here
    bought no accuracy. Worse, pyarco's Scheduler.cause RAISES RuntimeError
    on a negative offset (pyarco/sched.py:385, module global
    allow_late=False) where TimedQueue clamps and counts. Two opposite
    past-time policies on one cue. See findings F2/F3 in
    docs/superpowers/specs/2026-08-14-load-bearing-timed-cues-design.md.
    """
    import inspect

    from harness.arco_synth import ArcoSynthPool

    assert not hasattr(FakePool(), "schedule_at")
    assert not hasattr(ArcoSynthPool, "schedule_at")
    assert "when" not in inspect.signature(AudioBridge.feed_midi).parameters
```

Also edit `test_feed_midi_without_a_time_applies_immediately` (lines 82-89): rename it to `test_feed_midi_applies_immediately` and delete its final `assert pool.scheduled == []` line.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_audio.py::test_no_scheduling_api_survives_on_the_audio_path -v`
Expected: FAIL, `assert not hasattr(FakePool(), "schedule_at")` is False.

- [ ] **Step 3: Strip the timing API from `control/audio.py`**

Delete this line from the `SynthPool` Protocol (line 59):

```python
    def schedule_at(self, when: float, fn) -> None: ...
```

Delete from `FakePool`: the `self.scheduled: list[tuple[float, object]] = []` line in `__init__` (line 90) and the whole `schedule_at` method (lines 106-109).

Replace `AudioBridge.feed_midi` (lines 194-210) with:

```python
    def feed_midi(self, dev: str, status: int, d1: int, d2: int) -> None:
        """Apply one MIDI event to `dev`'s voice through its declared lanes.

        No `when`, deliberately. Cue timing is Control's, not the backend's:
        a Room cue waits on control/timed_queue.py's TimedQueue inside
        devicelink/agent.py and is applied here the instant it is released.
        pyarco's scheduler is NOT used -- see harness/arco_synth.py's note
        for the two reasons, both of which the offline suite cannot see.
        """
        self._apply_midi(dev, status, d1, d2)
```

- [ ] **Step 4: Strip `schedule_at` from `harness/arco_synth.py`**

Delete `schedule_at` (lines 122-134) and `_run_scheduled` (lines 136-137), and put this comment in their place, immediately after `poll()`:

```python
    # NO schedule_at(). pyarco's scheduler is deliberately not used for cue
    # timing, and re-adding it would reintroduce a bug no offline test can
    # see:
    #   1. sched dispatches ONLY from sched.poll(), which poll() above drives
    #      once per 44 Hz agent tick -- exactly the granularity
    #      control/timed_queue.py's TimedQueue already has. It buys nothing.
    #   2. pyarco's Scheduler.cause RAISES RuntimeError on a negative offset
    #      (pyarco/sched.py:385; the module global allow_late is False and
    #      the module-level cause() passes no late_ok), where TimedQueue
    #      clamps a past time and counts it. Two opposite past-time policies
    #      on one cue. The 2026-08-13 live run had 762 of 820 frames already
    #      past their deadline, so this would have raised on the MAJORITY of
    #      cues, been swallowed by DeviceLinkAgent's except, and killed Room
    #      audio silently while light kept moving.
    # See docs/superpowers/specs/
    # 2026-08-14-load-bearing-timed-cues-design.md findings F2/F3.
    # poll() stays: it is what pumps o2lite.
```

Then delete `test_schedule_at_delegates_to_the_pyarco_scheduler` from `tests/test_arco_synth.py` (lines 62-86). Its `FakeSched` double is itself the F4 finding: `absolute()` returned a marker tuple where the real one returns a float, and `cause()` never raised where the real one does.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS. Nothing outside the deleted tests passed `when=` to `feed_midi`.

- [ ] **Step 6: Commit**

```bash
git add control/audio.py harness/arco_synth.py tests/test_audio.py tests/test_arco_synth.py
git commit -m "refactor(terrarium): delete the unused pyarco cue-scheduling path

Zero production callers, and wiring it up would have been a bug: pyarco's
sched polls on the same 44 Hz tick TimedQueue does (no accuracy gained) and
its cause() raises on a past time where TimedQueue clamps. The 2026-08-13
run clamped 762/820 frames, so it would have raised on the majority and
silently killed Room audio. Reason recorded at the site."
```

---

## Task 2: `GameServer` computes `at` and hands it to handlers

`T = origin + horizon` gets exactly one home. A Bit receives the finished time, never the horizon and never a raw device stamp.

**Files:**
- Modify: `control/engine.py:1-20` (imports), `control/engine.py:33-64` (`__init__`), `control/engine.py:139-162` (`data`)
- Modify: `bits/test_bit.py:146,153,161` (three handler signatures)
- Modify: `bits/capture_bit.py:71,87` (two handler signatures)
- Test: `tests/test_engine_data.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `GameServer.__init__(bit_registry, room_binding=None, cue_horizon: float = 0.0, clock=time.monotonic)`
  - `GameServer.data(dev: str, verb: str, args: list, gesture_time: float | None = None) -> str | None`
  - `GameServer.rejected_stamps: int`
  - `GameServer._origin(gesture_time: float | None) -> float`
  - `control.engine._MAX_GESTURE_LEAD: float = 5.0`
  - A verb handler is now called `handler(dev, args, at)` and `at` is a `float`.

- [ ] **Step 1: Write the failing tests**

First update `VerbBit` in `tests/test_engine_data.py` so it records `at`. Change `__init__` to add `self.seen_at = []`, and change `_on_tilt` (line 35) to:

```python
    def _on_tilt(self, dev, args, at):
        if self.raise_next:
            raise RuntimeError("boom")
        if self.refuse_next is not None:
            return self.refuse_next
        self.seen.append((dev, args))
        self.seen_at.append(at)
        if self.next_cues is not None:
            return self.next_cues
        if self.next_cue is not None:
            return [self.next_cue]
        return [(dev, 0xB0, 74, 64)]
```

Then append these tests to `tests/test_engine_data.py`:

```python
def _joined(bit, cue_horizon=0.06, clock=lambda: 1000.0):
    gs = GameServer({"vb": lambda: bit}, cue_horizon=cue_horizon, clock=clock)
    gs.load_bit("vb")
    gs.join("ie1", "NODE_A")
    return gs


def test_handler_receives_at_computed_from_the_device_stamp():
    """T = gesture_time + cue_horizon, and the DEVICE's reading of the clock
    is the origin -- Design Rule 4, timestamps at the source. Jitter on the
    way up must not become jitter in the output."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0], gesture_time=999.5)
    assert bit.seen_at == [pytest.approx(999.56)]


def test_unstamped_gesture_falls_back_to_controls_clock():
    """The websocket transport never stamps: devicelink/protocol.py's _event
    defaults timestamp=0.0. That path must still produce a usable `at`."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0], gesture_time=0.0)
    assert bit.seen_at == [pytest.approx(1000.06)]


def test_negative_stamp_falls_back_to_controls_clock():
    """o2lite.time_get() returns -1 before clock sync. A cue scheduled
    against -1 is garbage."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0], gesture_time=-1.0)
    assert bit.seen_at == [pytest.approx(1000.06)]


def test_implausibly_future_stamp_is_refused_and_counted():
    """A device whose clock is wrong could otherwise park a cue hours out and
    hold a queue entry through teardown."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0], gesture_time=99999.0)
    assert bit.seen_at == [pytest.approx(1000.06)]
    assert gs.rejected_stamps == 1


def test_no_gesture_time_argument_still_works():
    """Callers that predate timing (harness drivers, console-driven calls)
    must keep working; they get Control's clock as the origin."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0])
    assert bit.seen_at == [pytest.approx(1000.06)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine_data.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'cue_horizon'`.

- [ ] **Step 3: Add the clock, the horizon and the origin rule to `GameServer`**

In `control/engine.py`, add `import time` to the imports block (after `import logging`).

Add this module constant just below `logger = logging.getLogger(__name__)`:

```python
# How far ahead of Control's own clock a device-supplied gesture timestamp may
# be before it is refused. A device whose clock is wrong could otherwise park a
# cue hours into the future and hold a TimedQueue entry through teardown.
# Refused stamps fall back to Control's clock and are counted -- see
# GameServer.rejected_stamps.
_MAX_GESTURE_LEAD = 5.0
```

Change the signature at line 33 and add three attributes at the end of `__init__` (after `self._observers: list = []`):

```python
    def __init__(self, bit_registry: dict, room_binding=None,
                 cue_horizon: float = 0.0, clock=time.monotonic):
```

```python
        # BootConfig.cue_horizon. ONE installation-wide constant: every cue's
        # target time is origin + horizon, computed here and nowhere else, so
        # a Bit receives a finished time rather than the ingredients. A
        # per-cue horizon would let two cues from one gesture land on
        # different frames and would make the clamp counters uninterpretable.
        self._horizon = cue_horizon
        # MUST be the same callable DeviceLinkAgent was built with. Two clock
        # bases is the bug that made the 2026-08-13 live run dark: Control
        # stamped frames off time.monotonic (~518,000) while the device
        # ticked on the O2 clock (~45). See harness/terrarium_boot.py build().
        self._clock = clock
        # Gesture stamps refused for being implausibly far ahead (see
        # _MAX_GESTURE_LEAD). A rising count means a device's clock is wrong.
        self.rejected_stamps = 0
```

Add this method just above `data`:

```python
    def _origin(self, gesture_time: float | None) -> float:
        """Resolve a cue's origin time: the device's own stamp when it is
        usable, else Control's clock.

        Three ways a stamp is unusable, all real. The websocket transport
        never stamps at all (devicelink/protocol.py's _event defaults
        timestamp=0.0). o2lite returns -1 until clock sync completes. And a
        device with a broken clock can send something implausible.
        """
        now = self._clock()
        if gesture_time is None or gesture_time <= 0:
            return now
        if gesture_time > now + _MAX_GESTURE_LEAD:
            self.rejected_stamps += 1
            logger.warning("refusing gesture stamp %.3f: more than %.1fs "
                           "ahead of %.3f", gesture_time, _MAX_GESTURE_LEAD,
                           now)
            return now
        return gesture_time
```

- [ ] **Step 4: Pass `at` to handlers**

Change `data`'s signature (line 139) and its handler call (line 162):

```python
    def data(self, dev: str, verb: str, args: list,
             gesture_time: float | None = None) -> str | None:
```

Add to `data`'s docstring, after the existing text:

```
        `gesture_time` is the inbound envelope's timestamp: the device's own
        reading of the O2 clock at the instant of the gesture. Control adds
        the installation's cue_horizon to it to get `at`, the time the
        consequence should be PRESENTED, and hands that to the handler.
```

Then, immediately before the `try:` around the handler call:

```python
        at = self._origin(gesture_time) + self._horizon
        try:
            cues = handler(dev, args, at)
```

- [ ] **Step 5: Update the five handlers in the tree**

`bits/test_bit.py`, three signatures only (bodies unchanged for now):

```python
    def _on_tilt(self, dev: str, args: list, at: float) -> list:
    def _on_tap(self, dev: str, args: list, at: float) -> list:
    def _on_shake(self, dev: str, args: list, at: float) -> list:
```

`bits/capture_bit.py`, two signatures (`at` is unused: a capture is a recording,
not a rendered consequence, so it has nothing to schedule):

```python
    def _on_capture(self, dev: str, args: list, at: float):
    def _on_telemetry(self, dev: str, args: list, at: float):
```

Update `bits/capture_bit.py`'s comment above them (line 68) to read:

```python
    # Both handlers return [] on success (there are no light cues to emit) or
    # a refusal string, which control/engine.py surfaces to the device as
    # /<dev>/error. Neither ever raises: boundary rule 2. `at` is unused: a
    # capture is a recording, not a rendered consequence, so there is nothing
    # here to schedule.
```

Update `control/bit.py`'s `verb_handlers` docstring, replacing the sentence
"A handler is called as handler(dev, args) and returns either a list":

```python
        A handler is called as handler(dev, args, at), where `at` is the
        absolute O2 time at which this gesture's consequence should be
        PRESENTED -- Control has already added the installation's
        cue_horizon to the device's own gesture stamp, so a Bit never sees
        the horizon and never sees a raw stamp. A handler returns either a
        list
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add control/engine.py control/bit.py bits/test_bit.py bits/capture_bit.py tests/test_engine_data.py
git commit -m "feat(terrarium): compute a cue's presentation time and hand it to Bits

GameServer gains cue_horizon and a clock, and computes at = origin +
horizon in one place. Verb handlers become handler(dev, args, at). The
origin is the device's own stamp when usable and Control's clock otherwise:
the websocket transport never stamps, o2lite returns -1 before sync, and a
broken device clock is refused and counted."
```

---

## Task 3: `ROOM` target and `when` stamping in one cue-dispatch path

A Bit gets a way to name the Room without holding a runtime device id, and every untimed cue picks up the `at` Control computed for whatever produced it.

**Files:**
- Modify: `control/cues.py`
- Modify: `control/engine.py` (`load_bit`, `data`, new `_dispatch_cues`/`_resolve_dev`)
- Test: `tests/test_engine_data.py`

**Interfaces:**
- Consumes: `GameServer._origin`, `at` from Task 2.
- Produces:
  - `control.cues.ROOM: str = "@room"`
  - `GameServer._dispatch_cues(cues, at: float | None) -> None`
  - `GameServer._resolve_dev(dev: str) -> str | None`
  - On-the-wire meaning: `on_light_cue(dev, status, data1, data2, when)` now receives `when = at` for cues that declared none.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine_data.py`:

```python
def _room_bound(bit, cue_horizon=0.06, clock=lambda: 1000.0, bound="sim-room"):
    from control.rooms import Room, RoomType
    gs = GameServer({"vb": lambda: bit}, cue_horizon=cue_horizon, clock=clock)
    gs.room = Room(room_type=RoomType.TEST)
    gs.room.bound_dev = bound
    gs.load_bit("vb")
    gs.join("ie1", "NODE_A")
    return gs


def test_room_target_resolves_to_the_bound_dev():
    """A Bit names the Room by a constant, never by the runtime id an
    admin-armed tap happened to bind -- that is what keeps a Bit
    offline-testable while still being able to drive the Room."""
    from control.cues import ROOM
    bit = VerbBit()
    bit.next_cues = [(ROOM, 0xB0, 74, 99)]
    gs = _room_bound(bit)
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    gs.data("ie1", "tilt", ["ie1", 0.0], gesture_time=999.5)
    assert seen == [("sim-room", 0xB0, 74, 99, pytest.approx(999.56))]


def test_room_target_with_no_room_bound_is_dropped_not_raised():
    from control.cues import ROOM
    bit = VerbBit()
    bit.next_cues = [(ROOM, 0xB0, 74, 99)]
    gs = _joined(bit)                      # no gs.room at all
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) is None
    assert seen == []


def test_untimed_cue_is_stamped_with_at():
    """A plain 4-tuple used to mean 'apply on arrival'. It now means 'apply
    at the time Control computed for this gesture', which is what makes one
    gesture produce one shared T without every Bit remembering to say so."""
    bit = VerbBit()
    gs = _joined(bit)
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    gs.data("ie1", "tilt", ["ie1", 0.0], gesture_time=999.5)
    assert seen == [("ie1", 0xB0, 74, 64, pytest.approx(999.56))]


def test_explicit_light_cue_time_wins_over_at():
    """A Bit that names its own time is expressing a derived offset (an echo
    at at+0.5); Control must not overwrite it."""
    bit = VerbBit()
    bit.next_cues = [LightCue("ie1", 0xB0, 74, 5, when=12345.0)]
    gs = _joined(bit)
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    gs.data("ie1", "tilt", ["ie1", 0.0], gesture_time=999.5)
    assert seen == [("ie1", 0xB0, 74, 5, 12345.0)]


def test_play_cue_can_target_the_room_too():
    from control.cues import PlayCue, ROOM
    bit = VerbBit()
    bit.next_cues = [PlayCue(ROOM, "click", "")]
    gs = _room_bound(bit)
    seen = []
    gs.on_play_cue = lambda *a: seen.append(a)
    gs.data("ie1", "tilt", ["ie1", 0.0])
    assert seen == [("sim-room", "click", "")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine_data.py -v -k "room_target or untimed or explicit_light or play_cue_can"`
Expected: FAIL with `ImportError: cannot import name 'ROOM' from 'control.cues'`.

- [ ] **Step 3: Add the `ROOM` constant**

In `control/cues.py`, after the imports and before `PlayCue`:

```python
# Sentinel dev id a Bit uses to target the Room. GameServer._resolve_dev turns
# it into the Room's bound dev; nothing downstream ever sees this string. A
# Bit therefore names the Room by a constant rather than by whatever id an
# admin-armed tap happened to bind, which is what keeps Bits offline-testable
# while still able to drive the Room. See docs/superpowers/specs/
# 2026-08-14-load-bearing-timed-cues-design.md section 4.1.
ROOM = "@room"
```

Add to `PlayCue`'s docstring, as a final paragraph:

```
    Untimed by design: the device owns when a local sample fires, so there is
    nothing on this path for Control to schedule. Its `dev` still goes
    through ROOM resolution like any other cue's.
```

- [ ] **Step 4: Extract and extend cue dispatch**

In `control/engine.py`, change the import line to `from control.cues import ROOM, LightCue, PlayCue`.

Add `self._warned_no_room = False` to `__init__`, next to `rejected_stamps`, and reset it in `load_bit` immediately after `self.bit = bit`:

```python
        self._warned_no_room = False     # once-per-Bit-load ROOM drop warning
```

Delete the whole `for cue in cues or ():` block from `data` (lines 174-200) and replace it with:

```python
        self._dispatch_cues(cues, at)
        return None
```

Then add these two methods immediately after `data`:

```python
    def _resolve_dev(self, dev: str) -> str | None:
        """cues.ROOM -> the Room's bound dev; anything else passes through.

        Returns None when a ROOM cue has no Room to go to, which the caller
        treats as a drop, never a raise. Warned once per Bit load rather than
        once per cue: a 20 Hz gesture stream would otherwise flood the log.
        """
        if dev != ROOM:
            return dev
        if self.room is None or self.room.bound_dev is None:
            if not self._warned_no_room:
                self._warned_no_room = True
                logger.warning("Bit emitted a ROOM cue with no Room bound; "
                               "dropping (logged once per Bit load)")
            return None
        return self.room.bound_dev

    def _dispatch_cues(self, cues, at: float | None) -> None:
        """Route a Bit's cues to the transport-owned sinks.

        Two things happen to every cue on the way out. A cue addressed to
        cues.ROOM is resolved to the Room's bound dev. And a cue that
        declares no time of its own gets `at`, the presentation time Control
        computed for whatever produced it -- which is what makes "one
        gesture, one T" hold without every Bit having to remember to say so.
        A Bit that DID name a time keeps it, because that is a deliberate
        derived offset (an echo at at+0.5), not an omission.

        Never raises. The whole per-cue block is guarded, not just the sink
        call: the 4-tuple unpack below is partial, so an arity-wrong cue from
        a buggy Bit would otherwise break data()'s documented "never raises"
        contract, and devicelink/agent.py's _on_verb has no handler around
        the call.
        """
        for cue in cues or ():
            try:
                if isinstance(cue, PlayCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    sink, args = self.on_play_cue, (dev, cue.name, cue.params)
                elif isinstance(cue, LightCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    when = at if cue.when is None else cue.when
                    sink, args = self.on_light_cue, (dev, cue.status,
                                                     cue.data1, cue.data2,
                                                     when)
                else:
                    # The historic plain 4-tuple. It used to mean "apply on
                    # arrival"; it now means "apply at the time Control
                    # computed for this cue's origin".
                    dev_, status, d1, d2 = cue
                    dev = self._resolve_dev(dev_)
                    if dev is None:
                        continue
                    sink, args = self.on_light_cue, (dev, status, d1, d2, at)
                if sink is None:
                    continue
                sink(*args)
            except Exception:
                logger.exception("cue dispatch failed; continuing")
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS. Existing tests that asserted `when=None` on a plain tuple now see a float; if any assert on the exact 5-tuple, update them to the computed `at` and note in the commit that the meaning changed.

- [ ] **Step 6: Commit**

```bash
git add control/cues.py control/engine.py tests/test_engine_data.py
git commit -m "feat(terrarium): add the ROOM cue target and stamp untimed cues with at

A Bit names the Room by a constant, not by the runtime dev id an
admin-armed tap bound, so it stays offline-testable. Cue dispatch moves
into one guarded _dispatch_cues shared by every cue source, which resolves
ROOM and stamps at onto any cue that declared no time. An explicit
LightCue.when still wins: that is a derived offset, not an omission."
```

---

## Task 4: `Bit.cues(at)`, the self-driven cue hook

Closes the deep-dive's "Nothing drives the Room's light during a live run": `Bit.update(dt)` returns only a completion bool, and verb handlers can only ever react to a device, so the Room's light reaches its declared static hue once and holds.

**Files:**
- Modify: `control/bit.py`
- Modify: `control/engine.py` (`tick`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `GameServer._dispatch_cues` from Task 3.
- Produces: `Bit.cues(at: float) -> list`, default `[]`. `GameServer._dispatch_bit_cues() -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
def test_bit_cues_are_dispatched_once_per_running_tick():
    """update(dt) answers 'am I done'; cues(at) answers 'what should happen'.
    Without this hook nothing in a Bit can animate the Room on its own."""
    from control.bit import Bit
    from control.cues import ROOM
    from control.roles import Role, RoleClass, RoleTable
    from control.rooms import Room, RoomType

    class AmbientBit(Bit):
        version = "0.1"
        def __init__(self):
            self.at_seen = []
        @property
        def role_table(self):
            player = Role(name="player", role_class=RoleClass.SHARED,
                          capacity=None, scored=False)
            return RoleTable(roles={"player": player},
                             node_map={"NODE_A": ["player"]})
        def cues(self, at):
            self.at_seen.append(at)
            return [(ROOM, 0xB0, 74, 42)]

    bit = AmbientBit()
    gs = GameServer({"ab": lambda: bit}, cue_horizon=0.06,
                    clock=lambda: 1000.0)
    gs.room = Room(room_type=RoomType.TEST)
    gs.room.bound_dev = "sim-room"
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    gs.load_bit("ab")
    gs.run()
    gs.tick(0.02)

    assert bit.at_seen == [pytest.approx(1000.06)]
    assert seen == [("sim-room", 0xB0, 74, 42, pytest.approx(1000.06))]


def test_bit_cues_are_not_dispatched_on_the_completing_tick():
    """A Bit that just signalled done is tearing down; dispatching a cue for
    it would put light on a device the engine is about to release."""
    from control.bit import Bit
    from control.roles import Role, RoleClass, RoleTable

    class DoneBit(Bit):
        version = "0.1"
        def __init__(self):
            self.cue_calls = 0
        @property
        def role_table(self):
            player = Role(name="player", role_class=RoleClass.SHARED,
                          capacity=None, scored=False)
            return RoleTable(roles={"player": player},
                             node_map={"NODE_A": ["player"]})
        def update(self, dt):
            return True
        def cues(self, at):
            self.cue_calls += 1
            return []

    bit = DoneBit()
    gs = GameServer({"db": lambda: bit})
    gs.load_bit("db")
    gs.run()
    gs.tick(0.02)
    assert bit.cue_calls == 0


def test_raising_bit_cues_does_not_wedge_the_tick():
    """Same guarantee every other Bit hook has: a misbehaving Bit must never
    stop Control reaching COMPLETING."""
    from control.bit import Bit
    from control.roles import Role, RoleClass, RoleTable

    class BadBit(Bit):
        version = "0.1"
        @property
        def role_table(self):
            player = Role(name="player", role_class=RoleClass.SHARED,
                          capacity=None, scored=False)
            return RoleTable(roles={"player": player},
                             node_map={"NODE_A": ["player"]})
        def cues(self, at):
            raise RuntimeError("boom")

    gs = GameServer({"bb": BadBit})
    gs.load_bit("bb")
    gs.run()
    gs.tick(0.02)                    # must not raise
    assert gs.state == State.RUNNING
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine.py -v -k "bit_cues or raising_bit_cues"`
Expected: FAIL. `bit.at_seen == []` because nothing calls `cues()`.

- [ ] **Step 3: Add the hook to the `Bit` interface**

In `control/bit.py`, add this method immediately after `update`:

```python
    def cues(self, at: float) -> list:
        """Self-driven cues for this tick, in the same vocabulary a verb
        handler returns: plain (dev, status, data1, data2) tuples,
        control.cues.LightCue, control.cues.PlayCue, and the
        control.cues.ROOM target.

        Called once per RUNNING tick, after update(dt), and skipped on the
        tick update() signals completion. `at` is the absolute time at which
        these cues should be PRESENTED; Control has already added the
        installation's cue_horizon to its own clock.

        This is the only way a Bit can animate anything without a device
        doing something: verb_handlers() can only ever react to a gesture,
        which is why the Room's light used to reach its declared static hue
        once and hold it for a whole run. Default: nothing to emit.
        """
        return []
```

- [ ] **Step 4: Dispatch it from `tick`**

Replace `GameServer.tick` (lines 202-206) with:

```python
    def tick(self, dt: float) -> None:
        if self.state != State.RUNNING:
            return
        if self.bit.update(dt):
            self._complete()
            return
        self._dispatch_bit_cues()

    def _dispatch_bit_cues(self) -> None:
        """Drain Bit.cues() once per RUNNING tick. A self-driven cue has no
        gesture behind it, so its origin is Control's own clock.

        Guarded exactly like every other Bit hook: a raising cues() must not
        stop this Bit reaching COMPLETING.
        """
        at = self._clock() + self._horizon
        try:
            cues = self.bit.cues(at)
        except Exception:
            logger.exception("Bit.cues raised; ignoring this tick")
            return
        self._dispatch_cues(cues, at)
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add control/bit.py control/engine.py tests/test_engine.py
git commit -m "feat(terrarium): add Bit.cues(at) for self-driven cues

update(dt) answers 'am I done'; cues(at) answers 'what should happen'.
Kept as a separate hook rather than a union return on update(), which
every Bit and most engine tests already depend on. Skipped on the
completing tick and guarded like every other Bit hook."
```

---

## Task 5: `RoomBridge.feed_light` / `feed_audio`

The two halves of a Room cue are now released at different times against one shared `at`, so a call that fans out to both at once is the one remaining way to silently lose the anchor. It goes.

**Files:**
- Modify: `control/room_bridge.py:66-70`
- Modify: `devicelink/agent.py:214` (the only caller)
- Test: `tests/test_room_bridge.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `RoomBridge.feed_light(status: int, d1: int, d2: int) -> None` and `RoomBridge.feed_audio(status: int, d1: int, d2: int) -> None`. `RoomBridge.feed_midi` no longer exists. The `RoomLightSink` / `RoomAudioSink` protocols keep `feed_midi(status, d1, d2)` unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests/test_room_bridge.py`, replace `test_unbound_bridge_feed_midi_is_a_noop`, `test_feed_midi_forwards_to_both_sinks` and `test_feed_midi_with_only_light_sink_skips_audio` with:

```python
def test_unbound_bridge_feeds_are_noops():
    bridge = RoomBridge()
    bridge.feed_light(0xB0, 74, 64)   # must not raise
    bridge.feed_audio(0xB0, 74, 64)   # must not raise


def test_feed_light_and_feed_audio_are_separately_addressable():
    """The two halves of a Room cue are released at DIFFERENT times against
    one shared `at`: light as early as possible, because the frame it
    renders still has to cross the wire, and audio at `at`, because it
    reaches Arco from Control with no wire in between. A single fan-out call
    could not express that. See the 2026-08-14 spec section 2."""
    bridge = RoomBridge()
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    bridge.bind("sim-room", light=light, audio=audio)

    bridge.feed_light(0xB0, 74, 64)
    assert light.fed == [(0xB0, 74, 64)]
    assert audio.fed == []

    bridge.feed_audio(0xB0, 74, 64)
    assert audio.fed == [(0xB0, 74, 64)]
    assert light.fed == [(0xB0, 74, 64)]


def test_feeds_with_only_a_light_sink_bound_skip_audio():
    bridge = RoomBridge()
    light = FakeRoomLightSink()
    bridge.bind("sim-room", light=light)
    bridge.feed_light(0x90, 60, 100)
    bridge.feed_audio(0x90, 60, 100)     # must not raise
    assert light.fed == [(0x90, 60, 100)]


def test_no_fan_out_call_survives():
    """A method feeding both sinks at once is the one remaining way to lose
    the shared anchor. Removed, not deprecated."""
    assert not hasattr(RoomBridge(), "feed_midi")
```

Also update line 45's `bridge.feed_midi(0xB0, 74, 64)` (the post-release no-op assertion) to `bridge.feed_light(0xB0, 74, 64)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_room_bridge.py -v`
Expected: FAIL with `AttributeError: 'RoomBridge' object has no attribute 'feed_light'`.

- [ ] **Step 3: Split the method**

In `control/room_bridge.py`, replace `feed_midi` (lines 66-70) with:

```python
    def feed_light(self, status: int, d1: int, d2: int) -> None:
        """Feed the light sink only.

        Separate from feed_audio because the two halves of a Room cue are
        released at different times against ONE shared `at`: light is fed as
        early as possible, since the frame it renders still has to cross the
        wire to reach the device by `at`, while audio waits until `at`
        because it reaches Arco from Control with no wire in between. See
        docs/superpowers/specs/
        2026-08-14-load-bearing-timed-cues-design.md section 2.
        """
        if self._light is not None:
            self._light.feed_midi(status, d1, d2)

    def feed_audio(self, status: int, d1: int, d2: int) -> None:
        """Feed the audio sink only. See feed_light for why they are split."""
        if self._audio is not None:
            self._audio.feed_midi(status, d1, d2)
```

Update the class docstring's last line, replacing "forwards the same MIDI bytes to both, mirroring harness/led_smoke.py's feed_shared() -- light and sound reading the same stream is the point":

```python
    """Owns whichever light/audio sinks are currently bound to the Room.

    Light and sound read the same stream, which is the point (mirroring
    harness/led_smoke.py's feed_shared()), but they are fed through separate
    calls because they are released at different times against one shared
    cue time -- see feed_light.
    """
```

- [ ] **Step 4: Update the single caller so the suite stays green**

In `devicelink/agent.py`, change line 214 from `self._room_bridge.feed_midi(status, d1, d2)` to `self._room_bridge.feed_light(status, d1, d2)` and the log message on line 216 to `"Room feed_light failed"`. Task 8 rewrites this method properly; this keeps the tree working in between.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add control/room_bridge.py devicelink/agent.py tests/test_room_bridge.py
git commit -m "refactor(terrarium): split RoomBridge.feed_midi into feed_light/feed_audio

The two halves of a Room cue are released at different times against one
shared cue time: light as early as possible because its frame still has to
cross the wire, audio at the cue time because it reaches Arco with no wire
in between. A single fan-out call cannot express that, and leaving one
available is the one remaining way to silently lose the anchor."
```

---

## Task 6: Thread the gesture stamp from the wire into the engine

The stamp is already on the envelope and already decoded (finding F6); only the last two hops discard it.

**Files:**
- Modify: `devicelink/agent.py:317-322` (`_handle` dispatch), `devicelink/agent.py:356-359` (`_on_verb`)
- Test: `tests/test_devicelink_agent.py` (`FakeServer.deliver`, plus new tests)

**Interfaces:**
- Consumes: `GameServer.data(..., gesture_time=...)` from Task 2.
- Produces: `DeviceLinkAgent._on_verb(dev: str, verb: str, args: list, gesture_time: float = 0.0) -> None`. `FakeServer.deliver(client, address, typespec="", args=None, timestamp=0.0)`.

- [ ] **Step 1: Make the fake transport able to carry a timestamp**

This is a boundary rule 5 change, not test convenience. Replace `FakeServer.deliver` in `tests/test_devicelink_agent.py` (lines 60-64) with:

```python
    def deliver(self, client, address, typespec="", args=None,
                timestamp=0.0):
        # `timestamp` is not optional decoration. The real o2lite transport
        # puts o2lite's msg_timestamp here (devicelink/o2_transport.py's
        # _to_msg), and the real websocket transport leaves it 0.0
        # (devicelink/protocol.py's _event default). A double that could
        # only ever produce one of those would hide half the design --
        # boundary rule 5 covers what a double omits as much as what it
        # permits.
        self.inbound.append((client, {"address": address,
                                      "typespec": typespec,
                                      "args": args or [],
                                      "timestamp": timestamp}))
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_devicelink_agent.py`:

```python
def test_gesture_stamp_reaches_the_engine():
    """The stamp is already on the envelope and already decoded; only
    _on_verb dropped it. Design Rule 4, timestamps at the source: jitter on
    the way up must not become jitter in the output."""
    gs, server, agent, dev, clk = _agent_with_joined_device()
    seen = []
    gs.data = lambda d, v, a, gesture_time=None: seen.append(gesture_time)
    server.deliver("c1", "/game/tilt", "sf", [dev, 12.0], timestamp=987.5)
    agent.poll()
    assert seen == [987.5]


def test_unstamped_gesture_reaches_the_engine_as_zero():
    """The websocket transport never stamps. GameServer falls back to its
    own clock there, and it can only do that if it is told 0.0 rather than
    something invented by the transport."""
    gs, server, agent, dev, clk = _agent_with_joined_device()
    seen = []
    gs.data = lambda d, v, a, gesture_time=None: seen.append(gesture_time)
    server.deliver("c1", "/game/tilt", "sf", [dev, 12.0])
    agent.poll()
    assert seen == [0.0]
```

`_agent_with_joined_device` (line 108) returns `(gs, server, agent, dev, clk)`, in that order. `list.append` returns `None`, so the stubbed `gs.data` reads as "handled" to `_on_verb` and no `/<dev>/error` is sent.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_devicelink_agent.py -v -k gesture_stamp`
Expected: FAIL with `TypeError: <lambda>() takes 3 positional arguments but 4 were given`, or `seen == []`.

- [ ] **Step 4: Thread the stamp through**

In `devicelink/agent.py`, change `_handle`'s final branch (line 322):

```python
        else:
            self._on_verb(dev, verb, env.args, env.timestamp)
```

and replace `_on_verb` (lines 356-359):

```python
    def _on_verb(self, dev: str, verb: str, args: list,
                 gesture_time: float = 0.0) -> None:
        """`gesture_time` is the inbound envelope's timestamp: the device's
        own reading of the O2 clock at the instant of the gesture (Design
        Rule 4, timestamps at the source). It is 0.0 on the websocket
        transport, which never stamps, and GameServer falls back to its own
        clock in that case -- so the transport must pass 0.0 through rather
        than invent anything.
        """
        reason = self.game_server.data(dev, verb, args,
                                       gesture_time=gesture_time)
        if reason is not None:
            self._send(dev, protocol.error_event(dev, verb, reason))
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "feat(terrarium): pass the inbound gesture timestamp to GameServer.data

The stamp was already on the envelope and already decoded; _on_verb was
where it died. FakeServer.deliver gains a timestamp parameter because the
real o2lite transport supplies msg_timestamp and the real websocket
transport supplies 0.0, and a double that can only produce one of those
hides half the design (boundary rule 5)."
```

---

## Task 7: The agent honors `at` on the per-device light path

Closes the deep-dive's "per-device timed cues are silently dropped", and stops the frame stamp being Control's render clock.

**Files:**
- Modify: `devicelink/agent.py:74-115` (`__init__`), `devicelink/agent.py:168-179` (`poll`), `devicelink/agent.py:247-282` (`_render_frames`), `devicelink/agent.py:410-429` (`_on_light_cue`)
- Test: `tests/test_devicelink_frames.py`

**Interfaces:**
- Consumes: `on_light_cue(dev, status, data1, data2, when)` carrying a real `when` from Task 3.
- Produces:
  - `DeviceLinkAgent._light_cues: TimedQueue` with payload `(dev, status, d1, d2, at)`
  - `DeviceLinkAgent._pending_at: dict[str, float]`
  - `DeviceLinkAgent._feed_light_now(dev: str, status: int, d1: int, d2: int, at: float | None) -> None`
  - `DeviceLinkAgent._drain_light_cues() -> None`
  - `/<dev>/leds` carries the cue's `at` as its `timestamp` when a cue produced the frame.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_devicelink_frames.py`. It already `importorskip`s luxaeterna and imports `FakeServer` from `tests.test_devicelink_agent`. Its existing rigs (`_make_rig`, `_make_rig_running`) drive a clock **iterator**, which cannot be rewound; these tests need a settable clock read many times per `poll()`, so add this rig alongside them:

```python
def _make_timed_rig(now, horizon):
    """A joined device on a SETTABLE clock, with GameServer and
    DeviceLinkAgent sharing that clock and one horizon.

    `now` is a one-element list the test mutates; both the engine and the
    agent read it. Unlike _make_rig's clock iterator this can be moved
    backwards and forwards freely, which cue-timing tests need.

    Driven to RUNNING before returning, for the same reason
    _make_rig_running does it: a session still playing its welcome
    signature does not render a cue's effect, and a frozen clock never
    finishes that signature. Returns (gs, server, agent) matching this
    file's convention; the test reads now[0] afterwards for its own base
    time, since warm-up advanced it by an amount the test should not
    hardcode.
    """
    clk = lambda: now[0]
    gs = GameServer({"test_bit": TestBit}, cue_horizon=horizon, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, horizon=horizon, clock=clk)
    gs.load_bit("test_bit")
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    for _ in range(200):
        if agent.bridges["ie1"].session.state == "running":
            break
        now[0] += 0.1
        agent.poll()
    else:
        pytest.fail("session never reached RUNNING")
    gs.run()
    server.sent.clear()
    agent._pending_at.clear()
    return gs, server, agent


HORIZON = 0.060


def _leds(server, dev="ie1"):
    return [m for d, m in server.sent if m["address"] == f"/{dev}/leds"]


def test_device_frame_carries_the_cues_own_time_not_the_render_clock():
    """A gesture's light must land at the time Control computed for THAT
    gesture. Stamping clock()+horizon here while the cue already carried
    origin+horizon charges the same constant twice, and the light would land
    at gesture + 2*horizon."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]
    at = (base - 0.005) + HORIZON          # 5 ms of delivery on the way up

    gs.on_light_cue("ie1", 0xB0, 74, 100, at)
    agent.poll()

    leds = _leds(server)
    assert leds, "the cue should have changed the rendered frame"
    assert leds[-1]["timestamp"] == pytest.approx(at)


def test_frame_with_no_cue_behind_it_carries_the_render_clock():
    """A breath-only frame is a STREAM frame: its origin genuinely is
    Control's tick, so clock()+horizon is the right stamp for it."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)

    now[0] += 1.0                          # the breath moves, no cue at all
    agent.poll()

    leds = _leds(server)
    assert leds
    assert leds[-1]["timestamp"] == pytest.approx(now[0] + HORIZON)


def test_earliest_pending_time_wins_for_one_frame():
    """One frame carries every cue applied that tick, so it must not be late
    for the soonest deadline among them."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]

    gs.on_light_cue("ie1", 0xB0, 74, 100, base + 0.20)
    gs.on_light_cue("ie1", 0xB0, 74, 110, base + 0.10)
    agent.poll()

    leds = _leds(server)
    assert leds
    assert leds[-1]["timestamp"] == pytest.approx(base + 0.10)


def test_a_cue_that_changes_no_frame_leaves_no_stale_time_behind():
    """A cue can feed a session without changing the rendered frame. If the
    pending time survived, a stale `at` would attach to some LATER frame and
    manufacture a spurious clamp on the device, corrupting the one counter
    the horizon measurement depends on."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]

    # cc:7 has no lane in TestBit's player light_manifest, so the session
    # accepts it and renders nothing differently.
    gs.on_light_cue("ie1", 0xB0, 7, 100, base + 0.01)
    agent.poll()
    server.sent.clear()

    now[0] = base + 5.0                    # much later: the breath has moved
    agent.poll()

    leds = _leds(server)
    assert leds, "the breath should have changed the frame"
    assert leds[-1]["timestamp"] == pytest.approx(now[0] + HORIZON)


def test_a_far_future_cue_is_held_before_it_reaches_the_session():
    """A Bit-declared cue further out than one horizon must not leak its
    state into whatever breath frame renders in between. Held until
    at - horizon, and NOT counted as a clamp."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]
    fed = []
    agent.bridges["ie1"].session.feed_midi = lambda s, a, b: fed.append((s, a, b))

    gs.on_light_cue("ie1", 0xB0, 74, 100, base + 0.50)   # feed at base+0.44
    agent.poll()
    assert fed == []
    assert agent._light_cues.clamped == 0

    now[0] = base + 0.45
    agent.poll()
    assert fed == [(0xB0, 74, 100)]
    assert agent._light_cues.clamped == 0


def test_a_gesture_cue_is_fed_immediately_and_counts_no_clamp():
    """A gesture cue's feed time (at - horizon) IS the gesture time, always
    past by the time Control sees it. Queueing it would count a clamp on
    every single gesture and destroy the counter's meaning, so it is applied
    directly instead."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]
    fed = []
    agent.bridges["ie1"].session.feed_midi = lambda s, a, b: fed.append((s, a, b))

    gs.on_light_cue("ie1", 0xB0, 74, 100, (base - 0.005) + HORIZON)
    assert fed == [(0xB0, 74, 100)]
    assert agent._light_cues.clamped == 0
    assert agent._light_cues.pending() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_devicelink_frames.py -v -k "carries_the_cues_own_time or earliest_pending or stale_time or far_future or counts_no_clamp"`
Expected: FAIL. The first one reports `timestamp == 1000.06` rather than `999.56`.

- [ ] **Step 3: Add the queue and the pending map**

In `devicelink/agent.py`'s `__init__`, immediately after `self._room_cues = TimedQueue()`:

```python
        # Deferred light-session feeds: (dev, status, d1, d2, at). ONLY a
        # Bit-declared cue further out than one horizon lands here. A gesture
        # cue's feed time (at - horizon) is the gesture time itself, already
        # past by the time Control sees it, so it is applied directly in
        # _on_light_cue and never queued -- queueing it would count a clamp on
        # every single gesture and destroy the counter's meaning.
        self._light_cues = TimedQueue()
        # dev -> the time the NEXT frame emitted for that dev must be
        # displayed, set when a cue is actually applied to its session. See
        # _feed_light_now for the earliest-wins rule and _render_frames for
        # why it is popped on every render attempt.
        self._pending_at: dict[str, float] = {}
```

Also update `self._horizon`'s comment (lines 82-86), which currently says the agent does not consult it:

```python
        # BootConfig.cue_horizon. Used two ways here. A frame with no cue
        # behind it (breath only) is a STREAM frame whose origin is this
        # tick, so it is stamped clock() + horizon. And a cue's session feed
        # is deferred to at - horizon when that is still in the future, so a
        # far-future state cannot leak into an intervening breath frame.
        self._horizon = horizon
```

- [ ] **Step 4: Add the feed and drain helpers**

Add these two methods immediately above `_on_light_cue`:

```python
    def _feed_light_now(self, dev: str, status: int, d1: int, d2: int,
                        at: float | None) -> None:
        """Apply a light cue to its session and record when the frame it
        produces must be displayed.

        Earliest wins: one frame carries every cue applied in a tick, so it
        must not be late for the soonest deadline among them.
        """
        if dev == self._room_dev and self._room_bridge is not None:
            try:
                self._room_bridge.feed_light(status, d1, d2)
            except Exception:
                logger.exception("Room feed_light failed")
                return
        else:
            bridge = self.bridges.get(dev)
            if bridge is None or bridge.session is None:
                return
            try:
                bridge.session.feed_midi(status, d1, d2)
            except Exception:
                logger.exception("feed_midi for %s failed", dev)
                return
        if at is None:
            return
        pending = self._pending_at.get(dev)
        if pending is None or at < pending:
            self._pending_at[dev] = at

    def _drain_light_cues(self) -> None:
        """Release deferred light-session feeds whose moment has come."""
        for (dev, status, d1, d2, at) in self._light_cues.due(self._clock()):
            self._feed_light_now(dev, status, d1, d2, at)
```

- [ ] **Step 5: Rewrite `_on_light_cue`**

Replace it entirely (lines 410-429):

```python
    def _on_light_cue(self, dev: str, status: int,
                      data1: int, data2: int,
                      when: float | None = None) -> None:
        """`when` is the cue's PRESENTATION time: GameServer computed it as
        origin + cue_horizon, once, for whatever produced this cue.

        Two halves, one anchor. Light is fed as early as possible, because
        the frame it renders still has to cross the wire to reach the device
        by `when`; that frame is stamped `when` and the device holds it.
        Room audio waits until `when` on _room_cues, because it reaches Arco
        from here with no wire in between. See docs/superpowers/specs/
        2026-08-14-load-bearing-timed-cues-design.md section 2.
        """
        now = self._clock()
        if dev == self._room_dev and self._room_bridge is not None:
            self._room_cues.push(when, (status, data1, data2), now=now)
        feed_at = None if when is None else when - self._horizon
        if feed_at is not None and feed_at > now:
            # A Bit-declared cue further out than one horizon. Hold the
            # session feed too, or the future state leaks into whatever
            # breath frame renders in between.
            self._light_cues.push(feed_at, (dev, status, data1, data2, when),
                                  now=now)
            return
        self._feed_light_now(dev, status, data1, data2, when)
```

- [ ] **Step 6: Drain before rendering, and stamp the frame**

In `poll()`, insert `self._drain_light_cues()` between `self._feed_breath()` and `self._render_frames()`:

```python
        self._feed_breath()
        # Before both renders: a feed released this tick must be reflected in
        # the frame rendered this tick, not the next one. Draining after would
        # delay every cue by one frame, exactly the class of error this
        # design exists to remove.
        self._drain_light_cues()
        self._render_frames()
        self._render_room()
        self._tick_audio()
```

In `_render_frames`, add the pop immediately after the `if universe is None or session is None: continue` guard:

```python
            # Popped on EVERY render attempt for this dev, changed frame or
            # not. A cue can feed a session without changing the frame; if
            # the entry survived, a stale `at` would attach to some later
            # frame and manufacture a spurious clamp on the device, which
            # would corrupt the one counter the horizon measurement depends
            # on. Popping before render_into also means a raised render drops
            # the time rather than mis-stamping a future frame.
            at = self._pending_at.pop(dev, None)
```

and replace the send block:

```python
            frame = bytes(universe.get_frame()[:36])
            if frame != self._last_frames.get(dev):
                self._last_frames[dev] = frame
                # The cue's own time when a cue produced this frame, else
                # this stream frame's own origin. Explicit `is not None`,
                # never truthiness: 0.0 is a legal O2 time.
                when = at if at is not None else self._clock() + self._horizon
                try:
                    self._send(dev, protocol.leds_event(dev, frame, when=when))
                except Exception:
                    logger.exception("leds send for %s failed", dev)
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_frames.py
git commit -m "feat(terrarium): honor a cue's time on the per-device light path

A per-device cue's when used to be accepted and dropped on the floor. The
session is now fed as early as possible and the frame it renders carries
the cue's own at, so the horizon is spent once, on the wire, instead of
being charged to both the cue and the frame. Frames with no cue behind them
keep clock()+horizon, which is correct: their origin is Control's tick."
```

---

## Task 8: The agent honors `at` on the Room path

Room audio gets its moment, and Room frames start carrying a time at all.

**Files:**
- Modify: `devicelink/agent.py:205-230` (`_render_room`), `devicelink/agent.py:152-156` (`clamped` docstring)
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `RoomBridge.feed_audio` (Task 5), `_pending_at` and `_room_cues` semantics (Task 7).
- Produces: `/sim-room/leds` carries a `timestamp`. `DeviceLinkAgent.clamped` means "Room audio cues that arrived already past their `at`".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_devicelink_agent.py`:

```python
def test_room_audio_waits_for_its_moment_and_light_does_not():
    """One anchor, two releases. Light is fed immediately because its frame
    still has to cross the wire; audio waits until `at` because it reaches
    Arco from Control with no wire in between."""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    now = [1000.0]
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=room_bridge,
                            horizon=0.060, clock=lambda: now[0])
    room_bridge.bind("sim-room", light=light, audio=audio)

    gs.on_light_cue("sim-room", 0xB0, 74, 100, 1000.05)
    assert light.fed == [(0xB0, 74, 100)]     # fed on arrival
    agent._render_room()
    assert audio.fed == []                    # not yet: at is 1000.05

    now[0] = 1000.06
    agent._render_room()
    assert audio.fed == [(0xB0, 74, 100)]


def test_room_frame_carries_a_time():
    """Room frames carried NO when at all before this: _render_room called
    leds_event with no timestamp, so they bypassed the device's queue and its
    clamp counter entirely while every per-device frame was scheduled."""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    now = [1000.0]
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge,
                            horizon=0.060, clock=lambda: now[0])
    server.bind_dev("sim-room", "c-room")

    gs.on_light_cue("sim-room", 0xB0, 74, 100, 1000.05)
    agent._render_room()

    leds = [m for d, m in server.sent if m["address"] == "/sim-room/leds"]
    assert leds
    assert leds[-1]["timestamp"] == pytest.approx(1000.05)


def test_a_room_audio_cue_already_past_clamps_and_counts():
    """The horizon being too small must be VISIBLE, not silent. This counter
    is what the separate horizon-measurement task consumes."""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    audio = FakeRoomAudioSink()
    now = [1000.0]
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=room_bridge,
                            horizon=0.060, clock=lambda: now[0])
    room_bridge.bind("sim-room", light=FakeRoomLightSink(), audio=audio)

    gs.on_light_cue("sim-room", 0xB0, 74, 100, 999.0)   # already past
    agent._render_room()
    assert audio.fed == [(0xB0, 74, 100)]               # released anyway
    assert agent.clamped == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_devicelink_agent.py -v -k "room_audio_waits or room_frame_carries or already_past_clamps"`
Expected: FAIL. `audio.fed` is `[]` because `_render_room` still calls `feed_light` for everything, and the frame has `timestamp == 0.0`.

- [ ] **Step 3: Rewrite `_render_room`**

Replace it entirely (lines 205-230):

```python
    def _render_room(self) -> None:
        if self._room_light is None or self._room_dev is None:
            return
        # Room AUDIO waits here for its moment. Room LIGHT was already fed in
        # _on_light_cue (or _drain_light_cues), because the frame it renders
        # still has to cross the wire to reach the simulator by `at`. One
        # anchor, two releases -- see the 2026-08-14 spec section 2.
        for (status, d1, d2) in self._room_cues.due(self._clock()):
            try:
                self._room_bridge.feed_audio(status, d1, d2)
            except Exception:
                logger.exception("Room feed_audio failed")
        # Popped unconditionally, for the same reason _render_frames does it:
        # a cue that changes no frame must not leave a stale time behind.
        at = self._pending_at.pop(self._room_dev, None)
        universe = self._room_light.universe
        try:
            self._room_light.session.render_into(universe)
        except Exception:
            logger.exception("Room render failed; skipping frame")
            return
        frame = bytes(universe.get_frame()[:36])
        if frame != self._last_frames.get(self._room_dev):
            self._last_frames[self._room_dev] = frame
            when = at if at is not None else self._clock() + self._horizon
            try:
                self._send(self._room_dev,
                           protocol.leds_event(self._room_dev, frame,
                                               when=when))
            except Exception:
                logger.exception("Room leds send failed")
```

- [ ] **Step 4: Correct the `clamped` docstring**

Replace it (lines 152-156):

```python
    @property
    def clamped(self) -> int:
        """Room AUDIO cues that arrived already past their target time.

        A rising count means BootConfig.cue_horizon is smaller than the
        upstream delivery time (gesture to Control). The downstream half is
        reported by the device's own counter, harness/shroom_client.py's
        ShroomClient.clamped, which rises when the horizon is smaller than
        the whole round trip. Both are dev-box figures.
        """
        return self._room_cues.clamped
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS. One existing test needs a look: `test_room_dev_cue_routes_to_room_bridge_not_normal_bridges` (`tests/test_devicelink_agent.py:457`) calls `gs.on_light_cue(...)` and then `agent._render_room()` with a comment saying the render is what drains the Room's timed queue. Light is now fed on arrival instead, so the `_render_room()` call is still needed (it renders the frame) but its comment is stale. Update the comment to say the render is what turns the already-fed cue into a frame. Change the comment, not the behavior.

The device side needs no new coverage: `tests/test_shroom_client.py` already has `test_a_timestamped_frame_is_held_until_its_time`, `test_an_unstamped_frame_shows_on_the_next_tick` and `test_a_frame_whose_time_has_passed_shows_immediately_and_clamps`, and `ShroomClient` uses the real `control/timed_queue.py`, so the spec's second double-strictness requirement is already satisfied by the existing suite. Room frames simply start taking the path per-device frames already took.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "feat(terrarium): honor a cue's time on the Room path

Room audio now waits on the Room's TimedQueue until the cue's time, and
Room frames carry a when at all -- _render_room called leds_event with no
timestamp, so Room frames bypassed the device's queue and its clamp counter
entirely while every per-device frame was scheduled."
```

---

## Task 9: Boot wiring, and one clock for the engine and the agent

`GameServer` now reads a clock (for `Bit.cues` and the no-stamp fallback), so the two-clock-bases failure that made the 2026-08-13 run dark is available again unless the two are literally the same callable.

**Files:**
- Modify: `control/boot.py:27-30` (`boot` signature), `control/boot.py:73` (`GameServer` construction)
- Modify: `harness/terrarium_boot.py:129-132` (the `_boot` call)
- Test: `tests/test_terrarium_boot.py`, `tests/test_boot.py`

**Interfaces:**
- Consumes: `GameServer(..., cue_horizon=..., clock=...)` from Task 2.
- Produces: `control.boot.boot(config, bit_registry, *, arco_command, room_binding, arco_process_cls=ArcoProcess, simulator_factory=None, known_device_connected=lambda dev: False, tick=None, clock=time.monotonic)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_terrarium_boot.py`. That file already has `_build_with_fakes(config, *, transport=None, clock=time.monotonic)` (line 23), which injects `_fake_arco`, a `FakePopen` and `_fake_room_audio()`; use it. It sits next to the existing `test_build_passes_the_supplied_clock_to_the_agent` (line 118), which this extends from the agent to the engine.

```python
def test_build_gives_the_engine_and_the_agent_one_clock_and_one_horizon():
    """Two clock bases is the bug that made the 2026-08-13 live run dark:
    Control stamped frames off time.monotonic while the device ticked on the
    O2 clock, roughly 518,000 against 45, so every frame was queued half a
    million seconds out and none ever displayed. GameServer now reads a clock
    too -- for Bit.cues origins and for the no-stamp fallback -- so the same
    failure is available again unless the two are literally the same
    callable.
    """
    clk = lambda: 4242.0
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit",
                        cue_horizon=0.111)
    gs, server, agent, arco, sim = _build_with_fakes(config, clock=clk)

    assert gs._clock is agent._clock is clk
    assert gs._horizon == 0.111
    assert agent._horizon == 0.111
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_terrarium_boot.py -v -k one_clock`
Expected: FAIL, `gs._clock` is `time.monotonic`, not `clk`.

- [ ] **Step 3: Thread the clock and the horizon through `boot()`**

In `control/boot.py`, add `clock=time.monotonic` as the last keyword-only parameter of `boot` (the module already imports `time`), and change line 73:

```python
        # cue_horizon and clock go in together and MUST match the ones
        # DeviceLinkAgent is built with: GameServer computes every cue's
        # target time (origin + horizon) and reads this clock for a
        # self-driven cue's origin and for the no-stamp fallback. Two clock
        # bases is the 2026-08-13 live-run bug.
        gs = GameServer(bit_registry, room_binding=room_binding,
                        cue_horizon=config.cue_horizon, clock=clock)
```

Add to `boot`'s docstring:

```
    `clock` is threaded into GameServer and must be the same callable the
    caller hands DeviceLinkAgent (harness/terrarium_boot.py's build() does
    exactly that). On the o2lite transport it is o2lite.time_get.
```

- [ ] **Step 4: Pass it from the driver**

In `harness/terrarium_boot.py`, change the `_boot` call (lines 129-132):

```python
    gs, room_bridge, arco = _boot(
        config, bit_registry, arco_command=arco_command,
        room_binding=room_binding, arco_process_cls=arco_process_cls,
        simulator_factory=factory, clock=clock)
```

Add to `build`'s `clock:` docstring paragraph, after the existing text:

```
    It is ALSO threaded into GameServer via boot(), because the engine now
    computes every cue's target time and reads this clock both for a
    self-driven cue's origin and for the fallback when a device did not
    stamp its gesture. The engine and the agent must read the same clock or
    a cue's time is unreachable -- the same failure this parameter was added
    to fix, one layer up.
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add control/boot.py harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "feat(terrarium): give the engine and the agent one clock and one horizon

GameServer now reads a clock (for Bit.cues origins and the no-stamp
fallback), which makes the 2026-08-13 two-clock-bases failure available
again unless boot() threads the identical callable into both. Pinned by a
test on build() rather than by a comment."
```

---

## Task 10: `TestBit` drives the Room

Where this becomes load-bearing. Without this task every mechanism above exists and nothing exercises it, which is exactly the predecessor spec's failure.

**Files:**
- Modify: `bits/test_bit.py` (imports, the stale NOTE at lines 73-83, `_on_tilt`, new `cues`)
- Test: `tests/test_test_bit.py`

**Interfaces:**
- Consumes: `control.cues.ROOM` (Task 3), `Bit.cues(at)` (Task 4), handler `at` (Task 2).
- Produces: `TestBit.ROOM_DRIFT_PERIOD: float = 12.0`, `TestBit.cues(at) -> list`, and `_on_tilt` returning two cues.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_test_bit.py`:

```python
def test_tilt_drives_the_calling_device_and_the_room_at_one_time():
    """The Room role declares cc:74 on BOTH its light_manifest (aurora hue)
    and its ugen_manifest (FluidSynth cutoff), so one tilt moves the Room's
    colour and the Room's drone timbre against a single shared time. This is
    the gesture that makes the timed-cue path load-bearing rather than merely
    present."""
    from control.cues import ROOM
    bit = TestBit()
    cues = bit._on_tilt("ie1", ["ie1", 0.0], at=1000.06)
    assert cues == [("ie1", 0xB0, 74, 64), (ROOM, 0xB0, 74, 64)]


def test_room_drift_is_a_deterministic_triangle():
    """Deterministic in _elapsed, which update(dt) already accumulates, so a
    test can assert the exact value. Triangle rather than sawtooth: a
    sawtooth snaps from 127 back to 0 once a period and aurora glides to its
    target, so the snap reads as a visible lurch."""
    from control.cues import ROOM
    bit = TestBit(run_duration=1000.0)
    bit.on_run_start()

    assert bit.cues(at=0.0) == [(ROOM, 0xB0, 74, 0)]

    bit.update(TestBit.ROOM_DRIFT_PERIOD / 2)          # half a period
    assert bit.cues(at=0.0) == [(ROOM, 0xB0, 74, 127)]

    bit.update(TestBit.ROOM_DRIFT_PERIOD / 2)          # a full period
    assert bit.cues(at=0.0) == [(ROOM, 0xB0, 74, 0)]


def test_room_animates_with_no_device_joined():
    """verb_handlers() can only react to a gesture. Without cues(), the
    Room's aurora reached its declared static hue once and held it for the
    whole run."""
    bit = TestBit(run_duration=1000.0)
    bit.on_run_start()
    bit.update(1.0)
    assert bit.cues(at=0.0), "the Room must animate with nobody joined"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_test_bit.py -v -k "tilt_drives or room_drift or room_animates"`
Expected: FAIL with `TypeError: _on_tilt() got an unexpected keyword argument 'at'` or a one-element cue list.

- [ ] **Step 3: Emit a Room cue from `tilt`**

In `bits/test_bit.py`, change the import line to:

```python
from control.cues import ROOM, PlayCue
```

Replace `_on_tilt` (lines 146-151):

```python
    def _on_tilt(self, dev: str, args: list, at: float) -> list:
        """args: [dev, gamma]. gamma is degrees in [-90, 90].

        Two cues, one `at`. The calling device's own hue lane, and the
        Room's. The Room role declares cc:74 on BOTH its light_manifest
        (aurora hue) and its ugen_manifest (FluidSynth cutoff), so one tilt
        moves the Room's colour and the Room's drone timbre against a single
        shared time. Neither cue names a time: control/engine.py stamps both
        with `at`, which is what makes "one gesture, one T" hold without a
        Bit having to remember to say so.
        """
        gamma = float(args[1]) if len(args) > 1 else 0.0
        gamma = max(-90.0, min(90.0, gamma))
        cc = int(round((gamma + 90.0) / 180.0 * 127.0))
        return [(dev, 0xB0, 74, cc), (ROOM, 0xB0, 74, cc)]
```

- [ ] **Step 4: Add the ambient drift**

Add the class attribute next to `version`:

```python
    # Seconds for one full out-and-back sweep of the Room's ambient hue.
    ROOM_DRIFT_PERIOD = 12.0
```

Add this method immediately after `update`:

```python
    def cues(self, at: float) -> list:
        """Self-driven Room animation: a slow hue drift so the Room breathes
        with nobody joined.

        verb_handlers() can only ever react to a device, so without this the
        Room's aurora reached its declared static hue once and held it,
        unanimated, for a whole run. Deterministic in self._elapsed, which
        update(dt) already accumulates, so a test can assert the exact value
        at a given elapsed time.

        Triangle rather than sawtooth: a sawtooth snaps from 127 back to 0
        once per period, and aurora GLIDES to its target, so the snap reads
        as a visible lurch rather than a wrap.
        """
        phase = (self._elapsed % self.ROOM_DRIFT_PERIOD) / self.ROOM_DRIFT_PERIOD
        cc = int(round(254 * (phase if phase < 0.5 else 1.0 - phase)))
        return [(ROOM, 0xB0, 74, cc)]
```

- [ ] **Step 5: Update the other two handler bodies' signatures and remove the stale NOTE**

`_on_tap` and `_on_shake` already took `at` from Task 2; leave their bodies alone.

Delete the NOTE comment block at lines 73-83 (starting `# NOTE: nothing in the current Bit interface can actually emit an`) and replace it with:

```python
        # The Room's own role. Its cc:74 lane is driven two ways now: by any
        # player's tilt (see _on_tilt) and by this Bit's own cues() drift, so
        # the Room animates whether or not anyone has joined.
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bits/test_bit.py tests/test_test_bit.py
git commit -m "feat(terrarium): TestBit drives the Room from a gesture and from itself

tilt now emits a Room-targeted cue alongside the calling device's, so one
gesture moves the Room's colour and its drone timbre against one shared
time. cues(at) adds a deterministic triangular hue drift so the Room
animates with nobody joined. Without this task every mechanism in this
branch exists and nothing exercises it, which is exactly how the
predecessor slice ended up with unmet criteria."
```

---

## Task 11: The end-to-end equality proof

Success criterion 4: one cue yields the same `at` on the audio and light paths, with no Arco, no pyarco and no O2.

**Files:**
- Create: `tests/test_timed_cues.py`

**Interfaces:**
- Consumes: everything from Tasks 2 through 10.
- Produces: nothing new; this task adds only tests.

- [ ] **Step 1: Write the failing test**

Create `tests/test_timed_cues.py`:

```python
"""One gesture, one shared presentation time, on both the Room's audio and
the Room's light. Success criterion 4 of docs/superpowers/specs/
2026-08-14-load-bearing-timed-cues-design.md.

Fully offline: no Arco, no pyarco, no O2. That is the whole point -- the
equality has to be checkable without the hardware it will eventually run on.
"""

import pytest

# devicelink.agent imports harness.device_bridge, which needs the sibling
# luxaeterna checkout. Same guard tests/test_devicelink_agent.py uses.
pytest.importorskip("luxaeterna")

from bits.test_bit import TestBit
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import RoomBridge
from control.rooms import Room, RoomType
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import FakeServer

HORIZON = 0.060
TICK = 1.0 / 44.0


class TickRecordingSink:
    """Records the clock reading at which each event arrived, not just the
    bytes, and forwards to the real sink underneath.

    Recording the time is not test convenience: "audio at `at`, light before
    it" is unassertable against a double that only keeps payloads, and
    boundary rule 5 covers what a double OMITS as much as what it permits.
    Forwarding matters too, because the light half has to actually reach the
    real LightSession or no frame changes and there is nothing to stamp.
    """

    def __init__(self, clock, inner=None):
        self._clock = clock
        self._inner = inner
        self.fed = []                      # (now, status, d1, d2)

    def feed_midi(self, status, d1, d2):
        self.fed.append((self._clock(), status, d1, d2))
        if self._inner is not None:
            self._inner.feed_midi(status, d1, d2)

    def clear(self):
        if self._inner is not None:
            self._inner.clear()

    def shutdown(self):
        pass


def _stack(now):
    """Control with TestBit loaded, the Room bound to 'sim-room', and one
    device joined to the scored `player` role, all on one settable clock.

    Ordering matters twice. load_bit() must precede DeviceLinkAgent, because
    the agent's _setup_room() reads the loaded Bit's Room declaration at
    construction time and silently builds nothing if there is no Bit yet.
    And the join must precede run(), because TestBit's `player` is a SCORED
    role and registration refuses those once RUNNING.

    Both sessions are then driven to RUNNING before returning: a session
    still playing its welcome signature does not render a cue's effect, and
    a frozen clock never finishes that signature. The caller reads now[0]
    afterwards for its own base time.

    run_duration is large so gs.tick() can be driven for several seconds in
    the ambient test without the Bit completing and unloading underneath it.
    """
    clock = lambda: now[0]
    binding = RoomBindingRegistry()
    gs = GameServer({"TestBit": lambda: TestBit(run_duration=1000.0)},
                    room_binding=binding, cue_horizon=HORIZON, clock=clock)
    gs.room = Room(room_type=RoomType.TEST)
    gs.room.bound_dev = "sim-room"
    binding.bind(RoomType.TEST, "sim-room")
    gs.load_bit("TestBit")

    server = FakeServer()
    room_bridge = RoomBridge()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge,
                            horizon=HORIZON, clock=clock)
    assert agent._room_light is not None, "the Room session must have built"

    light = TickRecordingSink(clock, inner=agent._room_light)
    audio = TickRecordingSink(clock)
    room_bridge.bind("sim-room", light=light, audio=audio)
    server.bind_dev("sim-room", "c-room")

    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    for _ in range(200):
        if (agent.bridges["ie1"].session.state == "running"
                and agent._room_light.session.state == "running"):
            break
        now[0] += 0.1
        agent.poll()
    else:
        pytest.fail("a session never reached RUNNING")
    gs.run()
    server.sent.clear()
    light.fed.clear()
    audio.fed.clear()
    agent._pending_at.clear()
    return gs, server, agent, light, audio


def test_one_gesture_yields_one_shared_presentation_time():
    now = [1000.0]
    gs, server, agent, light, audio = _stack(now)
    base = now[0]

    gesture = base - 0.005                # 5 ms of delivery on the way up
    at = gesture + HORIZON
    server.deliver("c1", "/game/tilt", "sf", ["ie1", 90.0],
                   timestamp=gesture)
    agent.poll()

    # Light: fed at once, because its frame still has to cross the wire.
    assert light.fed, "the Room's light should be fed on arrival"
    assert light.fed[0][0] < at
    assert light.fed[0][1:] == (0xB0, 74, 127)

    # And the frame it produced carries the gesture's own time, not
    # Control's render clock. This is the equality the whole design exists
    # for: the light frame's declared display time is derived once, from the
    # device's own stamp.
    leds = [m for d, m in server.sent if m["address"] == "/sim-room/leds"]
    assert leds, "the Room's frame should have changed"
    assert leds[-1]["timestamp"] == pytest.approx(at)

    # Audio: not yet. It reaches Arco with no wire, so it waits for `at`.
    assert audio.fed == []

    now[0] = at + TICK
    agent.poll()
    assert [f[1:] for f in audio.fed] == [(0xB0, 74, 127)]
    assert audio.fed[0][0] >= at
    assert audio.fed[0][0] - at <= TICK, "released within one tick of at"


def test_a_late_gesture_clamps_and_counts_rather_than_raising():
    """A horizon smaller than the delivery time must be VISIBLE, not silent.
    The 2026-08-13 run had 762 of 820 frames already past their deadline."""
    now = [1000.0]
    gs, server, agent, light, audio = _stack(now)
    base = now[0]

    server.deliver("c1", "/game/tilt", "sf", ["ie1", 90.0],
                   timestamp=base - 1.0)   # at is already a second past
    agent.poll()

    assert agent.clamped == 1
    assert [f[1:] for f in audio.fed] == [(0xB0, 74, 127)]   # released anyway


def test_the_room_animates_with_no_gesture_at_all():
    """Bit.cues(at) closes the other half: verb handlers can only react to a
    device, so without it the Room's light never moved during a real run."""
    now = [1000.0]
    gs, server, agent, light, audio = _stack(now)

    for _ in range(5):
        now[0] += 1.0
        gs.tick(1.0)
        agent.poll()

    assert light.fed, "cues(at) should have driven the Room with no gesture"
    assert {f[1:3] for f in light.fed} == {(0xB0, 74)}
    assert len({f[3] for f in light.fed}) > 1, "the hue should have moved"
```

- [ ] **Step 2: Run the test to verify it fails, then passes**

Run: `python -m pytest tests/test_timed_cues.py -v`
Expected on a tree where Tasks 2-10 are done: PASS. If it fails, the failure is in the wiring, not the test. Fix the wiring.

- [ ] **Step 3: Run the full suite offline**

Run: `python -m pytest tests -v`
Expected: PASS with no Arco running and no `PYTHONPATH` pointing at the arco checkout. Confirm the count of skipped tests is only the ones that `importorskip` luxaeterna or need `MM_ARCO_LIVE`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_timed_cues.py
git commit -m "test(terrarium): prove one gesture yields one shared cue time

Success criterion 4: a single tilt with a known device stamp produces one
at, the Room's light frame declares it as its display time, and the Room's
audio is released on it. No Arco, no pyarco, no O2. Also covers the clamp
path and the no-gesture Bit.cues path."
```

---

## Task 12: Live verification and documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-08-14-load-bearing-timed-cues-design.md` (Status line)
- Modify: `docs/MM_TERRARIUM.md` (via the `mm-deepdive-sync` skill)

**Interfaces:**
- Consumes: the whole branch.
- Produces: documentation only.

- [ ] **Step 1: Run the live verification**

This needs a real Arco and an interactive TTY: `ArcoProcess` cannot spawn Arco headless (its curses init opens `/dev/tty`), so an agent-driven run cannot do this. Hand it to the human.

**RUN ON: MYCOLOGICAL**

```bash
PYTHONPATH=/Users/chris/projects/arco python -m harness.terrarium_boot --transport o2lite --hold --setup-seconds 20
```

Restart the Arco server first: `pyarco`'s `arco.initialize()` calls `reset()`, which sends `/host/clear` and tears down the audio stream, so only the first client after a server start gets working audio on macOS.

Record three observations:
1. A tilt visibly moves the Room's hue **and** audibly moves the Room's drone timbre.
2. With no device joined, the Room still animates (the `cues(at)` drift).
3. Both clamp counters at teardown (`DeviceLinkAgent.clamped` and the simulator's `ShroomClient.clamped`). These are dev-box figures and feed the separate `cue_horizon` measurement task; they are not a venue number.

- [ ] **Step 2: Stamp the spec**

Change the spec's Status line to record the outcome, replacing `**Status:** Design. Not implemented.`:

```markdown
**Status:** Implemented and live-verified on <date>. <One sentence on what
was observed, including both clamp counts.> This document is a point-in-time
design record, not a living doc: for current behavior, constraints and known
issues read `docs/MM_TERRARIUM.md`.
```

- [ ] **Step 3: Sync the deep-dive**

Invoke the `mm-deepdive-sync` skill. The bullets in `docs/MM_TERRARIUM.md` that this branch changes:

- *Not yet built* / "Timed cues are plumbed but not load-bearing" (lines 796-809): now closed. Replace with what is actually true, including that `at` is a presentation time honored to within one 44 Hz tick, and that the per-path residual after that is unmeasured.
- *Not yet built* / "Nothing drives the Room's light during a live run" (lines 895-903): now closed by `Bit.cues(at)`.
- The `devicelink/o2_transport.py, control/timed_queue.py, harness/o2_shroom.py` section's closing paragraph, "The gap that survived this slice, and it is the important one" (lines 659-669): now closed; rewrite to point at this slice.
- Add `Bit.cues()` and the handler's `at` parameter to the `Bit` interface bullet under `control/` (lines 131-141).
- Add this spec to the *Design docs* list at the end.
- Leave the `cue_horizon` bullet (lines 828-838) open: this branch makes the counters meaningful, it does not set the number.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(terrarium): mark timed cues load-bearing and sync the deep-dive"
```

---

## Closeout

Use `superpowers:finishing-a-development-branch` (merge, branch and worktree cleanup) as the final step.
