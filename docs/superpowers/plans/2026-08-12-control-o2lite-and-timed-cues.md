# Control on o2lite and Timed Cues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Control a real O2 participant offering `game` on the Arco hub, and give every cue an absolute O2 time so audio and light are driven from one shared clock.

**Architecture:** Control becomes a guest on the o2lite connection pyarco already owns, offering `game` alongside pyarco's `actl` in one services string. A cue gains an optional target time on the O2 clock; a payload-generic holding queue releases work at that time on both the Control side (MIDI into a `LightSession`) and the device side (a rendered frame into LEDs). A Python simulated Tuneshroom over real o2lite is the acceptance vehicle.

**Tech Stack:** Python 3, pytest, o2litepy (via `PYTHONPATH=/Users/chris/projects/arco`), pyarco, luxaeterna (dev/test only).

**Spec:** [`docs/superpowers/specs/2026-08-12-control-o2lite-and-timed-cues-design.md`](../specs/2026-08-12-control-o2lite-and-timed-cues-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **No module under `control/` may import `o2litepy` or `pyarco`**, at module level or anywhere else. This is the rule that keeps the suite offline; `control/audio.py` is the existing exemplar.
- **Use the project virtualenv for every Python command.** There is no bare
  `python` on this box, and the sibling `luxaeterna` dev dependency is
  installed **only** in the venv. Set it once per shell:

  ```bash
  PY=/Users/chris/projects/mm-terrarium/.venv/bin/python
  ```

  Running `python3 -m pytest` instead collects an import error in
  `tests/test_terrarium_boot.py` that looks like a real failure and is not.
- **The full test suite must pass with no Arco server, no pyarco checkout, and no O2 network.** Run it with `$PY -m pytest tests -v`. The suite must be green with **zero failures and zero errors**; the passing count rises as each task adds tests (it was 544 passed, 1 skipped after Task 1), so treat the count as a moving number and the failure count as the gate.
- **Exactly one full-O2 process exists: the Arco server.** Everything else is an o2lite client. Nothing in this plan adds a second.
- **Control and pyarco share ONE o2lite connection and ONE services string.** `o2litepy`'s `set_services` does `self.services = services` (replace, not append), so the string is always `"actl,game"` written in full.
- **A dev id must be a valid O2 service name:** non-empty and at most 31 characters (o2litepy refuses longer).
- **`when=None` means apply on arrival.** Timing is opt-in; a Bit returning a plain 4-tuple keeps working.
- **The horizon is one installation-wide constant on `BootConfig`**, never a literal in source.
- **A negative `o2lite.time_get()` is a hard error**, never a silent zero. It means clock sync has not completed.
- **Transport exceptions never propagate into the engine tick** (boundary rule 2).
- Commit style follows the repo: `feat(terrarium): ...`, `fix(terrarium): ...`, `test(terrarium): ...`.

## Deviation from the spec, found during planning

The spec says the cue change is additive so "every existing test keeps passing unchanged." That is true for Bits and for the `devicelink` tests, which call `gs.on_light_cue(...)` with four positional arguments and are protected by a defaulted fifth parameter. It is **not** true for two engine tests that assert the exact tuple the sink received:

- `tests/test_engine_data.py:73` and `tests/test_engine_data.py:218`, both `assert cues == [("ie1", 0xB0, 74, 64)]`

Task 1 updates those two assertions to expect the trailing `None`. No other test is affected.

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `control/timed_queue.py` | Payload-generic hold-until-time queue. Pure, no imports beyond stdlib. |
| `devicelink/o2_transport.py` | o2lite-backed transport satisfying the agent's transport interface. Lazy o2litepy import. |
| `harness/o2_shroom.py` | The Python simulated Tuneshroom: o2lite client, WebSim LEDs, synthetic tilt sweep. |
| `harness/sync_bench.py` | Measures the dev-box delta between the audio call and the LED frame. |
| `tests/test_timed_queue.py` | Queue unit tests. |
| `tests/test_o2_transport.py` | Transport tests against `FakeO2Lite`. |
| `tests/test_o2_shroom.py` | Shroom client tests, socket-free. |
| `tests/test_sync_bench.py` | `summarise()` unit tests, no luxaeterna needed. |

**Modified:**

| File | Change |
| --- | --- |
| `control/cues.py` | Add `LightCue` with a `when` field. |
| `control/engine.py:176` | Dispatch `LightCue`; pass `when` as a fifth argument to `on_light_cue`. |
| `control/audio.py` | `SynthPool` Protocol and `FakePool` gain `schedule_at`; `AudioBridge.feed_midi` gains `when`. |
| `control/boot_config.py` | Add `cue_horizon` and `o2_ensemble`. |
| `devicelink/server.py` | Own the dev-to-connection map; `send(dev, msg)` replaces `send(client, msg)`. |
| `devicelink/agent.py` | Drop `_clients`; timed cue handling; timestamped frames. |
| `devicelink/protocol.py:86` | `leds_event` gains a timestamp argument. |
| `harness/arco_synth.py` | `ArcoSynthPool.schedule_at` wrapping `sched.cause(absolute(T), ...)`. |
| `harness/shroom_client.py` | Expose per-address handlers so o2lite can dispatch straight onto them. |
| `harness/terrarium_boot.py` | Accept an injected transport; wire the horizon; `--transport` switch. |
| `control/arco_process.py` | Add `poll()` so a dead Arco fails loud. |
| `tests/test_engine_data.py:73,218` | Expect the trailing `None`. |

---

### Task 1: `LightCue` and timed cue dispatch

Gives a Bit a way to say *when* a light cue should happen, without breaking the plain 4-tuple contract `control/cues.py` deliberately preserves.

**Files:**
- Modify: `control/cues.py`
- Modify: `control/engine.py:170-182`
- Modify: `devicelink/agent.py:356`
- Test: `tests/test_engine_data.py` (new tests, plus two assertion updates)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `LightCue(dev: str, status: int, data1: int, data2: int, when: float | None = None)`. The `on_light_cue` sink signature becomes `(dev: str, status: int, data1: int, data2: int, when: float | None)`. Tasks 3, 6 and 7 depend on both.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_engine_data.py`:

```python
from control.cues import LightCue


def test_plain_tuple_cue_carries_no_time():
    """A Bit returning the historic 4-tuple still works, and the sink sees
    when=None rather than a fabricated time."""
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)

    assert gs.data("ie1", "tilt", ["ie1", 30.0]) is None
    assert cues == [("ie1", 0xB0, 74, 64, None)]


def test_light_cue_carries_its_time():
    """A Bit opting into timing returns LightCue, and `when` reaches the
    sink unchanged."""
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)
    gs.bit.next_cue = LightCue("ie1", 0xB0, 74, 99, when=1234.5)

    assert gs.data("ie1", "tilt", ["ie1", 0.0]) is None
    assert cues == [("ie1", 0xB0, 74, 99, 1234.5)]
```

`VerbBit` in that file needs a `next_cue` hook. Find its `_on_tilt` handler and give the class an overridable cue:

```python
class VerbBit(Bit):
    # ... existing body unchanged ...
    next_cue = None            # set by a test to override the default cue

    def _on_tilt(self, dev, args):
        self.seen.append((dev, args))
        if self.refuse_next is not None:
            reason, self.refuse_next = self.refuse_next, None
            return reason
        if self.next_cue is not None:
            return [self.next_cue]
        return [(dev, 0xB0, 74, 64)]
```

Keep whatever `refuse_next` logic already exists; only the `next_cue` branch is new.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PY -m pytest tests/test_engine_data.py -v -k "carries"`
Expected: FAIL. `test_plain_tuple_cue_carries_no_time` fails on a 4-tuple where a 5-tuple was expected; `test_light_cue_carries_its_time` fails with `ImportError: cannot import name 'LightCue'`.

- [ ] **Step 3: Add `LightCue`**

Append to `control/cues.py`:

```python
@dataclass(frozen=True)
class LightCue:
    """A light cue carrying an explicit target time on the O2 clock.

    Plain 4-tuples (dev, status, data1, data2) remain valid and mean
    when=None, "apply on arrival" -- every Bit written before this type
    existed keeps working unchanged. A Bit opts into timing by returning
    LightCue instead. Distinct type rather than a 5-tuple for the same
    reason PlayCue is a distinct type: GameServer.data() tells cue kinds
    apart by identity, never by guessing at tuple arity.
    """
    dev: str
    status: int
    data1: int
    data2: int
    when: float | None = None
```

- [ ] **Step 4: Dispatch it in the engine**

In `control/engine.py`, change the import to include `LightCue`, then replace the cue loop body (currently at line 174-176):

```python
        for cue in cues or ():
            # The whole per-cue block is guarded, not just the sink call.
            # The old code was `sink, args = self.on_light_cue, tuple(cue)`,
            # which is total: it never raised for any-length iterable. The
            # 4-tuple unpack below is partial, so an arity-wrong cue from a
            # buggy Bit would otherwise raise straight out of data() and
            # break its documented "never raises" contract (engine.py's own
            # docstring) -- and devicelink/agent.py's _on_verb has no
            # handler around the call.
            try:
                if isinstance(cue, PlayCue):
                    sink, args = self.on_play_cue, (cue.dev, cue.name,
                                                    cue.params)
                elif isinstance(cue, LightCue):
                    sink, args = self.on_light_cue, (cue.dev, cue.status,
                                                     cue.data1, cue.data2,
                                                     cue.when)
                else:
                    # The historic plain 4-tuple: no declared time.
                    dev_, status, d1, d2 = cue
                    sink, args = self.on_light_cue, (dev_, status, d1, d2,
                                                     None)
                if sink is None:
                    continue
                sink(*args)
            except Exception:
                logger.exception("cue dispatch failed; continuing")
```

