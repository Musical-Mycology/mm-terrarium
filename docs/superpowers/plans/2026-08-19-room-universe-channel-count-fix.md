# Room Universe Channel-Count Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the DEMO Room's light from crashing on every render tick by widening luxaeterna's `Universe` buffer to fit a Room profile wider than 512 channels, then using that width in mm-terrarium's Room render buffer.

**Architecture:** Two repos, two small additive edits, no new abstractions. luxaeterna's `Universe` gains an optional `channel_count` constructor parameter (default unchanged at 512, so every existing caller is unaffected); mm-terrarium's `devicelink/agent.py` passes the Room profile's real channel count when building the Room's private render buffer. A regression test in each repo pins the fix; a live-verify run against a real Arco confirms it end to end.

**Tech Stack:** Python 3.14, pytest. luxaeterna is a sibling repo (`~/projects/luxaeterna`) consumed by mm-terrarium as a live editable pip install — edits there take effect immediately, no version bump or republish step.

## Global Constraints

- luxaeterna's `Universe` default behavior (zero-arg construction = exactly 512 channels) must not change — every existing caller across both repos relies on it.
- `self._room_light.universe` (mm-terrarium `devicelink/agent.py`) is never handed to any luxaeterna output backend (Art-Net/sACN/serial-Enttec) or `OutputLoop` — it is a private buffer read only inside `_render_room()`. Nothing in this plan touches those backends.
- luxaeterna change: commit directly to `main` at `~/projects/luxaeterna` (no branch/PR — confirmed with the user; it's small, additive, and backward-compatible).
- mm-terrarium change: normal branch + PR flow on this repo's current branch (`claude/lucid-curran-0613ef`).
- Run luxaeterna's suite with `~/projects/luxaeterna/.venv/bin/python -m pytest`. Run mm-terrarium's suite with `.venv/bin/python -m pytest tests -v` from the worktree root (there is no bare `python` on these boxes — see `docs/MM_TERRARIUM.md`).
- Full spec: `docs/superpowers/specs/2026-08-19-room-universe-channel-count-fix-design.md`.

---

### Task 1: Widen luxaeterna's `Universe` to accept an optional `channel_count`

**Repo:** `~/projects/luxaeterna` (sibling repo, NOT this mm-terrarium worktree — `cd` there for every step in this task).

**Files:**
- Modify: `luxaeterna/universe.py`
- Test: `tests/test_universe.py` (new file — no test file for `Universe` exists today; it's currently only exercised indirectly via `tests/test_output_hook.py`, `tests/test_universeset.py`, `tests/synth/*`)

**Interfaces:**
- Produces: `Universe.__init__(self, universe_id: int = 0, channel_count: int = DMX_CHANNELS) -> None`. `channel_count` is stored as `self._channel_count` and governs every bounds check and the initial/reset allocation size. `__len__()` returns `self._channel_count`. Every other public method (`set`, `get`, `set_range`, `fill`, `get_frame`, `reset`, `dirty`, `__getitem__`, `__setitem__`, `__repr__`) keeps its existing signature.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_universe.py`:

```python
"""Universe: a single DMX buffer, optionally wider than one 512-channel wire universe."""

from __future__ import annotations

import pytest

from luxaeterna.constants import DMX_CHANNELS
from luxaeterna.exceptions import ChannelError
from luxaeterna.universe import Universe


def test_default_construction_is_exactly_512_channels():
    universe = Universe()
    assert len(universe) == DMX_CHANNELS


def test_default_construction_rejects_a_range_past_512():
    universe = Universe()
    with pytest.raises(ChannelError):
        universe.set_range(0, bytes(DMX_CHANNELS + 1))


def test_wide_universe_accepts_a_range_a_default_universe_would_reject():
    universe = Universe(channel_count=2592)
    universe.set_range(0, bytes(2592))   # must not raise
    assert len(universe) == 2592


def test_wide_universe_still_rejects_a_range_past_its_own_bound():
    universe = Universe(channel_count=2592)
    with pytest.raises(ChannelError):
        universe.set_range(0, bytes(2593))


def test_wide_universe_get_frame_returns_its_own_full_width():
    universe = Universe(channel_count=2592)
    universe.set_range(0, bytes([7]) * 2592)
    frame = universe.get_frame()
    assert len(frame) == 2592
    assert frame == bytearray([7]) * 2592


def test_wide_universe_fill_defaults_to_its_own_width():
    universe = Universe(channel_count=2592)
    universe.fill(9)   # no start/count -- must fill the whole 2592, not 512
    frame = universe.get_frame()
    assert len(frame) == 2592
    assert all(b == 9 for b in frame)


def test_wide_universe_reset_reallocates_at_its_own_width():
    universe = Universe(channel_count=2592)
    universe.set_range(0, bytes([5]) * 2592)
    universe.reset()
    frame = universe.get_frame()
    assert len(frame) == 2592
    assert all(b == 0 for b in frame)


def test_wide_universe_set_and_get_a_single_channel_past_512():
    universe = Universe(channel_count=2592)
    universe.set(1000, 42)
    assert universe.get(1000) == 42


def test_wide_universe_rejects_a_single_channel_past_its_own_bound():
    universe = Universe(channel_count=2592)
    with pytest.raises(ChannelError):
        universe.get(2592)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/projects/luxaeterna && .venv/bin/python -m pytest tests/test_universe.py -v`
Expected: the four `default_construction` tests PASS (current behavior already matches); every `wide_universe` test FAILS, either with `ChannelError: Range ... exceeds universe bounds` (for `set_range`/`fill`/`set`/`get`) or a length assertion failure (for `get_frame`/`reset`) — because `Universe` has no `channel_count` parameter yet, so `channel_count=2592` currently raises `TypeError: __init__() got an unexpected keyword argument 'channel_count'` for all five `wide_universe` tests. Confirm the `TypeError` is what you see.

- [ ] **Step 3: Implement `channel_count`**

Read `luxaeterna/universe.py` in full first (it's ~108 lines). Then apply this diff, in full — every reference to the module-level `DMX_CHANNELS` constant inside the class body becomes `self._channel_count`, and `_channel_count` is added to `__slots__`:

```python
class Universe:
    """A single DMX512 universe (512 channels) — or, when constructed with a
    non-default channel_count, a caller-managed internal render buffer of
    that width (e.g. a simulator rendering a wider-than-512-channel logical
    surface before slicing it up itself). Real DMX-512 wire universes -- the
    Art-Net/sACN/serial-Enttec backends -- still assume exactly 512 channels
    and are unaffected by a non-default channel_count, since this class never
    hands itself to them; only the caller that constructed it does that.

    All channel values are stored in a flat bytearray for minimal
    allocation overhead. A lightweight lock protects concurrent writes
    from the application thread and reads from the output thread.
    """

    __slots__ = ("universe_id", "_channel_count", "_data", "_lock", "_dirty")

    def __init__(self, universe_id: int = 0, channel_count: int = DMX_CHANNELS) -> None:
        self.universe_id = universe_id
        self._channel_count = channel_count
        self._data = bytearray(channel_count)
        self._lock = threading.Lock()
        self._dirty = True  # Flag for output loop optimisation

    # --- single-channel ops ---

    def set(self, channel: int, value: int) -> None:
        """Set a single channel (0-511) to *value* (0-255)."""
        if not (0 <= channel < self._channel_count):
            raise ChannelError(f"Channel {channel} out of range 0-{self._channel_count - 1}")
        with self._lock:
            self._data[channel] = value & 0xFF
            self._dirty = True

    def get(self, channel: int) -> int:
        """Read a single channel value."""
        if not (0 <= channel < self._channel_count):
            raise ChannelError(f"Channel {channel} out of range 0-{self._channel_count - 1}")
        return self._data[channel]

    # --- bulk ops (fast path for pixel strips / multi-channel fixtures) ---

    def set_range(self, start: int, values: bytes | bytearray | Sequence[int]) -> None:
        """Set a contiguous range of channels starting at *start*.

        Uses slice assignment on bytearray — significantly faster than
        looping ``set()`` for multi-channel writes.
        """
        end = start + len(values)
        if start < 0 or end > self._channel_count:
            raise ChannelError(f"Range {start}:{end} exceeds universe bounds")
        with self._lock:
            self._data[start:end] = values
            self._dirty = True

    def fill(self, value: int, start: int = 0, count: int | None = None) -> None:
        """Fill *count* channels starting at *start* with a single *value*."""
        if count is None:
            count = self._channel_count - start
        end = start + count
        if start < 0 or end > self._channel_count:
            raise ChannelError(f"Fill range {start}:{end} exceeds universe bounds")
        val = value & 0xFF
        with self._lock:
            for i in range(start, end):
                self._data[i] = val
            self._dirty = True

    # --- frame output ---

    def get_frame(self) -> bytearray:
        """Return a snapshot of the universe for transmission.

        Returns a *copy* so the output backend can work with stable data
        while the application keeps writing.
        """
        with self._lock:
            self._dirty = False
            return bytearray(self._data)

    @property
    def dirty(self) -> bool:
        """True if the universe has been modified since the last get_frame()."""
        return self._dirty

    def reset(self) -> None:
        """Zero all channels."""
        with self._lock:
            self._data = bytearray(self._channel_count)
            self._dirty = True

    # --- dunder helpers ---

    def __len__(self) -> int:
        return self._channel_count

    def __getitem__(self, channel: int) -> int:
        return self.get(channel)

    def __setitem__(self, channel: int, value: int) -> None:
        self.set(channel, value)

    def __repr__(self) -> str:
        return f"Universe(id={self.universe_id})"
```

The module-level docstring at the top of the file (line 1, `"""Lux Aeterna — 512-channel DMX universe backed by a bytearray for speed."""`) and the `from .constants import DMX_CHANNELS, ...` import both stay exactly as they are — `DMX_CHANNELS` is still needed as the default value.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/projects/luxaeterna && .venv/bin/python -m pytest tests/test_universe.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Run the full luxaeterna suite to confirm no regression**

Run: `cd ~/projects/luxaeterna && .venv/bin/python -m pytest -v`
Expected: every test that passed before this change still passes (this touches a widely-used class, so this is the check that no other caller assumed `DMX_CHANNELS` was hardcoded inside `Universe` itself).

- [ ] **Step 6: Commit directly to `main`**

```bash
cd ~/projects/luxaeterna
git add luxaeterna/universe.py tests/test_universe.py
git commit -m "$(cat <<'EOF'
feat(universe): accept an optional channel_count

Universe was hardcoded to exactly 512 DMX channels everywhere a bound
was checked. A caller managing its own internal render buffer wider
than one wire universe (mm-terrarium's Room simulator, next) had no
way to construct one that fit. channel_count defaults to DMX_CHANNELS,
so every existing caller is unaffected.
EOF
)"
```

---

### Task 2: Use the Room profile's real channel count in mm-terrarium's Room render buffer

**Repo:** this mm-terrarium worktree (`claude/lucid-curran-0613ef`). Depends on Task 1 already committed in `~/projects/luxaeterna` (the editable install picks it up immediately — no reinstall step needed, confirm with `python -c "import luxaeterna; print(luxaeterna.__file__)"` pointing at the sibling checkout if in doubt).

**Files:**
- Modify: `devicelink/agent.py:172`
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `Universe(universe_id: int = 0, channel_count: int = DMX_CHANNELS)` from Task 1. `RoomProfile.channel_count: int` (`control/room_profile.py`, already exists, no change).
- Produces: nothing new consumed by later tasks — this is the last code task.

- [ ] **Step 1: Write the failing test**

Open `tests/test_devicelink_agent.py` and find `_room_ready_game_server()` (around line 424) — it builds a `GameServer` with `TestBit` loaded and `RoomType.TEST`'s `main` fixture bound. Add a DEMO-flavored sibling immediately after it, and a test that drives `_render_room()` against DEMO's real 2592-channel profile:

```python
def _demo_room_ready_game_server(bound=None):
    """DEMO-flavored sibling of _room_ready_game_server(): TestBit loaded
    against RoomType.DEMO instead of TEST, with DEMO's one fixture ("array",
    864px / 2592 channels -- see control/room_profile.py's ROOM_PROFILES)
    bound. This is the regression coverage for the ChannelError bug: nothing
    before this test drove _render_room() above 512 channels."""
    if bound is None:
        bound = {"array": "sim-room-array"}
    binding = RoomBindingRegistry()
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.DEMO)
    gs.load_bit("TestBit")
    for fixture, dev in bound.items():
        gs.room.bound[fixture] = dev
        binding.bind(RoomType.DEMO, fixture, dev)
    return gs


def test_render_room_does_not_raise_for_a_profile_wider_than_512_channels():
    """Regression test for the DEMO Room ChannelError bug: DEMO's profile is
    864px / 2592 channels, well past one DMX universe. Before the
    channel_count fix, _setup_room() built the Room's light sink over a
    hardcoded 512-channel Universe, so every render_into() call raised
    ChannelError -- caught and silently swallowed by _render_room(), so the
    Room's light never rendered a single frame against a real Arco."""
    from control.room_profile import room_profile

    gs = _demo_room_ready_game_server()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    agent.server.bind_dev("sim-room-array", object())   # simulate the hello handshake

    assert agent._room_light is not None
    universe = agent._room_light.universe
    assert len(universe) == room_profile(RoomType.DEMO).channel_count

    agent._render_room()   # must not raise, and must actually send a frame

    sent = server.addressed("/sim-room-array/leds")
    assert sent
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py::test_render_room_does_not_raise_for_a_profile_wider_than_512_channels -v`
Expected: FAIL. `agent._room_light.universe` is a default 512-channel `Universe`, so `len(universe) == room_profile(RoomType.DEMO).channel_count` (2592) fails the assertion before `_render_room()` is even called — confirming the buffer really is undersized today.

- [ ] **Step 3: Fix `_setup_room()`**

In `devicelink/agent.py`, change line 172 from:

```python
        self._room_light = _RoomLightSink(session, Universe())
```

to:

```python
        self._room_light = _RoomLightSink(
            session, Universe(channel_count=self._room_profile.channel_count))
```

No other line in `_setup_room()` or `_render_room()` changes — `_render_room()`'s existing `frame = bytes(universe.get_frame()[:self._room_profile.channel_count])` (line 304) already assumed a buffer this wide; it was silently truncating a too-small one before (never actually reached, since `render_into()` raised first).

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py::test_render_room_does_not_raise_for_a_profile_wider_than_512_channels -v`
Expected: PASS.

- [ ] **Step 5: Run the full mm-terrarium suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests -v`
Expected: every previously-passing test still passes, including every other Room test in `tests/test_devicelink_agent.py` (TEST's profile is 180 channels, well under 512, so the default-`channel_count` path they exercise is unaffected).

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "$(cat <<'EOF'
fix(devicelink): size the Room's render buffer to its real profile width

_setup_room() built the Room's light sink over a hardcoded 512-channel
Universe. DEMO's profile is 864px / 2592 channels, so every
render_into() call raised ChannelError, caught and silently skipped by
_render_room() -- the Room's light never rendered a single frame
against a real Arco. Pass the profile's own channel_count through
(luxaeterna's Universe now accepts one) instead of the hardcoded
default.
EOF
)"
```

---

### Task 3: Live-verify against a real Arco

**Repo:** this mm-terrarium worktree. Depends on Task 1 and Task 2 both committed.

**Files:** none modified — this is a verification-only task, run directly (no fresh subagent needed; there is no code change or file diff to review here, only a command's output).

- [ ] **Step 1: Confirm the environment has what the run needs**

```bash
ls ~/projects/arco > /dev/null && echo "arco checkout present"
ls ~/projects/fluidsynth/sf2/FluidR3_GM.sf2 && echo "soundfont present"
```

Expected: both print their confirmation line. If either is missing, stop and report to the user — the repro command below cannot run without them.

- [ ] **Step 2: Run the exact reproduction command that originally surfaced the bug**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m harness.run_stack --room-type DEMO --devices 1 --ci --seconds 45
```

Run this from the mm-terrarium worktree root, with both Task 1 and Task 2's changes present (Task 1's fix is picked up live via the editable install; no reinstall needed).

- [ ] **Step 3: Check the result**

```bash
grep -c "ChannelError" control.log
```

Expected: `0`. (Before this fix, this same command produced 2172 occurrences over a 45s run.)

Also confirm from the command's own stdout/log that:
- Arco's audio genuinely opened (as before — this fix doesn't touch audio, so this should be unchanged).
- The run completed and exited cleanly under `--ci` (bounded, non-hanging), per `harness/markers.py`'s readiness-contract matching described in `docs/MM_TERRARIUM.md`.

If the player device separately hits the pre-existing, documented, unrelated headless clock-sync defect (`docs/MM_TERRARIUM.md`'s "Not yet built" section), that is NOT part of this task's scope — do not chase it, just note it if seen.

- [ ] **Step 4: Report the result to the user**

State plainly whether `ChannelError` count is 0 and whether the Room's light rendered (no code changes in this step — nothing to commit).

---

## Self-Review

**Spec coverage:**
- §2.1 (widen `Universe`) → Task 1.
- §2.2 (`devicelink/agent.py` fix) → Task 2, Step 3.
- §3 Non-goals (no RGBW widening, no PixelSpan/UniverseSet adoption, no backend changes, no luxaeterna version bump) → nothing in Task 1 or 2 touches any of those; confirmed by the diffs shown being scoped to exactly the lines named.
- §4 Testing (luxaeterna `test_universe.py`, mm-terrarium DEMO regression test) → Task 1 Step 1, Task 2 Step 1.
- §5 Live-verify plan → Task 3.
- §6 Process (luxaeterna direct-to-main, mm-terrarium normal branch/PR) → Task 1 Step 6 commits straight to what will be luxaeterna's `main`; Task 2 stays on this repo's existing feature branch for its normal PR flow.

**Placeholder scan:** none — every step has literal, runnable code or commands.

**Type consistency:** `Universe.__init__(self, universe_id: int = 0, channel_count: int = DMX_CHANNELS)` (Task 1) matches the call site `Universe(channel_count=self._room_profile.channel_count)` (Task 2) — keyword-only usage, no positional mismatch. `RoomProfile.channel_count` is read, never written, by either task.