This replaces the existing `if sink is None: continue` / `try: sink(*args)`
block below it rather than sitting above one, so there is exactly one
`try` per cue.

Update the sink docstring at `control/engine.py:52` to read `on_light_cue(dev, status, data1, data2, when)`.

- [ ] **Step 5: Widen the agent's sink with a default**

In `devicelink/agent.py`, change the signature at line 356:

```python
    def _on_light_cue(self, dev: str, status: int,
                      data1: int, data2: int,
                      when: float | None = None) -> None:
```

The default matters: `tests/test_devicelink_agent.py` and `tests/test_devicelink_frames.py` call `gs.on_light_cue(...)` with four positional arguments in six places, and the default keeps all of them working. The body is unchanged in this task; Task 7 uses `when`.

- [ ] **Step 6: Update the two exact-tuple assertions**

In `tests/test_engine_data.py`, at line 73 and line 218, change:

```python
    assert cues == [("ie1", 0xB0, 74, 64)]
```

to:

```python
    assert cues == [("ie1", 0xB0, 74, 64, None)]
```

- [ ] **Step 7: Run the full suite**

Run: `$PY -m pytest tests -v`
Expected: PASS, all tests.

- [ ] **Step 8: Commit**

```bash
git add control/cues.py control/engine.py devicelink/agent.py tests/test_engine_data.py
git commit -m "feat(terrarium): give light cues an optional O2 time"
```

---

### Task 2: The payload-generic timed queue

The mechanism both sides use to hold work until its moment. Pure and stdlib-only, so it runs in the offline suite and on a Radxa alike.

**Files:**
- Create: `control/timed_queue.py`
- Test: `tests/test_timed_queue.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TimedQueue` with `push(when: float | None, payload, now: float) -> None`, `due(now: float) -> list`, and a `clamped: int` counter. Tasks 6, 7 and 8 use all three.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_timed_queue.py`:

```python
from control.timed_queue import TimedQueue


def test_a_future_payload_is_withheld_until_its_time():
    q = TimedQueue()
    q.push(10.0, "a", now=0.0)
    assert q.due(9.9) == []
    assert q.due(10.0) == ["a"]


def test_a_released_payload_is_not_released_twice():
    q = TimedQueue()
    q.push(1.0, "a", now=0.0)
    assert q.due(1.0) == ["a"]
    assert q.due(2.0) == []


def test_none_means_now_and_is_not_a_clamp():
    """when=None is 'no time declared', not 'late'. It must not inflate the
    clamp counter, which exists to report a too-small horizon."""
    q = TimedQueue()
    q.push(None, "a", now=5.0)
    assert q.due(5.0) == ["a"]
    assert q.clamped == 0


def test_a_past_time_releases_immediately_and_counts_as_clamped():
    q = TimedQueue()
    q.push(3.0, "late", now=5.0)
    assert q.due(5.0) == ["late"]
    assert q.clamped == 1


def test_payloads_are_released_in_time_order():
    q = TimedQueue()
    q.push(3.0, "third", now=0.0)
    q.push(1.0, "first", now=0.0)
    q.push(2.0, "second", now=0.0)
    assert q.due(10.0) == ["first", "second", "third"]


def test_equal_times_keep_insertion_order():
    """Two cues from one gesture share a time; the Bit's ordering is the
    only ordering available, so it must survive."""
    q = TimedQueue()
    q.push(1.0, "a", now=0.0)
    q.push(1.0, "b", now=0.0)
    assert q.due(1.0) == ["a", "b"]


def test_payloads_need_not_be_comparable():
    """Payloads are MIDI tuples on one side and frames on the other. Sorting
    must never fall through to comparing them."""
    q = TimedQueue()
    q.push(1.0, {"not": "comparable"}, now=0.0)
    q.push(1.0, {"also": "not"}, now=0.0)
    assert len(q.due(1.0)) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PY -m pytest tests/test_timed_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.timed_queue'`.

- [ ] **Step 3: Write the implementation**

Create `control/timed_queue.py`:

```python
"""TimedQueue: hold a payload until its moment on the O2 clock.

Payload-generic on purpose. The two consumers hold different things:
Control holds (when, midi) and feeds a luxaeterna LightSession; a device
holds (when, frame) and lights its LEDs. Keeping the payload opaque is
what lets one module serve both, and what makes the later move to
device-side rendering a change of payload rather than a change of
scheduling. See docs/superpowers/specs/
2026-08-12-control-o2lite-and-timed-cues-design.md section 5.3.

Pure and stdlib-only: it runs in the offline suite and on a Radxa alike.
"""

from __future__ import annotations


class TimedQueue:
    def __init__(self) -> None:
        self._items: list[tuple[float, int, object]] = []
        # Payloads released late because their time had already passed.
        # A rising count is the signal that BootConfig.cue_horizon is too
        # small -- see the design spec section 6.
        self.clamped = 0
        self._seq = 0

    def push(self, when: float | None, payload, now: float) -> None:
        """Queue `payload` for release at `when`.

        `when=None` means no time was declared: release at the next drain,
        and do NOT count it as a clamp. A `when` already in the past IS a
        clamp: it releases at the next drain and increments the counter.
        """
        if when is None:
            due_at = now
        elif when < now:
            self.clamped += 1
            due_at = now
        else:
            due_at = when
        # The sequence number keeps equal times in insertion order and, more
        # importantly, stops sort() from ever comparing two payloads -- they
        # are MIDI tuples on one side and dicts/frames on the other.
        self._items.append((due_at, self._seq, payload))
        self._seq += 1

    def due(self, now: float) -> list:
        """Release every payload whose time has arrived, in time order."""
        ready = [item for item in self._items if item[0] <= now]
        if not ready:
            return []
        self._items = [item for item in self._items if item[0] > now]
        ready.sort(key=lambda item: (item[0], item[1]))
        return [payload for (_, _, payload) in ready]

    def pending(self) -> int:
        """How many payloads are still waiting. Used by sync_bench and by
        teardown, which must not drop work still in flight."""
        return len(self._items)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$PY -m pytest tests/test_timed_queue.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add control/timed_queue.py tests/test_timed_queue.py
git commit -m "feat(terrarium): add the payload-generic timed release queue"
```

---

### Task 3: The audio scheduling seam

Lets `AudioBridge` schedule against the O2 clock without ever importing pyarco.

**Files:**
- Modify: `control/audio.py` (the `SynthPool` Protocol at line 54, `FakePool` at line 83, `feed_midi` at line 187)
- Modify: `harness/arco_synth.py` (the `ArcoSynthPool` class at line 62)
- Test: `tests/test_audio.py`, `tests/test_arco_synth.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SynthPool.schedule_at(when: float, fn: callable) -> None` on the Protocol, `FakePool.scheduled: list[tuple[float, callable]]`, and `AudioBridge.feed_midi(dev, status, d1, d2, when=None)`. Task 7 calls `feed_midi` with a `when`; Task 9 reads `FakePool.scheduled`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_audio.py`:

```python
def test_feed_midi_without_a_time_applies_immediately():
    """The pre-timing behavior is the default and must not regress."""
    pool = FakePool()
    bridge = AudioBridge(pool)
    bridge.on_grant("ie1", _role_with_cc74())
    bridge.feed_midi("ie1", 0xB0, 74, 100)
    assert pool.voices[0].ccs == [(74, 100)]
    assert pool.scheduled == []


def test_feed_midi_with_a_time_schedules_instead_of_applying():
    pool = FakePool()
    bridge = AudioBridge(pool)
    bridge.on_grant("ie1", _role_with_cc74())
    bridge.feed_midi("ie1", 0xB0, 74, 100, when=1234.5)

    assert pool.voices[0].ccs == []          # not applied yet
    assert len(pool.scheduled) == 1
    when, fn = pool.scheduled[0]
    assert when == 1234.5

    fn()                                      # the scheduler fires it
    assert pool.voices[0].ccs == [(74, 100)]
```

`_role_with_cc74()` is a helper for a Role whose `ugen_manifest` declares one flsyn instrument with a `cc:74 -> cc:74` lane. If `tests/test_audio.py` already has an equivalent helper, use that one instead of adding a second; otherwise add:

```python
def _role_with_cc74():
    from control.roles import Role, RoleClass
    return Role(
        name="player", role_class=RoleClass.SHARED, capacity=None,
        scored=True,
        ugen_manifest={"instruments": [
            {"instrument": "flsyn", "program": 89,
             "lanes": [{"source": "cc:74", "dest": "cc:74"}]}]},
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$PY -m pytest tests/test_audio.py -v -k "feed_midi_with_a_time or feed_midi_without_a_time"`
Expected: FAIL with `AttributeError: 'FakePool' object has no attribute 'scheduled'`.

- [ ] **Step 3: Extend the Protocol and the fake**

In `control/audio.py`, add to the `SynthPool` Protocol (line 54):

```python
class SynthPool(Protocol):
    def acquire(self) -> DeviceVoice: ...
    def release(self, voice: DeviceVoice) -> None: ...
    def poll(self) -> None: ...
    def shutdown(self) -> None: ...
    def schedule_at(self, when: float, fn) -> None: ...
```

And to `FakePool` (line 83):

```python
    def schedule_at(self, when: float, fn) -> None:
        """Record rather than run. A test fires the callable itself, which
        is what makes 'scheduled for T' assertable with no scheduler."""
        self.scheduled.append((when, fn))
```

with `self.scheduled: list[tuple[float, object]] = []` added to `FakePool.__init__`.

- [ ] **Step 4: Thread `when` through `feed_midi`**

In `control/audio.py`, change `feed_midi` (line 187) to take `when` and defer when it is set. The existing body becomes an inner closure:

```python
    def feed_midi(self, dev: str, status: int, d1: int, d2: int,
                  when: float | None = None) -> None:
        """Apply one MIDI event to `dev`'s voice through its declared lanes.

        `when` is an absolute time on the O2 clock. None means apply now,
        which is the pre-timing behavior and stays the default. A time is
        handed to the pool's scheduler rather than slept on: this module
        must never block the tick, and must never import pyarco to find a
        clock (boundary: see the module docstring).
        """
        def apply() -> None:
            self._apply_midi(dev, status, d1, d2)

        if when is None:
            apply()
        else:
            self._pool.schedule_at(when, apply)
```

Rename the current body of `feed_midi` to `_apply_midi(self, dev, status, d1, d2)`, unchanged except for the name. Whatever attribute currently holds the pool (check the constructor at line 126) is what `self._pool` must refer to.

- [ ] **Step 5: Run the audio tests**

Run: `$PY -m pytest tests/test_audio.py -v`
Expected: PASS, including the two new tests and every pre-existing one.

- [ ] **Step 6: Write the failing test for the real pool**

Add to `tests/test_arco_synth.py`:

```python
def test_schedule_at_delegates_to_the_pyarco_scheduler():
    """ArcoSynthPool must schedule through pyarco's sched, which already
    runs on O2 time (pyarco/arco_engine.py sets sched.time_get =
    o2lite_time_get). No pyarco import happens here: a fake sched is
    injected, exactly as the pool's other tests inject a fake flsyn."""
    from harness.arco_synth import ArcoSynthPool

    calls = []

    class FakeSched:
        def cause(self, when, obj, meth, *args):
            calls.append((when, obj, meth, args))

        def absolute(self, t):
            return ("absolute", t)

    pool = ArcoSynthPool()
    pool._sched = FakeSched()

    marker = lambda: None
    pool.schedule_at(99.5, marker)

    assert len(calls) == 1
    when, obj, meth, args = calls[0]
    assert when == ("absolute", 99.5)
```

- [ ] **Step 7: Run it to verify it fails**

Run: `$PY -m pytest tests/test_arco_synth.py -v -k schedule_at`
Expected: FAIL with `AttributeError: 'ArcoSynthPool' object has no attribute 'schedule_at'`.

- [ ] **Step 8: Implement `schedule_at` on the real pool**

Add to `ArcoSynthPool` in `harness/arco_synth.py`:

```python
    def schedule_at(self, when: float, fn) -> None:
        """Run `fn` at absolute O2 time `when`.

        pyarco's scheduler is already on O2 time (arco_engine.py sets
        sched.time_get = o2lite_time_get and syncs rtsched to it), so an
        absolute O2 second is exactly what cause() wants. sched.py's header
        is explicit that this accumulates logical time without drift or
        polling quantization, which is the whole reason the horizon can be
        a single constant.
        """
        if self._sched is None:
            raise RuntimeError("ArcoSynthPool.schedule_at before start()")
        self._sched.cause(self._sched.absolute(when), self, "_run_scheduled", fn)

    def _run_scheduled(self, fn) -> None:
        fn()
```

- [ ] **Step 9: Run the tests**

Run: `$PY -m pytest tests/test_arco_synth.py tests/test_audio.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add control/audio.py harness/arco_synth.py tests/test_audio.py tests/test_arco_synth.py
git commit -m "feat(terrarium): schedule audio against the O2 clock through the injected pool"
```

---

### Task 4: Move the client map into the transport

Under O2 there is no connection object, only an address. This removes the agent's assumption that one exists, which is what makes Task 5 a drop-in.

**Files:**
- Modify: `devicelink/server.py` (`send` at line 81, `drain_new_clients` at line 69)
- Modify: `devicelink/agent.py` (`_clients` in `__init__`, `client_for` at line 145, `_send` at line 379)
- Test: `tests/test_devicelink_server.py`, `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the transport interface every later task codes against:
  `drain_new_clients() -> list`, `drain_inbound() -> list[tuple[object, dict]]`,
  `send(dev: str, msg: dict) -> None`, `bind_dev(dev: str, client) -> None`,
  `drop_dev(dev: str) -> None`. Task 5 implements it; Task 7 calls `send`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_devicelink_server.py`:

```python
def test_send_addresses_a_dev_not_a_connection():
    """The agent must not need a connection object to reach a device: o2lite
    has none. The server owns the mapping."""
    server = DeviceLinkServer(host="127.0.0.1", port=0)
    sent = []

    class FakeConn:
        def send(self, raw):
            sent.append(raw)

    conn = FakeConn()
    server.bind_dev("ie1", conn)
    server.send("ie1", {"address": "/ie1/leds", "typespec": "b",
                        "args": [[0] * 36], "timestamp": 0.0})
    assert len(sent) == 1


def test_send_to_an_unbound_dev_is_a_silent_no_op():
    """A cue for a device that has gone away must not raise into the tick
    (boundary rule 2)."""
    server = DeviceLinkServer(host="127.0.0.1", port=0)
    server.send("nobody", {"address": "/nobody/leds", "typespec": "b",
                           "args": [[0] * 36], "timestamp": 0.0})


def test_drop_dev_unbinds():
    server = DeviceLinkServer(host="127.0.0.1", port=0)
    sent = []

    class FakeConn:
        def send(self, raw):
            sent.append(raw)

    server.bind_dev("ie1", FakeConn())
    server.drop_dev("ie1")
    server.send("ie1", {"address": "/ie1/leds", "typespec": "b",
                        "args": [[0] * 36], "timestamp": 0.0})
    assert sent == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_devicelink_server.py -v -k "addresses_a_dev or unbound_dev or drop_dev"`
Expected: FAIL with `AttributeError: 'DeviceLinkServer' object has no attribute 'bind_dev'`.

- [ ] **Step 3: Implement the mapping in the server**

In `devicelink/server.py`, add `self._devs: dict[str, object] = {}` to `__init__`, then add:

```python
    def bind_dev(self, dev: str, client) -> None:
        """Associate a dev id with its connection. Called by the agent once
        /game/hello names an otherwise anonymous client."""
        self._devs[dev] = client

    def drop_dev(self, dev: str) -> None:
        self._devs.pop(dev, None)
```

and change `send` (currently `send(self, client, msg)`) to:

```python
    def send(self, dev: str, msg: dict) -> None:
        """Send to a dev id. Unknown dev is a silent no-op: a cue for a
        device that has gone away must never raise into the engine tick."""
        client = self._devs.get(dev)
        if client is None:
            return
        # ... existing serialize-and-write body, using `client` ...
```

Keep the existing `broadcast` as it is.

- [ ] **Step 4: Simplify the agent**

In `devicelink/agent.py`:

- Delete `self._clients: dict[str, object] = {}` from `__init__`.
- Replace `client_for` (line 145) with a delegation, since existing tests use it:

```python
    def client_for(self, dev: str):
        return self.server._devs.get(dev)
```

- Change `_send` (line 379) to:

```python
    def _send(self, dev: str, msg: dict) -> None:
        self.server.send(dev, msg)
```

- Wherever `_handle` currently records `self._clients[dev] = client` on hello, call `self.server.bind_dev(dev, client)` instead. Wherever it removes a dev from `_clients` on release or disconnect, call `self.server.drop_dev(dev)`.

- [ ] **Step 5: Repoint the one test that writes to `_clients`**

`tests/test_devicelink_agent.py:470` does `agent._clients["sim-room"] = client`
to simulate the hello handshake. That attribute no longer exists, and
`client_for` is a getter, so it cannot replace a write. Change it to:

```python
    agent.server.bind_dev("sim-room", client)   # simulate the hello handshake
```

- [ ] **Step 6: Run the full suite**

Run: `$PY -m pytest tests -v`
Expected: PASS. Any other failure referencing `agent._clients` is a read, and
becomes `agent.client_for(dev)`.

- [ ] **Step 7: Commit**

```bash
git add devicelink/server.py devicelink/agent.py tests/test_devicelink_server.py
git commit -m "refactor(terrarium): address devices by dev id, not connection"
```

---

### Task 5: The o2lite transport and `FakeO2Lite`

**Files:**
- Create: `devicelink/o2_transport.py`
- Test: `tests/test_o2_transport.py`

**Interfaces:**
- Consumes: the transport interface from Task 4.
- Produces: `O2LiteTransport(services: str = "actl,game")` with `start(o2lite)`, `drain_new_clients()`, `drain_inbound()`, `send(dev, msg)`, `bind_dev(dev, client)`, `drop_dev(dev)`, `stop()`. Also `FakeO2Lite` with `.sent: list[tuple[str, float, str, tuple]]` and `.services: str`. Tasks 6, 8 and 9 use both.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_o2_transport.py`:

```python
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport


def _started():
    fake = FakeO2Lite()
    transport = O2LiteTransport()
    transport.start(fake)
    return transport, fake


def test_start_registers_game_alongside_actl():
    """set_services REPLACES rather than appends (o2litepy o2lite.py:707),
    so Control must write the whole string. Dropping actl would silently
    stop Arco's control replies -- the failure this test exists to catch."""
    transport, fake = _started()
    assert fake.services == "actl,game"


def test_start_refuses_an_unsynced_clock():
    """time_get() returns -1 until clock sync completes. Scheduling against
    -1 is garbage, so this is a hard error rather than a silent zero."""
    fake = FakeO2Lite(now=-1.0)
    transport = O2LiteTransport()
    try:
        transport.start(fake)
    except RuntimeError as exc:
        assert "clock" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError on an unsynced clock")


def test_inbound_game_messages_are_drained_as_envelopes():
    """o2litepy hands a handler the address with its leading '/' already
    stripped (O2lite_handler.__init__ does address[1:]). The transport must
    re-prefix it, or the agent sees "game/tilt" and drops every frame."""
    transport, fake = _started()
    fake.deliver("/game/tilt", "sf", ("ie1", 30.0))
    drained = transport.drain_inbound()
    assert len(drained) == 1
    _client, msg = drained[0]
    assert msg["address"] == "/game/tilt"
    assert msg["args"] == ["ie1", 30.0]


def test_the_inbound_timestamp_is_carried_from_the_message():
    """The device stamps its gesture at the source (Design Rule 4), and that
    time is what Control adds the horizon to. Losing it here would silently
    reintroduce the upward jitter the whole scheme exists to remove."""
    transport, fake = _started()
    fake.deliver("/game/tilt", "sf", ("ie1", 30.0), timestamp=555.5)
    _client, msg = transport.drain_inbound()[0]
    assert msg["timestamp"] == 555.5


def test_an_inbound_blob_is_decoded_back_to_a_value():
    transport, fake = _started()
    fake.deliver("/game/telemetry", "b", ([1, 2, 3],))
    _client, msg = transport.drain_inbound()[0]
    assert msg["args"] == [[1, 2, 3]]


def test_a_message_with_unreadable_args_is_dropped_not_raised():
    """A malformed frame is "drop this frame", never an engine error."""
    transport, fake = _started()
    fake.deliver("/game/tilt", "Z", ())        # 'Z' is not an O2 type
    assert transport.drain_inbound() == []


def test_draining_twice_does_not_repeat_a_message():
    transport, fake = _started()
    fake.deliver("/game/hello", "s", ("ie1",))
    assert len(transport.drain_inbound()) == 1
    assert transport.drain_inbound() == []


def test_drain_new_clients_is_a_noop():
    """o2lite has no connection to accept: a device is anonymous until it
    says /game/hello. agent.py already tolerates an empty list here."""
    transport, _fake = _started()
    assert transport.drain_new_clients() == []


def test_send_addresses_the_device_service_and_carries_the_timestamp():
    transport, fake = _started()
    transport.bind_dev("ie1", object())
    transport.send("ie1", {"address": "/ie1/leds", "typespec": "b",
                           "args": [[0] * 36], "timestamp": 42.5})
    assert len(fake.sent) == 1
    addr, timestamp, typespec, _args = fake.sent[0]
    assert addr == "/ie1/leds"
    assert timestamp == 42.5
    assert typespec == "b"


def test_an_led_list_is_sent_as_a_blob_of_bytes():
    """o2litepy's _add_blob reads x.size and x.data (o2lite.py's blob
    branch), so a bare Python list raises AttributeError on the wire. 36
    ints become 36 bytes."""
    transport, fake = _started()
    transport.bind_dev("ie1", object())
    transport.send("ie1", {"address": "/ie1/leds", "typespec": "b",
                           "args": [[255, 0, 128] * 12], "timestamp": 0.0})
    _addr, _ts, _typespec, args = fake.sent[0]
    blob = args[0]
    assert blob.size == 36
    assert bytes(blob.data)[:3] == b"\xff\x00\x80"


def test_a_role_config_dict_is_sent_as_utf8_json_in_a_blob():
    """The role blob must stay byte-identical to JoinResult.config, so it
    is serialized whole rather than flattened into typed args."""
    import json

    transport, fake = _started()
    transport.bind_dev("ie1", object())
    config = {"bit_name": "TestBit", "role": "player",
              "light_manifest": {"instruments": []}}
    transport.send("ie1", {"address": "/ie1/role", "typespec": "b",
                           "args": [config], "timestamp": 0.0})
    _addr, _ts, _typespec, args = fake.sent[0]
    assert json.loads(bytes(args[0].data).decode("utf-8")) == config


def test_non_blob_args_pass_through_untouched():
    transport, fake = _started()
    transport.bind_dev("ie1", object())
    transport.send("ie1", {"address": "/ie1/deny", "typespec": "ss",
                           "args": ["role full", "try the jam node"],
                           "timestamp": 0.0})
    _addr, _ts, _typespec, args = fake.sent[0]
    assert args == ("role full", "try the jam node")


def test_send_to_an_unbound_dev_is_a_silent_no_op():
    transport, fake = _started()
    transport.send("nobody", {"address": "/nobody/leds", "typespec": "",
                              "args": [], "timestamp": 0.0})
    assert fake.sent == []


def test_a_dev_id_too_long_for_o2_is_refused():
    """o2litepy refuses a service name over 31 characters, and a dev id IS
    the device's service name. Catch it at bind, not at send."""
    transport, _fake = _started()
    try:
        transport.bind_dev("i" * 32, object())
    except ValueError as exc:
        assert "31" in str(exc)
    else:
        raise AssertionError("expected ValueError on an over-long dev id")


def test_an_empty_dev_id_is_refused():
    transport, _fake = _started()
    try:
        transport.bind_dev("", object())
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on an empty dev id")
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_o2_transport.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devicelink.o2_transport'`.

- [ ] **Step 3: Write the implementation**

Create `devicelink/o2_transport.py`:

```python
"""The o2lite-backed device transport: Control's `game` service on the Arco
hub.

Satisfies the same small interface DeviceLinkServer does (drain_new_clients
/ drain_inbound / send / bind_dev / drop_dev), so DeviceLinkAgent is
unchanged by the swap. See docs/superpowers/specs/
2026-08-12-control-o2lite-and-timed-cues-design.md section 5.1.

o2litepy is NEVER imported at module level here. The caller passes an
already-initialized o2lite object into start(), which is how
harness/terrarium_boot.py hands over the connection pyarco owns. That keeps
this module importable, and the offline suite green, with no o2litepy on
the path.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# o2litepy refuses a service name longer than this (o2lite.py:697), and a
# dev id is the device's own service name.
MAX_DEV_LEN = 31


class Blob:
    """Duck-types o2litepy's O2blob.

    o2litepy's _add_blob reads only `.size` and `.data`, so this needs no
    o2litepy import at all -- which is what keeps this module importable,
    and the offline suite green, with no o2litepy on the path.
    """

    __slots__ = ("size", "data")

    def __init__(self, raw: bytes) -> None:
        self.size = len(raw)
        self.data = bytearray(raw)


def to_o2_arg(type_char: str, value):
    """Convert one JSON-envelope argument into what o2litepy's send expects.

    'b' is the one that matters. Passing a Python list (36 LED ints) or a
    dict (the role config) straight through raises AttributeError inside
    _add_blob, which reads x.size and x.data. A list of ints is raw bytes;
    anything else is UTF-8 JSON, which is what the device decodes on the
    other side. Every other type char passes through untouched.
    """
    if type_char != "b":
        return value
    if isinstance(value, (bytes, bytearray)):
        return Blob(bytes(value))
    if isinstance(value, list) and all(isinstance(v, int) for v in value):
        return Blob(bytes(v & 0xFF for v in value))
    return Blob(json.dumps(value).encode("utf-8"))

# The complete services string. set_services REPLACES rather than appends
# (o2litepy o2lite.py:707), and pyarco has already claimed "actl"
# (pyarco/arco_engine.py:98), so Control writes both or silently breaks
# Arco's control replies.
SERVICES = "actl,game"

# Every /game/* verb the agent routes. Registered as full-path handlers so
# o2lite dispatches straight into the drain queue.
GAME_VERBS = ("hello", "join", "tilt", "tap", "shake", "capture", "telemetry")


# Inbound arguments are PULLED off the o2lite object one at a time, in
# typespec order (o2litepy o2lite-api.md, "Values are returned sequentially
# from the message"). There is no prebuilt args list.
_GETTERS = {"s": "get_string", "i": "get_int32", "f": "get_float",
            "d": "get_double", "h": "get_int64", "t": "get_time",
            "b": "get_blob", "B": "get_bool"}


def pull_args(o2lite, typespec: str) -> list:
    """Read one message's arguments off `o2lite`, in typespec order."""
    args = []
    for type_char in typespec:
        getter = _GETTERS.get(type_char)
        if getter is None:
            raise ValueError(f"unsupported O2 type {type_char!r}")
        value = getattr(o2lite, getter)()
        if type_char == "b":
            value = from_o2_arg(value)
        args.append(value)
    return args


def from_o2_arg(blob):
    """Decode an inbound blob back into a list of ints or a JSON value.

    o2litepy's get_blob returns an O2blob with .size and .data (its own doc
    says "as bytes", but the code returns the object -- trust the code).
    LED frames are raw bytes; everything else was written as UTF-8 JSON by
    to_o2_arg above.
    """
    raw = bytes(getattr(blob, "data", blob))[:getattr(blob, "size", None)]
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return list(raw)


class FakeO2Lite:
    """In-process double, sibling of control/audio.py's FakePool. Records
    what was sent and lets a test deliver inbound messages with no hub.

    It reproduces two real o2litepy behaviors that are easy to get wrong:
    a handler takes exactly THREE parameters, and it receives the address
    with its leading '/' already stripped (O2lite_handler.__init__ does
    `self.address = address[1:]`, and _msg_dispatch compares against the
    stripped form).
    """

    def __init__(self, now: float = 100.0) -> None:
        self._now = now
        self.services = ""
        self.sent: list[tuple[str, float, str, tuple]] = []
        self.handlers: dict[str, object] = {}
        self.msg_timestamp = 0.0
        self._pull: list = []

    def time_get(self) -> float:
        return self._now

    def set_time(self, now: float) -> None:
        self._now = now

    def set_services(self, services: str) -> None:
        self.services = services

    def method_new(self, path, typespec, full, handler, info) -> None:
        self.handlers[path] = handler

    def send(self, addr, timestamp, *args) -> None:
        typespec = args[0] if len(args) > 1 else ""
        self.sent.append((addr, timestamp, typespec, tuple(args[1:])))

    def send_cmd(self, addr, timestamp, *args) -> None:
        self.send(addr, timestamp, *args)

    def poll(self) -> None:
        pass

    def deliver(self, address: str, typespec: str, args: tuple,
                timestamp: float = 0.0) -> None:
        """Simulate an inbound message arriving from the hub."""
        handler = self.handlers.get(address)
        if handler is None:
            return
        self.msg_timestamp = timestamp
        self._pull = list(args)
        handler(address[1:], typespec, None)      # leading '/' stripped

    # --- the pull-style getters ------------------------------------------

    def _next(self):
        return self._pull.pop(0)

    def get_string(self):
        return self._next()

    def get_int32(self):
        return self._next()

    def get_int64(self):
        return self._next()

    def get_float(self):
        return self._next()

    def get_double(self):
        return self._next()

    def get_time(self):
        return self._next()

    def get_bool(self):
        return self._next()

    def get_blob(self):
        raw = self._next()
        return Blob(bytes(raw) if isinstance(raw, (bytes, bytearray, list))
                    else raw)


class O2LiteTransport:
    def __init__(self, services: str = SERVICES) -> None:
        self._services = services
        self._o2 = None
        self._inbound: list[tuple[object, dict]] = []
        self._devs: dict[str, object] = {}

    def start(self, o2lite) -> None:
        """Adopt an already-connected o2lite object and claim `game` on it.

        Raises RuntimeError if the clock is not synced: time_get() returns
        -1 before sync, and a cue scheduled against -1 is meaningless.
        """
        now = o2lite.time_get()
        if now < 0:
            raise RuntimeError(
                "o2lite clock is not synchronized (time_get() < 0); "
                "Arco must be clock master before Control offers `game`")
        self._o2 = o2lite
        o2lite.set_services(self._services)
        for verb in GAME_VERBS:
            # typespec None means "match any": a verb's shape is the Bit's
            # business, and GameServer.data already validates it.
            o2lite.method_new(f"/game/{verb}", None, True,
                              self._on_message, None)

    def _on_message(self, address, typespec, info) -> None:
        """o2lite handler.

        THREE parameters, not four: o2litepy calls
        `h.handler(address, types, h.info)` and hands over no args list.
        Arguments are pulled off the o2lite object in typespec order.

        `address` arrives with its leading '/' already stripped, because
        O2lite_handler.__init__ strips it from the registered path and
        _msg_dispatch compares the stripped forms. Re-prefix it so the
        envelope the agent sees is identical to the websocket transport's.
        """
        try:
            args = pull_args(self._o2, typespec or "")
        except Exception:
            logger.exception("dropping /%s: unreadable arguments", address)
            return
        self._inbound.append((None, {
            "timestamp": getattr(self._o2, "msg_timestamp", 0.0),
            "address": f"/{address}",
            "typespec": typespec or "",
            "args": args}))

    # --- the transport interface ------------------------------------------

    def drain_new_clients(self) -> list:
        """No connections to accept: a device is anonymous until it sends
        /game/hello. agent.py:150 already tolerates an empty list."""
        return []

    def drain_inbound(self) -> list:
        drained, self._inbound = self._inbound, []
        return drained

    def bind_dev(self, dev: str, client) -> None:
        if not dev or len(dev) > MAX_DEV_LEN:
            raise ValueError(
                f"dev id {dev!r} is not a valid O2 service name "
                f"(1..{MAX_DEV_LEN} characters)")
        self._devs[dev] = client

    def drop_dev(self, dev: str) -> None:
        self._devs.pop(dev, None)

    def send(self, dev: str, msg: dict) -> None:
        """Send one outbound envelope to `dev`'s own service.

        Unknown dev is a silent no-op, matching DeviceLinkServer: a cue for
        a device that has gone away must never raise into the engine tick.
        """
        if dev not in self._devs or self._o2 is None:
            return
        typespec = msg.get("typespec", "")
        raw_args = msg.get("args", [])
        args = [to_o2_arg(t, v) for t, v in zip(typespec, raw_args)]
        try:
            self._o2.send(msg["address"], msg.get("timestamp", 0.0),
                          typespec, *args)
        except Exception:
            logger.exception("o2lite send to %s failed", dev)

    def stop(self) -> None:
        self._o2 = None
        self._devs.clear()
        self._inbound.clear()
```

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest tests/test_o2_transport.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add devicelink/o2_transport.py tests/test_o2_transport.py
git commit -m "feat(terrarium): add the o2lite device transport offering game"
```

---

### Task 6: Horizon config and the Room's timed light path

Wires the queue into `DeviceLinkAgent` for the Room, and gives the horizon a home.

**Files:**
- Modify: `control/boot_config.py`
- Modify: `devicelink/agent.py` (`__init__`, `_on_light_cue` at line 356, `_render_room` at line 161)
- Test: `tests/test_boot_config.py`, `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `TimedQueue` (Task 2), the widened `_on_light_cue` (Task 1).
- Produces: `BootConfig.cue_horizon: float` and `BootConfig.o2_ensemble: str`; `DeviceLinkAgent(..., clock=..., horizon=...)` and `DeviceLinkAgent.clamped` (an int read by Task 8's bench).

- [ ] **Step 1: Write the failing config test**

Add to `tests/test_boot_config.py`:

```python
def test_cue_horizon_has_a_conservative_default():
    """One installation-wide constant, never a per-cue value: a per-cue
    horizon would let two cues from one gesture land on different frames
    and make the clamp counter uninterpretable. The default must clear one
    44 Hz frame (22.7 ms) with room to spare."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    assert config.cue_horizon >= 0.0227
    assert config.o2_ensemble == "arco"
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_boot_config.py -v -k cue_horizon`
Expected: FAIL with `AttributeError: 'BootConfig' object has no attribute 'cue_horizon'`.

- [ ] **Step 3: Add the fields**

In `control/boot_config.py`, add to the `BootConfig` dataclass:

```python
    # O2 ensemble name Control and pyarco share with the Arco server.
    o2_ensemble: str = "arco"
    # How far ahead of a gesture a cue is scheduled, in seconds. ONE
    # installation-wide constant, never per-cue: a per-cue horizon would let
    # two cues from one gesture land on different frames and would make the
    # clamp counter meaningless. It must clear the 44 Hz frame quantization
    # (22.7 ms) plus Arco's block and buffer latency plus network time.
    # This default is a placeholder to be replaced by a measured figure from
    # harness/sync_bench.py; no venue-box measurement exists, and none of
    # these numbers carry from a dev box to the venue box.
    cue_horizon: float = 0.060
```

- [ ] **Step 4: Write the failing agent test**

Add to `tests/test_devicelink_agent.py`:

```python
def test_a_timed_room_cue_is_withheld_until_its_time():
    """The Room's light must not jump ahead of the audio scheduled for the
    same instant."""
    gs, agent, bridge = _agent_with_bound_room()      # existing helper
    agent._clock = lambda: 100.0

    gs.on_light_cue("sim-room", 0xB0, 74, 100, 100.5)
    agent.poll()
    assert bridge.fed == []

    agent._clock = lambda: 100.5
    agent.poll()
    assert bridge.fed == [(0xB0, 74, 100)]


def test_an_untimed_room_cue_still_applies_on_arrival():
    gs, agent, bridge = _agent_with_bound_room()
    gs.on_light_cue("sim-room", 0xB0, 74, 100)
    agent.poll()
    assert bridge.fed == [(0xB0, 74, 100)]


def test_a_late_room_cue_applies_and_counts_as_clamped():
    gs, agent, bridge = _agent_with_bound_room()
    agent._clock = lambda: 100.0
    gs.on_light_cue("sim-room", 0xB0, 74, 100, 99.0)
    agent.poll()
    assert bridge.fed == [(0xB0, 74, 100)]
    assert agent.clamped == 1
```

Reuse whichever helper `tests/test_devicelink_agent.py` already uses to build an agent with a bound Room and a `FakeRoomLightSink` (the file's Room tests at lines 455 and 511 already construct one). If that construction is inline rather than a helper, extract it to `_agent_with_bound_room()` first and repoint those two tests at it.

- [ ] **Step 5: Run to verify it fails**

Run: `$PY -m pytest tests/test_devicelink_agent.py -v -k "timed_room_cue or clamped"`
Expected: FAIL. The cue is fed immediately, so `bridge.fed` is non-empty on the first poll.

- [ ] **Step 6: Implement**

In `devicelink/agent.py`:

Add to `__init__` (alongside the existing `clock` parameter), taking the horizon so Task 9's driver can pass the configured one:

```python
        self._horizon = horizon
        self._room_cues = TimedQueue()
```

with `horizon: float = 0.0` added to the signature and `from control.timed_queue import TimedQueue` at the top.

Change `_on_light_cue` to queue rather than feed, for the Room branch only:

```python
    def _on_light_cue(self, dev: str, status: int,
                      data1: int, data2: int,
                      when: float | None = None) -> None:
        if dev == self._room_dev and self._room_bridge is not None:
            self._room_cues.push(when, (status, data1, data2),
                                 now=self._clock())
            return
        bridge = self.bridges.get(dev)
        if bridge is None or bridge.session is None:
            return
        try:
            bridge.session.feed_midi(status, data1, data2)
        except Exception:
            logger.exception("feed_midi for %s failed", dev)
```

Drain it at the top of `_render_room` (line 161), before the render:

```python
    def _render_room(self) -> None:
        if self._room_light is None or self._room_dev is None:
            return
        for (status, d1, d2) in self._room_cues.due(self._clock()):
            try:
                self._room_bridge.feed_midi(status, d1, d2)
            except Exception:
                logger.exception("Room feed_midi failed")
        # ... existing render body unchanged ...
```

Add the counter as a property:

```python
    @property
    def clamped(self) -> int:
        """Cues that arrived already late. A rising count means
        BootConfig.cue_horizon is too small."""
        return self._room_cues.clamped
```

- [ ] **Step 7: Run the full suite**

Run: `$PY -m pytest tests -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add control/boot_config.py devicelink/agent.py tests/test_boot_config.py tests/test_devicelink_agent.py
git commit -m "feat(terrarium): hold Room cues until their time on the O2 clock"
```

---

### Task 7: Timestamped LED frames

A joined device lights up at T rather than on arrival. This is the device half of the same property Task 6 gave the Room.

**Files:**
- Modify: `devicelink/protocol.py:86`
- Modify: `devicelink/agent.py` (`_render_frames`, around line 224)
- Modify: `harness/shroom_client.py` (`_on_leds` at line 120, `handle` at line 84)
- Test: `tests/test_devicelink_protocol.py`, `tests/test_devicelink_frames.py`, `tests/test_shroom_client.py`

**Interfaces:**
- Consumes: `TimedQueue` (Task 2).
- Produces: `protocol.leds_event(dev, channels, when: float = 0.0)`; `ShroomClient.tick(now: float) -> None` and `ShroomClient.clamped -> int`. The constructor signature is unchanged: the client reads "now" from the most recent `tick`, so it stays socket-free and needs no clock injected. Task 8 drives `tick`.

- [ ] **Step 1: Write the failing protocol test**

Add to `tests/test_devicelink_protocol.py`:

```python
def test_leds_event_carries_a_display_time():
    event = protocol.leds_event("ie1", [0] * 36, when=42.5)
    assert event["timestamp"] == 42.5
    assert event["address"] == "/ie1/leds"


def test_leds_event_defaults_to_no_declared_time():
    """Zero keeps the pre-timing behavior: display on arrival."""
    event = protocol.leds_event("ie1", [0] * 36)
    assert event["timestamp"] == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_devicelink_protocol.py -v -k leds_event_carries`
Expected: FAIL with `TypeError: leds_event() got an unexpected keyword argument 'when'`.

- [ ] **Step 3: Implement**

In `devicelink/protocol.py`, replace `leds_event` (line 86):

```python
def leds_event(dev: str, channels, when: float = 0.0) -> dict:
    """channels: a flat sequence of 36 ints (12 pixels x GRB).

    `when` is an absolute O2 time at which the device should display this
    frame. 0.0 means no declared time: display on arrival, the pre-timing
    behavior. Control renders every joined device's light and ships finished
    frames, so the device schedules a FRAME, not MIDI -- see the design
    spec section 5.3.
    """
    return _event(f"/{dev}/leds", "b", [list(channels)], timestamp=when)
```

Change the `_event` helper (line 71) to accept the timestamp:

```python
def _event(address: str, typespec: str, args: list,
           timestamp: float = 0.0) -> dict:
    return encode(Envelope(timestamp=timestamp, address=address,
                           typespec=typespec, args=args))
```

- [ ] **Step 4: Stamp frames in the agent**

In `devicelink/agent.py`, find the `_render_frames` send at line 224 and add the horizon:

```python
                    self._send(dev, protocol.leds_event(
                        dev, frame, when=self._clock() + self._horizon))
```

- [ ] **Step 5: Write the failing client test**

Create or add to `tests/test_shroom_client.py`:

```python
from harness.shroom_client import ShroomClient


class FakeLeds:
    def __init__(self):
        self.shown = []
        self.cleared = 0

    def show(self, frame):
        self.shown.append(frame)

    def clear(self):
        self.cleared += 1


def test_a_timestamped_frame_is_held_until_its_time():
    leds = FakeLeds()
    client = ShroomClient("ie1", "node-a", leds=leds)

    client.handle({"timestamp": 10.0, "address": "/ie1/leds",
                   "typespec": "b", "args": [[7] * 36]})
    client.tick(now=9.9)
    assert leds.shown == []

    client.tick(now=10.0)
    assert len(leds.shown) == 1


def test_an_unstamped_frame_shows_on_the_next_tick():
    """timestamp 0.0 means no declared time, and must not be treated as a
    time far in the past that trips the clamp counter."""
    leds = FakeLeds()
    client = ShroomClient("ie1", "node-a", leds=leds)

    client.handle({"timestamp": 0.0, "address": "/ie1/leds",
                   "typespec": "b", "args": [[7] * 36]})
    client.tick(now=500.0)
    assert len(leds.shown) == 1
    assert client.clamped == 0


def test_a_frame_whose_time_has_passed_shows_immediately_and_clamps():
    leds = FakeLeds()
    client = ShroomClient("ie1", "node-a", leds=leds)

    client.handle({"timestamp": 5.0, "address": "/ie1/leds",
                   "typespec": "b", "args": [[7] * 36]})
    client.tick(now=9.0)
    assert len(leds.shown) == 1
    assert client.clamped == 1


def test_release_still_clears_immediately():
    """A release must not sit in the queue behind a pending frame: the
    device is being torn down."""
    leds = FakeLeds()
    client = ShroomClient("ie1", "node-a", leds=leds)
    client.handle({"timestamp": 0.0, "address": "/ie1/release",
                   "typespec": "", "args": []})
    assert leds.cleared == 1
    assert client.released is True
```

- [ ] **Step 6: Run to verify it fails**

Run: `$PY -m pytest tests/test_shroom_client.py -v`
Expected: FAIL with `AttributeError: 'ShroomClient' object has no attribute 'tick'`.

- [ ] **Step 7: Implement in the client**

In `harness/shroom_client.py`, add `from control.timed_queue import TimedQueue` and change `__init__` to build one:

```python
    def __init__(self, dev: str, node: str, leds=None,
                 on_role: Callable[[dict], None] | None = None) -> None:
        self.dev = dev
        self.node = node
        self.leds = leds
        self.on_role = on_role
        self.config: dict | None = None
        self.released = False
        self.last_deny: tuple[str, str] | None = None
        self.last_error: tuple[str, str] | None = None
        # Frames wait here until their declared display time. Control
        # renders; this client only decides WHEN to light up.
        self._frames = TimedQueue()
```

Change `_on_leds` (line 120) to queue instead of showing, and add `tick`:

```python
    def _on_leds(self, env) -> str:
        if not env.args or not isinstance(env.args[0], list):
            logger.debug("dropping /leds with a non-list payload")
            return ""
        channels = env.args[0]
        if len(channels) != LED_CHANNELS:
            logger.debug("dropping /leds with %d channels", len(channels))
            return ""
        frame = bytes(int(v) & 0xFF for v in channels)
        # timestamp 0.0 means "no declared time"; None is what TimedQueue
        # reads as that, and it must NOT count as a clamp.
        when = env.timestamp if env.timestamp else None
        self._frames.push(when, frame, now=self._now())
        return env.address

    def tick(self, now: float) -> None:
        """Light up any frame whose time has arrived. Driven by the client's
        own loop; on a synced device `now` is o2lite.time_get()."""
        self._last_now = now
        for frame in self._frames.due(now):
            if self.leds is not None:
                self.leds.show(frame)

    def _now(self) -> float:
        """The most recent time tick() was given.

        Deliberately not a clock of its own: this module is socket-free and
        clock-free by design, so that handle() stays testable with neither.
        The tick loop runs far faster than frames arrive, so this is at most
        one iteration stale, which can only shift a borderline clamp by one
        tick. Before the first tick it is 0.0, so a frame arriving that
        early is held until its absolute O2 time, which is correct.
        """
        return getattr(self, "_last_now", 0.0)

    @property
    def clamped(self) -> int:
        return self._frames.clamped
```

`_on_release` stays exactly as it is: a release clears immediately rather than queueing, because the device is being torn down.

- [ ] **Step 8: Run the full suite**

Run: `$PY -m pytest tests -v`
Expected: PASS. `tests/test_devicelink_frames.py` may assert on frame contents rather than timing; if a test there breaks because frames are now held, drive `client.tick(now)` in it rather than weakening the assertion.

- [ ] **Step 9: Commit**

```bash
git add devicelink/protocol.py devicelink/agent.py harness/shroom_client.py tests/test_devicelink_protocol.py tests/test_shroom_client.py tests/test_devicelink_frames.py
git commit -m "feat(terrarium): display device frames at their declared time"
```

---

### Task 8: The Python simulated Tuneshroom

The acceptance vehicle: a clock-synced o2lite device that drives the whole path with no Dart and no browser build.

**Files:**
- Create: `harness/o2_shroom.py`
- Test: `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: `ShroomClient` + `tick` (Task 7), `O2LiteTransport`/`FakeO2Lite` (Task 5).
- Produces: `build(dev, node, sim_host, sim_port, serve) -> (client, backend)` and `tilt_sweep(elapsed: float) -> float`. Task 9's bench imports `tilt_sweep`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_o2_shroom.py`:

```python
from harness.o2_shroom import tilt_sweep


def test_tilt_sweep_stays_in_range():
    """gamma is degrees in [-90, 90]: TestBit._on_tilt clamps to that, and a
    sweep that relied on the clamp would silently flatten at both ends."""
    for step in range(0, 400):
        value = tilt_sweep(step * 0.05)
        assert -90.0 <= value <= 90.0


def test_tilt_sweep_reverses_rather_than_jumping():
    """A ping-pong ramp, not a sawtooth: aurora glides its hue under cc:74,
    and a wrap-around discontinuity reads as a visible snap."""
    samples = [tilt_sweep(step * 0.05) for step in range(0, 200)]
    biggest_step = max(abs(b - a) for a, b in zip(samples, samples[1:]))
    assert biggest_step < 20.0


def test_tilt_sweep_is_periodic():
    """One full period returns to where it started, so the sweep closes its
    loop cleanly rather than drifting. Also pins it as a deterministic
    function of elapsed time: a random walk would make every acceptance run
    a judgement call."""
    from harness.o2_shroom import SWEEP_PERIOD

    assert tilt_sweep(0.0) == tilt_sweep(SWEEP_PERIOD)
    assert tilt_sweep(1.25) == tilt_sweep(1.25 + SWEEP_PERIOD)
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_o2_shroom.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.o2_shroom'`.

- [ ] **Step 3: Write the module**

Create `harness/o2_shroom.py`:

```python
"""python -m harness.o2_shroom -- a simulated Tuneshroom over real o2lite.

The acceptance vehicle for docs/superpowers/specs/
2026-08-12-control-o2lite-and-timed-cues-design.md: a clock-synced O2
device that joins TEST_PLAYER_NODE, drives one gesture, and displays its
frames at their declared time.

It reuses harness/shroom_client.py's ShroomClient unmodified for the
protocol surface -- that module's docstring already anticipated this, since
its transport half lives in main() precisely because o2lite replaces it.

Trap worth knowing: TestBit's `player` is a SCORED role, and
RegistrationState.join() refuses a scored role once the Bit is RUNNING. The
driver must hold in SETUP long enough for this client to join, exactly as
harness/devicelink_smoke.py's --setup-seconds already does.

Usage (needs a running Arco and PYTHONPATH=/Users/chris/projects/arco):
    python3 -m harness.o2_shroom --dev ie1 --node TEST_PLAYER_NODE
"""

from __future__ import annotations

import math

from harness.shroom_client import ShroomClient

# Degrees. TestBit._on_tilt clamps gamma to [-90, 90] and maps it onto
# cc:74, which `player` binds to aurora's hue lane.
SWEEP_DEGREES = 90.0
# Seconds for one full there-and-back sweep. Slow enough to watch the hue
# glide rather than strobe.
SWEEP_PERIOD = 8.0


def tilt_sweep(elapsed: float) -> float:
    """A deterministic ping-pong ramp over [-90, 90] degrees.

    A triangle wave rather than a sawtooth: aurora glides its hue under
    cc:74, so a wrap-around discontinuity reads as a visible snap. Same
    shape as led_smoke.py's canned cc:74 ramp, which is what proved this
    looks right.
    """
    phase = (elapsed % SWEEP_PERIOD) / SWEEP_PERIOD
    triangle = 2.0 * abs(2.0 * (phase - math.floor(phase + 0.5)))
    return SWEEP_DEGREES * (triangle - 1.0)


def build(dev: str, node: str = "TEST_PLAYER_NODE",
          sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True):
    """Construct the client and its LED backend WITHOUT opening a socket.

    Returns (client, backend). serve=False gives a record-only backend for
    headless tests, matching led_smoke.py's and room_simulator.py's
    build()/main() split.
    """
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import shroom_capability

    from harness.room_simulator import WebSimLeds

    backend = WebSimBackend(capability=shroom_capability(),
                            host=sim_host, port=sim_port, serve=serve)
    client = ShroomClient(dev, node, leds=WebSimLeds(backend))
    return client, backend


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", default="ie1")
    parser.add_argument("--node", default="TEST_PLAYER_NODE")
    parser.add_argument("--ensemble", default="arco")
    parser.add_argument("--sim-host", default="127.0.0.1")
    parser.add_argument("--sim-port", type=int, default=0)
    parser.add_argument("--tilt-hz", type=float, default=20.0)
    parser.add_argument("--no-join", action="store_true",
                        help="Send /game/hello but never /game/join, and "
                             "emit no gestures. This is what the Room "
                             "simulator needs: Control has already recorded "
                             "this dev as the bound Room before the process "
                             "is spawned, so there is no node to tap "
                             "(harness/room_simulator.py's rule, reused).")
    args = parser.parse_args()

    # Lazy, exactly like harness/arco_synth.py: this module must import with
    # no o2litepy on the path.
    from o2litepy import o2lite

    from devicelink.o2_transport import pull_args

    client, backend = build(args.dev, args.node,
                            args.sim_host, args.sim_port)
    backend.open()
    print(f"Watch the Shroom at http://{args.sim_host}:{backend.port}/")

    o2lite.initialize(args.ensemble)
    o2lite.set_services(args.dev)          # the device offers its own ie<N>

    def on_down(address, typespec, info):
        """o2litepy handler: THREE parameters, and `address` has already had
        its leading '/' stripped. Arguments are pulled in typespec order,
        not handed over as a list."""
        try:
            values = pull_args(o2lite, typespec or "")
        except Exception:
            return                          # drop the frame, never raise
        client.handle({"timestamp": o2lite.msg_timestamp,
                       "address": f"/{address}",
                       "typespec": typespec or "", "args": values})

    for kind in ("role", "leds", "release", "deny", "error"):
        o2lite.method_new(f"/{args.dev}/{kind}", None, True, on_down, None)

    while o2lite.time_get() < 0:           # block until clock sync
        o2lite.poll()
        time.sleep(0.01)
    print(f"clock synced at {o2lite.time_get():.3f}")

    o2lite.send_cmd("/game/hello", 0, "s", args.dev)
    if not args.no_join:
        o2lite.send_cmd("/game/join", 0, "ss", args.dev, args.node)

    start = o2lite.time_get()
    interval = 1.0 / args.tilt_hz
    next_tilt = start
    try:
        while not client.released:
            o2lite.poll()
            now = o2lite.time_get()
            if not args.no_join and now >= next_tilt:
                gamma = tilt_sweep(now - start)
                # Timestamps at the source (Design Rule 4): the device's own
                # synced clock reading, not Control's receipt time.
                o2lite.send("/game/tilt", now, "sf", args.dev, gamma)
                next_tilt += interval
            client.tick(now)
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"frames displayed late: {client.clamped}")
        backend.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest tests/test_o2_shroom.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Verify the module imports with no o2litepy**

Run: `python -c "import harness.o2_shroom; print('ok')"`
Expected: prints `ok`. If it raises `ModuleNotFoundError: No module named 'o2litepy'`, an import escaped out of `main()` and must be moved back inside it.

- [ ] **Step 6: Commit**

```bash
git add harness/o2_shroom.py tests/test_o2_shroom.py
git commit -m "feat(terrarium): add the o2lite simulated Tuneshroom"
```

---

### Task 9: `sync_bench` and the boot wiring

Closes the loop: the driver runs the o2lite transport, and the bench reports what alignment actually looks like on this box.

**Files:**
- Create: `harness/sync_bench.py`
- Modify: `harness/terrarium_boot.py` (`build` at line 51, `main` at line 114)
- Test: `tests/test_sync_bench.py`, `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `summarise(deltas: list[float]) -> dict` with keys `count`, `mean_ms`, `p95_ms`, `worst_ms`.

- [ ] **Step 1: Write the failing bench tests**

Create `tests/test_sync_bench.py`:

```python
from harness.sync_bench import summarise


def test_summarise_reports_worst_and_p95_not_just_mean():
    """render_bench.py's lesson, applied to sync: a path that averages well
    while missing badly once a second reads as healthy and is not."""
    deltas = [0.001] * 99 + [0.200]
    stats = summarise(deltas)
    assert stats["count"] == 100
    assert stats["worst_ms"] == 200.0
    assert stats["mean_ms"] < 5.0
    assert stats["p95_ms"] < stats["worst_ms"]


def test_summarise_uses_absolute_deltas():
    """Light landing 10 ms EARLY is as wrong as 10 ms late."""
    stats = summarise([-0.010, 0.010])
    assert stats["worst_ms"] == 10.0


def test_summarise_of_nothing_is_empty_not_an_error():
    stats = summarise([])
    assert stats["count"] == 0
    assert stats["worst_ms"] == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_sync_bench.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.sync_bench'`.

- [ ] **Step 3: Write the bench**

Create `harness/sync_bench.py`:

```python
"""Measure how closely the audio call and the LED frame land on one cue.

Reports worst and p95 alongside the mean, for the same reason
harness/render_bench.py does: a path that averages 2 ms while missing by
200 ms once a second reads as healthy and is not.

EVERY FIGURE THIS PRODUCES IS A DEV-BOX FIGURE. The venue target is
bare-metal Linux on a Raspberry Pi 5 relaying every hop through the same
process doing all room synthesis while feeding a 44 Hz render loop. No
venue-box measurement exists; the box does not exist. Do not quote these
numbers as venue latency, and do not derive BootConfig.cue_horizon for a
venue from them.

summarise() takes no luxaeterna and no pyarco dependency, so it runs in the
core offline suite.
"""

from __future__ import annotations


def summarise(deltas: list[float]) -> dict:
    """Reduce signed second-deltas (audio time minus light time) to stats.

    Absolute values throughout: light landing 10 ms early is as wrong as 10
    ms late, and signed averaging would let the two cancel into a
    flattering zero.
    """
    if not deltas:
        return {"count": 0, "mean_ms": 0.0, "p95_ms": 0.0, "worst_ms": 0.0}
    magnitudes = sorted(abs(d) * 1000.0 for d in deltas)
    p95_index = min(len(magnitudes) - 1, int(len(magnitudes) * 0.95))
    return {
        "count": len(magnitudes),
        "mean_ms": sum(magnitudes) / len(magnitudes),
        "p95_ms": magnitudes[p95_index],
        "worst_ms": magnitudes[-1],
    }
```

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest tests/test_sync_bench.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Write the failing boot-wiring test**

Add to `tests/test_terrarium_boot.py`:

```python
def test_build_passes_the_configured_horizon_to_the_agent():
    """The horizon lives in one place. An agent built with its own default
    would silently disagree with the audio path's scheduling."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit",
                        cue_horizon=0.075)
    gs, server, agent, arco, sim = _build_with_fakes(config)   # existing helper
    try:
        assert agent._horizon == 0.075
    finally:
        shutdown(gs, agent, arco, sim)
```

Use whichever fake-injecting helper `tests/test_terrarium_boot.py` already has for `build()` (it injects `arco_process_cls`, `simulator_popen`, and `room_audio=AudioBridge(FakePool())`). If there is no named helper, extract one from the file's existing build test first.

- [ ] **Step 6: Run to verify it fails**

Run: `$PY -m pytest tests/test_terrarium_boot.py -v -k horizon`
Expected: FAIL with `AttributeError` or an assertion mismatch, since `build()` does not pass a horizon.

- [ ] **Step 7: Wire it**

In `harness/terrarium_boot.py`, change the `DeviceLinkAgent` construction in `build()` (line 84):

```python
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge,
                            room_audio=room_audio,
                            horizon=config.cue_horizon)
```

Add a `--horizon` flag to `main()` so the measured value can be tried without an edit:

```python
    ap.add_argument("--horizon", type=float, default=None,
                    help="Cue scheduling horizon in seconds. Default: "
                         "BootConfig.cue_horizon. Measure with "
                         "python -m harness.sync_bench.")
```

and apply it when building the config:

```python
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    if args.horizon is not None:
        config.cue_horizon = args.horizon
```

- [ ] **Step 8: Run the suite**

Run: `$PY -m pytest tests -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add harness/sync_bench.py harness/terrarium_boot.py tests/test_sync_bench.py tests/test_terrarium_boot.py
git commit -m "feat(terrarium): add sync_bench and wire the cue horizon through boot"
```

---

### Task 10: Run the driver on o2lite

The step that makes success criterion 1 reachable. Until this lands, the
o2lite transport exists but nothing constructs it, and `build()` still opens
a websocket `DeviceLinkServer`.

**Files:**
- Modify: `harness/terrarium_boot.py` (`build` at line 51, `main` at line 114)
- Modify: `control/arco_process.py`
- Test: `tests/test_terrarium_boot.py`, `tests/test_arco_process.py`

**Interfaces:**
- Consumes: `O2LiteTransport` / `FakeO2Lite` (Task 5), `harness.o2_shroom --no-join` (Task 8), `BootConfig.o2_ensemble` and `cue_horizon` (Task 6).
- Produces: `build(..., transport=None)`, `_O2SimulatorFactory(ensemble, popen=...)`, and `ArcoProcess.poll() -> int | None`. Nothing later depends on these; this is the last task.

- [ ] **Step 1: Write the failing transport-swap test**

Add to `tests/test_terrarium_boot.py`:

```python
def test_build_can_run_the_agent_on_the_o2lite_transport():
    """The whole point of the slice: device traffic crosses the Arco hub.
    A FakeO2Lite stands in for the connection pyarco owns, so this asserts
    the wiring with no Arco and no o2litepy."""
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport

    fake = FakeO2Lite()
    transport = O2LiteTransport()
    transport.start(fake)

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, sim = _build_with_fakes(config,
                                                     transport=transport)
    try:
        assert agent.server is transport
        assert fake.services == "actl,game"
    finally:
        shutdown(gs, agent, arco, sim)
```

Extend `_build_with_fakes` to forward a `transport=` keyword to `build()`.

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/test_terrarium_boot.py -v -k o2lite_transport`
Expected: FAIL with `TypeError: build() got an unexpected keyword argument 'transport'`.

- [ ] **Step 3: Accept an injected transport in `build()`**

In `harness/terrarium_boot.py`, add `transport=None` to `build()`'s signature and use it in place of the websocket server when supplied:

```python
    if transport is None:
        server = DeviceLinkServer(host=host, port=port)
        server.start()
    else:
        # o2lite mode: there is no socket to listen on. The connection is
        # pyarco's, already clock-synced by arco.initialize(), and the
        # caller started the transport on it.
        server = transport
```

Guard the simulator factory, which builds a websocket URL that means nothing
in o2lite mode:

```python
    if transport is None:
        factory = _SimulatorFactory(f"ws://{host}:{server.port}/ws",
                                    popen=simulator_popen)
    else:
        factory = _O2SimulatorFactory(config.o2_ensemble,
                                      popen=simulator_popen)
```

and add the o2lite factory beside the existing one:

```python
class _O2SimulatorFactory:
    """Spawns the Room simulator as an o2lite client rather than a
    websocket one. Reuses harness/o2_shroom.py with --no-join: Control has
    already recorded this dev as the bound Room before the process is
    spawned, so there is no Registration Node to tap -- the same rule
    harness/room_simulator.py follows."""

    def __init__(self, ensemble: str, *, popen=subprocess.Popen) -> None:
        self._ensemble = ensemble
        self._popen = popen
        self.process: SimulatorProcess | None = None

    def __call__(self) -> str:
        self.process = SimulatorProcess(
            [sys.executable, "-m", "harness.o2_shroom",
             "--dev", SIM_DEV, "--ensemble", self._ensemble, "--no-join"],
            popen=self._popen)
        self.process.start()
        return SIM_DEV
```

- [ ] **Step 4: Give `main()` a transport switch**

```python
    ap.add_argument("--transport", choices=("websocket", "o2lite"),
                    default="websocket",
                    help="websocket: the JSON devicelink shim, no Arco in "
                         "the device path. o2lite: real O2 through the Arco "
                         "hub, which requires a running Arco server.")
```

and in `main()`, before `build()`:

```python
    transport = None
    if args.transport == "o2lite":
        from o2litepy import o2lite            # lazy: websocket mode needs no o2litepy

        from devicelink.o2_transport import O2LiteTransport
        # pyarco's ArcoSynthPool.start() runs arco.initialize(), which
        # connects o2lite and blocks until clock sync. build() does that
        # while constructing room_audio, so the transport is started after
        # build() returns rather than before it.
        transport = O2LiteTransport()
```

Because the connection does not exist until `build()` has constructed the
audio pool, start the transport immediately after `build()` returns:

```python
    gs, server, agent, arco, simulator = build(...)
    if transport is not None:
        transport.start(o2lite)               # raises if the clock is unsynced
```

- [ ] **Step 5: Handle Arco dying mid-run**

Spec section 6: fail loud, because silent degradation in a venue is worse
than a visible stop. In `main()`'s tick loop:

```python
        while True:
            if arco.poll() is not None:       # subprocess exited
                print("Arco exited; aborting the Bit", file=sys.stderr)
                break
            agent.poll()
            gs.tick(1.0 / 44.0)
            time.sleep(1.0 / 44.0)
```

Add `poll()` to `ArcoProcess` in `control/arco_process.py` if it is absent,
delegating to the subprocess handle:

```python
    def poll(self):
        """None while the server is still running, else its exit code."""
        return None if self._process is None else self._process.poll()
```

Add the matching test to `tests/test_arco_process.py`:

```python
def test_poll_reports_a_dead_subprocess():
    class DeadPopen:
        def poll(self):
            return 1

    proc = ArcoProcess(["fake"], popen=lambda *a, **k: DeadPopen())
    proc.start()
    assert proc.poll() == 1
```

Use whatever injection point `ArcoProcess` already offers for `popen`; the
file's existing tests show the convention.

- [ ] **Step 6: Run the full suite**

Run: `$PY -m pytest tests -v`
Expected: PASS, every test.

- [ ] **Step 7: Verify the offline guarantee still holds**

Run: `python -c "import control.audio, control.timed_queue, control.engine, control.boot, devicelink.o2_transport, devicelink.agent, harness.o2_shroom, harness.terrarium_boot; import sys; bad=[m for m in sys.modules if m.startswith(('o2litepy','pyarco'))]; print('leaked:', bad); assert not bad"`
Expected: prints `leaked: []` and exits 0. This is the constraint the whole plan is built around; if it fails, an import escaped module scope.

- [ ] **Step 8: Commit**

```bash
git add harness/sync_bench.py harness/terrarium_boot.py control/arco_process.py tests/test_sync_bench.py tests/test_terrarium_boot.py tests/test_arco_process.py
git commit -m "feat(terrarium): run Control's game service on o2lite end to end"
```

---

## Manual verification

These need a live Arco and are not part of the suite. Run them from the repo root with `PYTHONPATH=/Users/chris/projects/arco`.

**Restart the Arco server before each run.** `pyarco`'s `arco.initialize()` unconditionally calls `reset()`, which sends `/host/clear`, tearing down the audio stream. Only the first client after a server start gets working audio on macOS. This is upstream in Arco and not fixable here.

**RUN ON: MYCOLOGICAL (this dev box)**

Terminal 1, the Arco server (a curses app, start it by hand):

```bash
/Users/chris/projects/arco/apps/pytest/server
```

Terminal 2, the Terrarium on the o2lite transport:

```bash
cd /Users/chris/projects/mm-terrarium && PYTHONPATH=/Users/chris/projects/arco python -m harness.terrarium_boot --transport o2lite --setup-seconds 30
```

Terminal 3, the simulated Tuneshroom:

```bash
cd /Users/chris/projects/mm-terrarium && PYTHONPATH=/Users/chris/projects/arco python -m harness.o2_shroom --dev ie1
```

Expected: terminal 3 prints a `clock synced at <positive number>` line and a browser URL. Opening it shows a 12-LED Shroom whose hue glides back and forth on the sweep. The Room's own drone starts on RUNNING. On exit, `frames displayed late: 0`; a non-zero count means `cue_horizon` is too small for this box.

## Open items this plan does not close

Carried from the spec's section 8, and worth restating so nobody reads a green suite as a finished question:

- **The horizon default of 0.060 s is a placeholder, not a measurement.** `sync_bench` produces the real figure for this box, and even that does not carry to the venue box.
- **Arco is clock master only once its audio is up** (`arco.cpp:1295`). The `start()` assertion in Task 5 makes the failure visible rather than silent, but the coupling is upstream.
- **Nothing yet drives the Room's light during a live run.** Task 6 makes Room cues arrive on time; no Bit emits one. `TestBit`'s handlers still address only the calling device, and `Bit.update(dt)` still has no cue-emission mechanism at all.
