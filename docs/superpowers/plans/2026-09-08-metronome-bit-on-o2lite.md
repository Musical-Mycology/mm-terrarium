# MetronomeBit on the o2lite stack -- implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MetronomeBit loads, runs, judges taps correctly, and completes on the o2lite/o2ws stack, with the headless smoke test proving it.

**Architecture:** The engine stamps its presentation lead onto the loaded Bit (`Bit.cue_horizon`) so the Bit can grade input against presented beats; the Testshroom drives the gestures the granted role declares in `uses`, with a new phase-locked beat tapper that taps on displayed light pulses; `terrarium_boot` configures Python logging so engine and Bit diagnostics land in `control.log`.

**Tech Stack:** Python 3.13 in `.venv`, pytest, o2litepy (live only), luxaeterna (dev dep).

**Spec:** `docs/superpowers/specs/2026-09-08-metronome-bit-on-o2lite-design.md`

## Global Constraints

- Run every test through the venv: `.venv/bin/python -m pytest tests -q -p no:cacheprovider`. Never `python3`.
- No em dashes anywhere (code, comments, docs, commit messages). Use `--`.
- No attribution lines in commit messages.
- Test doubles must never be more permissive than the library they stand for (boundary rule 5).
- Engine boundary: `control/` stays Bit-agnostic; the only engine change is the `cue_horizon` stamp in `load_bit`.
- Every command in this plan runs on MYCOLOGICAL from the worktree root.
- `bits/metronome/bit.toml` already has `enabled = false` removed (commit f5f42a6); do not re-add it.

---

### Task 1: `Bit.cue_horizon`, stamped by `load_bit`

**Files:**
- Modify: `control/bit.py` (class attributes, after `room_types`)
- Modify: `control/engine.py:405` (right after `self.bit = bit`)
- Test: `tests/test_engine_bit_config.py`

**Interfaces:**
- Produces: `Bit.cue_horizon: float` class attribute, default `0.0`; `GameServer.load_bit` sets `bit.cue_horizon = self._horizon` on the instance.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine_bit_config.py`:

```python
def test_bit_cue_horizon_defaults_to_zero():
    assert ConfigBit().cue_horizon == 0.0


def test_load_bit_stamps_the_installations_cue_horizon_on_the_bit():
    gs = GameServer({"ConfigBit": ConfigBit}, cue_horizon=0.060)
    gs.load_bit("ConfigBit")
    assert gs.bit.cue_horizon == 0.060
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_bit_config.py -q -p no:cacheprovider`
Expected: 2 failures, `AttributeError: 'ConfigBit' object has no attribute 'cue_horizon'`.

- [ ] **Step 3: Add the attribute and the stamp**

In `control/bit.py`, after the `room_types` class attribute block, add:

```python
    # The installation's presentation lead, in seconds: for every `at` the
    # engine hands this Bit (a verb handler's `at`, fires(at)), `at` is the
    # gesture's own stamp (or Control's clock) PLUS this lead. Stamped by
    # GameServer.load_bit from BootConfig.cue_horizon. A Bit that emits cues
    # never needs it: cues are already in presentation time. A Bit that
    # grades INPUT against presented output does: a tap made at the instant
    # a beat is presented arrives as `at = beat + cue_horizon`, so the
    # tap's own moment is `at - cue_horizon`. Default 0.0 keeps every
    # hand-constructed test Bit exact.
    cue_horizon: float = 0.0
```

In `control/engine.py`, immediately after `self.bit = bit` (line 405), add:

```python
        # See Bit.cue_horizon: the one place the presentation lead reaches
        # a Bit, so `origin + horizon` still lives only in data()/tick().
        bit.cue_horizon = self._horizon
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_engine_bit_config.py tests/test_engine.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add control/bit.py control/engine.py tests/test_engine_bit_config.py
git commit -m "feat(engine): stamp the installation's cue_horizon on the loaded Bit"
```

---

### Task 2: MetronomeBit grades taps at their own moment, and logs judgments

**Files:**
- Modify: `bits/metronome/metronome_bit.py` (`_on_tap`, the judgment loop in `fires`, module imports)
- Test: `tests/test_metronome_bit_engine.py`, `tests/test_metronome_bit_judgment.py`

**Interfaces:**
- Consumes: `Bit.cue_horizon` from Task 1.
- Produces: `MetronomeBit._on_tap` grades `t = at - self.cue_horizon - self.INPUT_OFFSET_S`; INFO log lines `tap <dev> cycle <c> beat <w> err <+x.x> ms` and `cycle <c> <dev>: success|fail (<n> hits, spoiled=<bool>)` on logger `bits.metronome.metronome_bit`.

- [ ] **Step 1: Write the failing engine-level test**

Append to `tests/test_metronome_bit_engine.py`:

```python
def test_a_tap_made_when_a_wait_beat_is_presented_is_judged_on_time():
    """A physically perfect tap: stamped at the instant Control PRESENTS a
    wait beat. GameServer.data() adds cue_horizon to that stamp before the
    handler sees it, so without Bit.cue_horizon the Bit read every perfect
    tap as +horizon (60 ms, outside its 50 ms window). Reproduced live
    2026-09-08; see the spec section 1.3."""
    clk = SimpleNamespace(t=100.0)
    gs = GameServer({"metro": MetronomeBit}, clock=lambda: clk.t,
                    cue_horizon=0.060)
    gs.room = _Room({"main": "sim-room-main"})
    gs.on_light_cue = lambda *a: None
    gs.load_bit("metro")
    gs.hello("ie1", "sim", "1")
    gs.join("ie1", "METRO_PLAYER_NODE")
    gs.run()
    bit = gs.bit
    while bit._t0 is None:
        _tick(gs, clk, 0.01, step=0.01)
    presented = bit._grid(4)            # cycle 0, wait beat 0
    while clk.t < presented - 1e-9:
        _tick(gs, clk, 0.01, step=0.01)
    clk.t = presented
    assert gs.data("ie1", "tap", ["ie1", 1.0, 50.0, 1],
                   gesture_time=presented) is None
    assert bit._tap_errors_ms == [0.0]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metronome_bit_engine.py -q -p no:cacheprovider -k presented`
Expected: FAIL, `assert [60.0] == [0.0]`.

- [ ] **Step 3: Write the failing unit tests**

Append to `tests/test_metronome_bit_judgment.py`:

```python
def test_tap_subtracts_the_stamped_cue_horizon():
    bit = _started()
    bit.cue_horizon = 0.060
    # The engine hands `at = tap moment + horizon`; the tap moment is the
    # gridpoint itself.
    bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, 0) + 0.060)
    assert bit._tap_errors_ms == [0.0]


def test_cue_horizon_defaults_to_zero_so_existing_judgment_is_unchanged():
    bit = _started()
    assert bit.cue_horizon == 0.0
    bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, 2) + 0.010)
    assert bit._tap_errors_ms == [10.0]


def test_taps_and_verdicts_are_logged_at_info(caplog):
    import logging
    bit = _started()
    with caplog.at_level(logging.INFO, logger="bits.metronome.metronome_bit"):
        bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, 1) + 0.004)
        # past cycle 0's judge deadline: grid(7) + TOLERANCE + JUDGE_SLACK
        bit.fires(bit._grid(7) + bit.TOLERANCE_S + bit.JUDGE_SLACK_S + 0.001)
    messages = [r.getMessage() for r in caplog.records]
    assert "tap ie1 cycle 0 beat 1 err +4.0 ms" in messages
    assert any(m.startswith("cycle 0 ie1: fail") for m in messages)
```

- [ ] **Step 4: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_metronome_bit_judgment.py -q -p no:cacheprovider`
Expected: `test_tap_subtracts_the_stamped_cue_horizon` fails (`[60.0] == [0.0]`), the logging test fails (no records); the default test passes.

- [ ] **Step 5: Implement**

In `bits/metronome/metronome_bit.py`, add to the imports:

```python
import logging
```

and after the imports:

```python
logger = logging.getLogger(__name__)
```

Replace the start of `_on_tap` so it reads:

```python
    def _on_tap(self, dev: str, args: list, at: float) -> list:
        if self._t0 is None or self._done:
            return []
        # `at` is the presentation time of this tap's consequence: the
        # device's own stamp plus the installation's cue_horizon (stamped
        # on this Bit by GameServer.load_bit). The beat grid is in
        # presentation time too, and each beat is PRESENTED at its
        # gridpoint, so the tap's own moment is `at - cue_horizon`. The
        # 2026-08-20 design claimed the horizon cancelled here; it cancels
        # for this Bit's own cues, not for input (spec 2026-09-08 section
        # 1.3). INPUT_OFFSET_S stays what it was: a knob for real
        # input-path latency, not for the horizon.
        t = at - self.cue_horizon - self.INPUT_OFFSET_S
```

After `self._tap_errors_ms.append(round(best_err * 1000.0, 1))` add:

```python
        logger.info("tap %s cycle %d beat %d err %+.1f ms",
                    dev, cycle, best_w, best_err * 1000.0)
```

In `fires`, inside the judgment loop, after `success = ...` add:

```python
                logger.info("cycle %d %s: %s (%d hits, spoiled=%s)",
                            c, dev, "success" if success else "fail",
                            len(phrase["hits"]), phrase["spoiled"])
```

- [ ] **Step 6: Run the Bit's whole suite**

Run: `.venv/bin/python -m pytest tests/test_metronome_bit_engine.py tests/test_metronome_bit_judgment.py tests/test_metronome_bit_grid.py tests/test_metronome_bit_finale.py tests/test_metronome_bit_declarations.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add bits/metronome/metronome_bit.py tests/test_metronome_bit_engine.py tests/test_metronome_bit_judgment.py
git commit -m "fix(metronome): grade taps at their own moment, not at presentation time; log judgments"
```

---

### Task 3: MetronomeBit resolves through the registry again

**Files:**
- Test: `tests/test_bit_packages.py:18-33`

- [ ] **Step 1: Rewrite the test to go through the enabled gate**

Replace `test_metronome_package_rhythm_block_reaches_instance` with:

```python
def test_metronome_package_rhythm_block_reaches_instance():
    # Re-enabled 2026-09-08 (spec 2026-09-08-metronome-bit-on-o2lite): the
    # shipped manifest must resolve through the registry's enabled gate,
    # exactly the path --profile and the Console take.
    reg = BitRegistry.discover()
    cls = reg.bit_class("MetronomeBit")
    fast_cfg = reg.resolve_config("MetronomeBit", {"rhythm": {"bpm": 120}})
    fast = cls(fast_cfg)
    assert abs(fast.BEAT_S - 0.5) < 1e-9
    default = cls()
    assert abs(default.BEAT_S - 0.6) < 1e-9
    assert abs(fast.LEAD_IN_S - 0.5) < 1e-9


def test_metronome_package_is_enabled():
    reg = BitRegistry.discover()
    assert reg.packages["MetronomeBit"].config.identity.enabled is True
    assert "MetronomeBit" in reg.lazy_class_map()
```

Remove the now-unused imports `replace` and `merge_overrides` only if nothing else in the file uses them (check with `grep -n "replace(\|merge_overrides(" tests/test_bit_packages.py`).

- [ ] **Step 2: Run**

Run: `.venv/bin/python -m pytest tests/test_bit_packages.py tests/test_bit_registry.py tests/test_run_profile.py tests/test_run_stack.py tests/test_disabled_bit_cli.py -q -p no:cacheprovider`
Expected: all pass (the conftest enabled-copy fixtures strip a line that is no longer there; harmless).

- [ ] **Step 3: Commit**

```bash
git add tests/test_bit_packages.py
git commit -m "test(bits): MetronomeBit resolves through the registry's enabled gate"
```

---

### Task 4: `harness/beat_tapper.py`, a phase-locked follower of light pulses

**Files:**
- Create: `harness/beat_tapper.py`
- Test: `tests/test_beat_tapper.py`

**Interfaces:**
- Produces: `class BeatTapper(beats_per_cycle=8, call_beats=4)` with `observe(frame: bytes, now: float) -> int | None` (returns the beat index to tap on, or None), `reset()`, `taps: int` (count of taps issued), `locked: bool`. Constants `RISE_RATIO = 0.25`, `DARK_SUM_FRACTION = 0.05`, `ACCEPT_FRACTION = 0.2`, `MIN_PERIOD_S = 0.2`, `MAX_PERIOD_S = 2.0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_beat_tapper.py`:

```python
"""tests/test_beat_tapper.py

The Testshroom's synthetic player for a call-and-response Bit: it watches
the brightness of the frames it DISPLAYS, locks onto the beat pulses, and
taps on the answer beats. Frames here are 36-channel (12 px GRB); a
"level" is painted as one byte on every channel so brightness is
level * 36.
"""
from harness.beat_tapper import BeatTapper

BEAT = 0.75
BASE, PULSE = 60, 110


def frame(level: int) -> bytes:
    return bytes([level]) * 36


def pulse_train(tapper, t_first, beats, period=BEAT, level=BASE, pulse=PULSE):
    """Feed `beats` beats from t_first: a pulse frame at each gridpoint and
    a decay frame 150 ms later. Returns the list of (beat_time, result)."""
    out = []
    for k in range(beats):
        t = t_first + k * period
        out.append((t, tapper.observe(frame(pulse), t)))
        tapper.observe(frame(level), t + 0.15)
    return out


def test_first_two_pulses_lock_and_nothing_taps_during_the_call():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)      # steady base before beat 0
    results = pulse_train(tapper, 20.0, 4)
    assert tapper.locked
    assert [r for _, r in results] == [None, None, None, None]


def test_taps_on_the_four_answer_beats_of_every_cycle():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    results = pulse_train(tapper, 20.0, 16)
    tapped = [r for _, r in results if r is not None]
    assert tapped == [4, 5, 6, 7, 12, 13, 14, 15]
    assert tapper.taps == 8


def test_the_grant_rise_from_black_is_not_beat_zero():
    """The role grant lights a black canvas moments before beat 0."""
    tapper = BeatTapper()
    tapper.observe(frame(0), 19.0)
    tapper.observe(frame(BASE), 19.2)      # grant: black -> base
    results = pulse_train(tapper, 20.0, 8)
    tapped = [r for _, r in results if r is not None]
    assert tapped == [4, 5, 6, 7]


def test_fireworks_flashes_between_beats_are_ignored():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    pulse_train(tapper, 20.0, 8)           # cycle 0, locked
    # 12 flashes every 120 ms starting just after beat 8's gridpoint,
    # riding on top of the real pulses of cycle 1.
    t8 = 20.0 + 8 * BEAT
    flash_times = [t8 + 0.05 + i * 0.12 for i in range(12)]
    events = []
    for k in range(8, 16):
        events.append((20.0 + k * BEAT, PULSE))
        events.append((20.0 + k * BEAT + 0.15, BASE))
    for ft in flash_times:
        events.append((ft, 200))
        events.append((ft + 0.08, BASE))
    events.sort()
    tapped = [tapper.observe(frame(level), t) for t, level in events]
    tapped = [r for r in tapped if r is not None]
    # A flash that lands inside a beat's window may be taken AS that beat
    # (it is within tolerance); no beat index may be tapped twice and no
    # call beat may be tapped.
    assert tapped == sorted(set(tapped))
    assert all(8 <= k < 16 and k % 8 >= 4 for k in tapped)


def test_a_dark_spell_re_indexes_by_time():
    """A failed player goes dark for a cycle and is relit on its next
    turn's downbeat: the next rise is beat 16, not beat 8."""
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    pulse_train(tapper, 20.0, 8)
    tapper.observe(frame(0), 20.0 + 8 * BEAT)        # fail: dark
    results = pulse_train(tapper, 20.0 + 16 * BEAT, 8)
    tapped = [r for _, r in results if r is not None]
    assert tapped == [20, 21, 22, 23]


def test_period_is_refined_over_the_long_baseline():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    # First interval carries 10 ms of display jitter; later pulses are exact.
    tapper.observe(frame(PULSE), 20.0)
    tapper.observe(frame(BASE), 20.15)
    tapper.observe(frame(PULSE), 20.0 + BEAT + 0.010)
    tapper.observe(frame(BASE), 20.0 + BEAT + 0.16)
    for k in range(2, 12):
        tapper.observe(frame(PULSE), 20.0 + k * BEAT)
        tapper.observe(frame(BASE), 20.0 + k * BEAT + 0.15)
    assert abs(tapper.period - BEAT) < 0.002


def test_an_implausible_first_interval_restarts_the_lock():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    tapper.observe(frame(PULSE), 12.0)     # a lone rise 8 s early
    tapper.observe(frame(BASE), 12.15)
    results = pulse_train(tapper, 20.0, 8)
    tapped = [r for _, r in results if r is not None]
    assert tapped == [4, 5, 6, 7]


def test_reset_forgets_the_lock():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    pulse_train(tapper, 20.0, 8)
    tapper.reset()
    assert not tapper.locked and tapper.taps == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_beat_tapper.py -q -p no:cacheprovider`
Expected: ImportError, `harness.beat_tapper` not found.

- [ ] **Step 3: Implement**

Create `harness/beat_tapper.py`:

```python
"""BeatTapper: the Testshroom's synthetic player for a call-and-response
Bit (MetronomeBit today).

A human hears the Room's four clicks and taps four back. A headless
Testshroom hears nothing, but it does SEE its own light: MetronomeBit
pulses every joined player's level on every beat (cc:11 60 -> 110, back
150 ms later). This class watches the brightness of the frames the device
actually displays, locks onto those pulses, and reports which beats to
tap on: the answer beats, index modulo `beats_per_cycle` at or past
`call_beats`.

Clock-free and socket-free: the caller passes each displayed frame with
the O2 time it was displayed at (harness/websim_leds.py's on_show hook,
driven from ShroomClient.tick), and sends the /game/tap itself when
observe() returns a beat index. The tap's stamp is therefore the display
tick's own clock reading -- at the source (Design Rule 4) -- which is
what makes a live run prove the whole loop: Control's cue, the device's
display, the tap, and the Bit's judgment against the same gridpoint.

Why a phase-lock and not a counter: fireworks (12 bloom flashes in 1.4 s
on a winning player) are rises too, and a failed player goes dark for a
whole cycle and sees no pulses at all. Indexing each accepted rise by
TIME against the locked grid survives both; counting rises survives
neither.
"""

from __future__ import annotations

RISE_RATIO = 0.25          # a frame this much brighter than the last is a rise
DARK_SUM_FRACTION = 0.05   # below this fraction of full-white, a frame is dark
ACCEPT_FRACTION = 0.2      # a rise within this fraction of a period of a
                           # predicted beat IS that beat
MIN_PERIOD_S = 0.2
MAX_PERIOD_S = 2.0


class BeatTapper:
    def __init__(self, beats_per_cycle: int = 8, call_beats: int = 4) -> None:
        self.beats_per_cycle = beats_per_cycle
        self.call_beats = call_beats
        # Set by the caller once the granted role's `uses` lists `tap`. A
        # role property, not lock state, so reset() leaves it alone.
        self.armed = False
        self.reset()

    def reset(self) -> None:
        self._prev_sum: int | None = None
        self._prev_channels: int = 0
        self._t_first: float | None = None
        self.period: float | None = None
        self._last_index: int = -1
        self.taps = 0

    @property
    def locked(self) -> bool:
        return self.period is not None

    def observe(self, frame: bytes, now: float) -> int | None:
        """Record one displayed frame. Returns the beat index to tap on
        right now, or None."""
        total = sum(frame)
        prev, self._prev_sum = self._prev_sum, total
        self._prev_channels = len(frame)
        if prev is None:
            return None
        if total <= prev * (1.0 + RISE_RATIO):
            return None
        from_black = prev < DARK_SUM_FRACTION * 255 * len(frame)
        return self._rise(now, from_black)

    def _rise(self, now: float, from_black: bool) -> int | None:
        if self._t_first is None:
            if from_black:
                return None          # the grant lighting a black canvas
            self._t_first = now
            self._last_index = 0
            return None
        if self.period is None:
            if from_black:
                return None
            interval = now - self._t_first
            if not (MIN_PERIOD_S <= interval <= MAX_PERIOD_S):
                self._t_first = now  # restart from this rise
                self._last_index = 0
                return None
            self.period = interval
            self._last_index = 1
            return self._tap_if_answer(1)
        k = round((now - self._t_first) / self.period)
        if k <= self._last_index:
            return None              # already handled this beat
        predicted = self._t_first + k * self.period
        if abs(now - predicted) > ACCEPT_FRACTION * self.period:
            return None              # a flash, not a beat
        self.period = (now - self._t_first) / k
        self._last_index = k
        return self._tap_if_answer(k)

    def _tap_if_answer(self, k: int) -> int | None:
        if k % self.beats_per_cycle < self.call_beats:
            return None
        self.taps += 1
        return k
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_beat_tapper.py -q -p no:cacheprovider`
Expected: all pass. If `test_fireworks_flashes_between_beats_are_ignored` fails on the "no call beat tapped" assertion, the flash accepted as a beat had an index within the window; that is allowed by the test only for answer beats, so check `ACCEPT_FRACTION` is applied to `abs(now - predicted)` as written.

- [ ] **Step 5: Commit**

```bash
git add harness/beat_tapper.py tests/test_beat_tapper.py
git commit -m "feat(harness): BeatTapper, a phase-locked synthetic player for call-and-response Bits"
```

---

### Task 5: `WebSimLeds.on_show`

**Files:**
- Modify: `harness/websim_leds.py` (`WebSimLeds.__init__`, `show`)
- Test: `tests/test_websim_leds.py`

**Interfaces:**
- Produces: `WebSimLeds(backend, channels, on_show=None, clock=None)`; `show(frame)` calls `on_show(frame, clock())` after `backend.send(frame)` when both are given (`now` is `None` when no clock).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_websim_leds.py`:

```python
def test_on_show_sees_each_displayed_frame_with_the_clock_reading():
    backend = FakeBackend()
    seen = []
    leds = WebSimLeds(backend, channels=36,
                      on_show=lambda frame, now: seen.append((frame, now)),
                      clock=lambda: 42.5)

    leds.show(bytes(range(36)))

    assert backend.sent == [bytes(range(36))]
    assert seen == [(bytes(range(36)), 42.5)]


def test_on_show_without_a_clock_passes_none():
    seen = []
    leds = WebSimLeds(FakeBackend(), channels=36,
                      on_show=lambda frame, now: seen.append(now))
    leds.show(bytes(36))
    assert seen == [None]


def test_clear_does_not_call_on_show():
    seen = []
    leds = WebSimLeds(FakeBackend(), channels=36,
                      on_show=lambda frame, now: seen.append(frame))
    leds.clear()
    assert seen == []


def test_a_raising_on_show_never_stops_the_frame(caplog):
    backend = FakeBackend()

    def boom(frame, now):
        raise RuntimeError("tapper broke")

    leds = WebSimLeds(backend, channels=36, on_show=boom)
    leds.show(bytes(36))
    assert backend.sent == [bytes(36)]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_websim_leds.py -q -p no:cacheprovider`
Expected: 4 failures, `TypeError: ... unexpected keyword argument 'on_show'`.

- [ ] **Step 3: Implement**

In `harness/websim_leds.py`, add `import logging` at the top (after `from __future__ import annotations`) and `logger = logging.getLogger(__name__)` after the palette. Replace `WebSimLeds.__init__` and `show`:

```python
    def __init__(self, backend, channels: int, on_show=None,
                 clock=None) -> None:
        self._backend = backend
        self._channels = channels
        # on_show(frame, now): called after every DISPLAYED frame (not
        # clear()), with `now` from `clock()` when a clock is given, else
        # None. harness/beat_tapper.py listens here; the Room simulator
        # passes neither. A raising hook is logged and never stops the
        # frame -- a broken tapper must not blank the canvas.
        self._on_show = on_show
        self._clock = clock

    def show(self, frame: bytes) -> None:
        self._backend.send(frame)
        if self._on_show is None:
            return
        try:
            self._on_show(frame, self._clock() if self._clock else None)
        except Exception:
            logger.exception("on_show hook raised; frame already displayed")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_websim_leds.py tests/test_o2_shroom.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/websim_leds.py tests/test_websim_leds.py
git commit -m "feat(harness): WebSimLeds on_show hook for displayed frames"
```

---

### Task 6: The Testshroom drives the gestures the role declares

**Files:**
- Modify: `harness/o2_shroom.py` (`build()` signature and `WebSimLeds` construction; `main()` tapper wiring, the gesture block of the tick loop, the lobby reset, the exit report)
- Test: `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: `BeatTapper` (Task 4), `WebSimLeds(on_show=, clock=)` (Task 5).
- Produces: `wants_verb(config: dict | None, verb: str) -> bool` (module-level, pure); `build(..., on_show=None)`; printed lines `tap sent: beat <k> at <t>` and `beat taps sent: <n>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_o2_shroom.py`:

```python
# --- The role's `uses` list decides which synthetic gestures run. ---------

from harness.o2_shroom import wants_verb


def test_wants_verb_follows_the_roles_uses_list():
    cfg = {"uses": ["tap"]}
    assert wants_verb(cfg, "tap") is True
    assert wants_verb(cfg, "tilt") is False


def test_wants_verb_is_permissive_when_uses_is_absent_or_empty():
    """Legacy roles that declare no `uses` keep today's tilt sweep."""
    assert wants_verb({}, "tilt") is True
    assert wants_verb({"uses": []}, "tilt") is True
    assert wants_verb(None, "tilt") is True


def test_build_threads_on_show_into_the_leds():
    seen = []
    client, backend = build("ie1", serve=False,
                            on_show=lambda frame, now: seen.append(frame))
    client.leds.show(bytes(36))
    assert seen == [bytes(36)]


def test_main_gates_the_tilt_sweep_and_the_tapper_on_uses():
    """Source-level pin, in the style of test_main_has_exactly_one_backend_close:
    the tick loop consults wants_verb for both gestures and the tapper is
    wired to the leds' on_show."""
    import inspect
    import harness.o2_shroom as mod
    src = inspect.getsource(mod.main)
    assert 'wants_verb(client.config, "tilt")' in src
    assert 'wants_verb(client.config, "tap")' in src
    assert "BeatTapper(" in src
    assert "on_show=" in src
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py -q -p no:cacheprovider -k "wants_verb or on_show or gates"`
Expected: ImportError on `wants_verb`.

- [ ] **Step 3: Implement the helper and `build()`**

In `harness/o2_shroom.py`, after `SWEEP_RESUME_SECONDS = 5.0`, add:

```python
def wants_verb(config: dict | None, verb: str) -> bool:
    """Whether the granted role wants this synthetic gesture. The role blob
    carries the Bit's `uses` list (control/role_config.py); a role that
    declares one gets only what it lists -- MetronomeBit's player lists
    `tap` alone, and driving it with tilts earned an `unknown verb 'tilt'`
    error on every run. A role that declares nothing keeps the legacy
    tilt sweep, so hand-built Bits are unchanged."""
    uses = (config or {}).get("uses") or []
    return not uses or verb in uses
```

Change `build()`'s signature to add `on_show=None` after `instrument`, extend its docstring with one line (`on_show, when given, is WebSimLeds' displayed-frame hook, called (frame, clock()) -- see harness/websim_leds.py.`), and change the `ShroomClient` construction to:

```python
    client = ShroomClient(dev, node,
                          leds=WebSimLeds(backend, channels,
                                          on_show=on_show, clock=clock),
                          on_role=_on_role, on_play=on_play,
                          expected_channels=channels, instrument=instrument)
```

- [ ] **Step 4: Wire the tapper into `main()`**

In `main()`, before `client, backend = build(...)`, add:

```python
    from harness.beat_tapper import BeatTapper

    # The synthetic player for a call-and-response role (MetronomeBit):
    # taps on the answer beats it sees in its own light. Armed only once
    # the granted role's `uses` says `tap` (see the tick loop below).
    tapper = BeatTapper()

    def _on_frame(frame: bytes, now) -> None:
        if not tapper.armed or now is None:
            return
        beat = tapper.observe(frame, now)
        if beat is None:
            return
        # Stamped at the display tick's own clock reading -- the moment
        # this device showed the beat -- never Control's receipt time.
        o2lite.send("/game/tap", now, "sffi", args.dev, 1.0, 50.0, 1)
        print(f"tap sent: beat {beat} at {now:.3f}", flush=True)
```

and pass `on_show=_on_frame` into `build(...)`. `tapper.armed` is the instance attribute Task 4 defined; the tick loop below sets it, and `_on_frame` reads it, so no `nonlocal` is needed.

In the tick loop, replace the block that starts at `if not args.no_join and _gestures_ready(client):` so it reads:

```python
                if not args.no_join and _gestures_ready(client):
                    if next_tilt is None:
                        next_tilt = now   # first tilt fires now the role is in
                        print(f"{markers.DEVICE_ROLE_GRANTED} {joins_sent} "
                              f"join(s); gestures starting at {now:.3f}", flush=True)
                        # The role decides which synthetic gestures run.
                        tapper.armed = wants_verb(client.config, "tap")
                        if tapper.armed:
                            print("role uses tap: beat tapper armed", flush=True)
                    operator = drain_gestures(operator_input, o2lite.send,
                                              args.dev, now)
                    if operator is not None:
                        last_operator_tilt = operator
                    sweeping = (wants_verb(client.config, "tilt")
                                and (last_operator_tilt is None
                                     or now - last_operator_tilt >= SWEEP_RESUME_SECONDS))
                    if now >= next_tilt:
                        if sweeping:
                            gamma = tilt_sweep(now - start)
                            o2lite.send("/game/tilt", now, "sf", args.dev, gamma)
                        next_tilt += interval
```

Keep the existing comments in that block where they still apply (the "Say so explicitly" and "Timestamps at the source" comments). In the lobby-return branch, after `client.reset_for_lobby()`, add:

```python
            tapper.reset()
            tapper.armed = False
```

In the `finally:` block, after `print(f"frames displayed late: {client.clamped}")`, add:

```python
        print(f"beat taps sent: {tapper.taps}")
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py tests/test_websim_leds.py tests/test_run_stack.py -q -p no:cacheprovider`
Expected: all pass. `test_main_has_exactly_one_backend_close` and the source-level `main()` pins must still pass; if a pin on the tilt send line breaks, keep that line's text as it was (`o2lite.send("/game/tilt", now, "sf", args.dev, gamma)`).

- [ ] **Step 6: Commit**

```bash
git add harness/o2_shroom.py tests/test_o2_shroom.py
git commit -m "feat(harness): Testshroom drives the gestures the role declares; beat tapper for tap roles"
```

---

### Task 7: Logging reaches control.log

**Files:**
- Modify: `harness/terrarium_boot.py` (module imports, new `configure_logging`, first line of `main()`)
- Test: `tests/test_terrarium_boot.py`

**Interfaces:**
- Produces: `configure_logging(level=logging.INFO, stream=None) -> logging.Handler` (idempotent; returns the existing handler on a second call).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium_boot.py`:

```python
def test_configure_logging_routes_engine_warnings_to_the_stream():
    import io
    import logging
    from harness.terrarium_boot import configure_logging
    stream = io.StringIO()
    handler = configure_logging(stream=stream)
    try:
        logging.getLogger("control.engine").warning("refusing gesture stamp")
        assert "WARNING control.engine: refusing gesture stamp" in stream.getvalue()
        logging.getLogger("bits.metronome.metronome_bit").info("tap ie1")
        assert "INFO bits.metronome.metronome_bit: tap ie1" in stream.getvalue()
    finally:
        logging.getLogger().removeHandler(handler)


def test_configure_logging_is_idempotent():
    import logging
    from harness.terrarium_boot import configure_logging
    first = configure_logging()
    try:
        assert configure_logging() is first
        ours = [h for h in logging.getLogger().handlers
                if getattr(h, "terrarium_boot", False)]
        assert ours == [first]
    finally:
        logging.getLogger().removeHandler(first)


def test_main_configures_logging_first():
    import inspect
    import harness.terrarium_boot as mod
    src = inspect.getsource(mod.main)
    assert src.index("configure_logging()") < src.index("_build_arg_parser()")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -q -p no:cacheprovider -k configure_logging`
Expected: ImportError on `configure_logging`.

- [ ] **Step 3: Implement**

In `harness/terrarium_boot.py`, add `import logging` to the stdlib imports. After the imports, add:

```python
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO, stream=None) -> logging.Handler:
    """Route every `logging` call in the Control process to stderr, which
    run_stack merges into control.log. Nothing configured the root logger
    before 2026-09-08, so every logger.warning in control/engine.py and
    devicelink/agent.py (refused gesture stamps, ROOM cues with no Room
    bound, dropped frames) was silently discarded in a live run. INFO is
    quiet here: three info sites exist in the runtime path, plus
    MetronomeBit's per-tap judgment lines, which are the point.

    Idempotent: a second call returns the handler the first installed,
    so tests that call main() do not stack handlers."""
    root = logging.getLogger()
    for existing in root.handlers:
        if getattr(existing, "terrarium_boot", False):
            return existing
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.terrarium_boot = True
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return handler
```

Make the first statement of `main()`:

```python
    configure_logging()
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "feat(harness): configure logging in terrarium_boot so engine diagnostics reach control.log"
```

---

### Task 8: Full suite, then the live CI run

**Files:**
- None modified unless the suite or the run finds a defect (fix it in the file that owns it, with a test, in this task).

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider`
Expected: 0 failures (baseline on main was 2037 passed, 1 skipped; this branch adds tests). One cue test is known to flake once; rerun that file alone if it does.

- [ ] **Step 2: Run the live smoke test**

Run (MYCOLOGICAL, worktree root): `./smoke-test.sh --ci --profile profiles/dev-metronome.toml --seconds 60`
Expected: exit 0 and `stack run bit-completed`. Then read `runs/<stamp>/control.log`, `ie1.log`, `ie2.log` and confirm:
- `join granted: ie1 -> player (scored) via METRO_PLAYER_NODE` and the same for ie2.
- `role uses tap: beat tapper armed` in both device logs, no `unknown verb 'tilt'` line.
- `tap sent: beat <k> at <t>` lines in both device logs, only for k with k % 8 >= 4, and `beat taps sent: N` with N >= 8 per device.
- `INFO bits.metronome.metronome_bit: tap ie1 cycle 0 beat 0 err <x> ms` lines in control.log with |x| well inside 50 ms.
- At least one `cycle <c> <dev>: success` line, a fireworks fire, then the finale (the run lasts about 10 s longer than the fail-only run).
- `Bit completed; tearing down`.

- [ ] **Step 3: If anything above is missing, debug it with systematic-debugging**

Root cause first. Candidate places: the tapper's rise threshold against the real aurora render (dump `sum(frame)` per displayed frame from `_on_frame` temporarily to see the pulse amplitude), `ACCEPT_FRACTION` against real display jitter, the tap stamp path (`env.timestamp` in `devicelink/agent.py:_handle` -> `GameServer.data(gesture_time=)`). Add a test for whatever is fixed. Do not widen `TOLERANCE_S`.

- [ ] **Step 4: Commit anything fixed**

```bash
git add -A
git commit -m "fix(harness): <what the live run found>"
```

Record the run stamp (`runs/<stamp>`) for Task 10.

---

### Task 9: Browser gate over o2ws

**Files:**
- None in this repo unless a defect is found.

- [ ] **Step 1: Build the browser app**

Run (MYCOLOGICAL): `git -C /Users/chris/projects/mm-tuneshroom checkout claude/o2ws-link && (cd /Users/chris/projects/mm-tuneshroom && tool/sim build)`
Expected: `build/web/index.html` exists. Restore the previous branch afterwards only if it was not `claude/o2ws-link` (it was `main` at planning time; leave the checkout on `claude/o2ws-link` and say so in the PR).

- [ ] **Step 2: Serve the stack with the web build**

Run in the background (MYCOLOGICAL, worktree root): `./smoke-test.sh --serve --profile profiles/dev-metronome.toml --devices 1 --web-build /Users/chris/projects/mm-tuneshroom/build/web` (`--devices 1` leaves the second `player` slot for the browser; see Step 4)
Read the `WWW_URL:` line from the output. Wait for `Holding in SETUP`.

- [ ] **Step 3: Open the guest page**

In the automation browser open `http://127.0.0.1:8788/app/index.html?node=METRO_PLAYER_NODE` (the LAN address is refused by the tool). Wait for the page to report clock sync and a granted role. Take a screenshot.

- [ ] **Step 4: Confirm**

In `runs/<stamp>/control.log`: a `device hello:` for the browser's dev id, `join granted: <dev> -> player (scored) via METRO_PLAYER_NODE`, and no `string wire flavor` errors. In the page: LED frames changing (the beat pulse) once the second scored device (ie1 from the stack) has joined and the round is RUNNING. Judge join and frames, not timing (hidden tabs clamp timers to about 1 s). Note: the profile's `min_scored = 2` and `devices = 2` means the stack's two Testshrooms fill both `player` slots (capacity 2) before a browser can join; run the serve step with `--devices 1` so the browser is the second scored join.

- [ ] **Step 5: Stop the stack**

Ctrl-C the serve run (or kill the background process) and confirm the teardown lines. Record the stamp and the screenshot path for Task 10.

---

### Task 10: Docs

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (MetronomeBit entry at the `### bits/metronome/metronome_bit.py` heading; the `### devicelink/o2_transport.py ... Control on o2lite, and timed cues` entry; the `### Browser guests over o2ws, Control side` entry's Plan B note)
- Modify: `docs/superpowers/specs/2026-08-20-metronome-bit-design.md` (section 4)
- Modify: `docs/superpowers/specs/2026-09-08-metronome-bit-on-o2lite-design.md` (Status)

- [ ] **Step 1: MetronomeBit entry**

Replace the `- **Live-verified headless 2026-08-20** ...` bullet with:

```markdown
- **Live-verified headless 2026-08-20** (`--ci --devices 2 --room-type DEMO
  --bit MetronomeBit`, runs/20260820-231958): both joins granted, clean
  teardown. CI note: a `--seconds` bound below ~35 s can truncate the
  finale window.
- **Disabled 2026-09-01, re-enabled 2026-09-08.** The console-load-
  stabilization slice set `enabled = false` "pending redesign"; no
  redesign was ever specified, and the flag was the first thing the
  o2lite-era run hit (the profile refuses before Arco boots). Design:
  `docs/superpowers/specs/2026-09-08-metronome-bit-on-o2lite-design.md`.
- **Every perfect tap was judged 60 ms late (root-caused 2026-09-08).**
  `GameServer.data()` hands a handler `at = stamp + cue_horizon`, the
  time the tap's consequence is presented; the beat grid is in
  presentation time and each beat is presented AT its gridpoint, so a
  tap made the instant a beat shows carries `at = beat + horizon`. The
  2026-08-20 design's "the horizon offset cancels" was true for the
  Bit's own cues and false for input. Reproduced at engine level
  (`tests/test_metronome_bit_engine.py`, `+60.0` in `tap_errors_ms`
  with a 50 ms window). Fix: `Bit.cue_horizon`, stamped by `load_bit`,
  and the Bit grades `at - cue_horizon - INPUT_OFFSET_S`.
  `input_offset_ms` stays a knob for real input-path latency.
- **The Testshroom drives what the role declares.** `harness/o2_shroom.py`
  reads `uses` off the role blob: the tilt sweep runs only for a role
  that lists `tilt` (or lists nothing), which ends the `unknown verb
  'tilt'` error every MetronomeBit run used to log; a role that lists
  `tap` arms `harness/beat_tapper.py`, a phase-locked follower of
  brightness rises in the frames the device DISPLAYS (the Bit's per-beat
  level pulse), tapping on the answer beats with the display tick's own
  clock reading as the stamp. Fireworks flashes are rejected by the
  lock's acceptance window; a failed player's dark spell re-indexes by
  time. `WebSimLeds(on_show=, clock=)` is the seam.
- **Logging.** `harness/terrarium_boot.py`'s `configure_logging()` (INFO
  to stderr, merged into control.log by run_stack) is what makes the
  Bit's `tap <dev> cycle <c> beat <w> err <ms>` and `cycle <c> <dev>:
  success|fail` lines and every engine/agent warning visible in a run.
- **CI vs serve markers.** `round loaded:` / `round ended:` are serve-mode
  lines (`_serve_rounds`); a one-shot `--ci` run ends with `Bit
  completed; tearing down`.
- **Live-verified on o2lite 2026-09-08** (`./smoke-test.sh --ci --profile
  profiles/dev-metronome.toml --seconds 60`, runs/<STAMP-FROM-TASK-8>):
  <FILL: joins, taps sent per device, tap error range, successes,
  finale, completion>. Browser gate over o2ws (runs/<STAMP-FROM-TASK-9>,
  mm-tuneshroom `claude/o2ws-link` build served from
  `http://127.0.0.1:8788/app/index.html?node=METRO_PLAYER_NODE`):
  <FILL: joined as player, frames received>. A real phone on the venue
  LAN is still Chris's check.
```

Fill every `<FILL ...>` and `<STAMP...>` from Tasks 8 and 9 before committing; a placeholder left in the deep-dive is a task failure.

- [ ] **Step 2: Timed-cues entry correction**

In `docs/MM_TERRARIUM.md`, inside the `### devicelink/o2_transport.py, control/timed_queue.py, harness/o2_shroom.py` entry, locate the paragraph beginning `**The gap that survived this slice was closed 2026-08-14.**` and append this paragraph after it (the 2026-08-14 spec itself stays as written):

```markdown
**Correction (2026-09-08):** "a Bit never sees the horizon" now has one
exception. `Bit.cue_horizon` is stamped on every loaded Bit by
`GameServer.load_bit` (`control/bit.py`). Cue emission is unchanged and
still never needs it; input judgment does, because a gesture made when a
beat is presented arrives as `at = beat + horizon`. MetronomeBit is the
first consumer; see its entry.
```

- [ ] **Step 3: The 2026-08-20 spec**

In `docs/superpowers/specs/2026-08-20-metronome-bit-design.md`, at the end of section 4 (after the Determinism bullet), add:

```markdown
> **Status 2026-09-08:** the "horizon offset cancels" claim above was
> wrong for input. A tap made at the instant a beat is presented arrives
> as `at = gridpoint + cue_horizon`, so every perfect tap graded +60 ms
> late. Fixed by `Bit.cue_horizon` (stamped by the engine) and
> `t_tap = at - cue_horizon - INPUT_OFFSET_S`. See
> `2026-09-08-metronome-bit-on-o2lite-design.md`.
```

- [ ] **Step 4: This slice's spec Status**

In `docs/superpowers/specs/2026-09-08-metronome-bit-on-o2lite-design.md`, replace the `**Status:**` line with a paragraph naming the two run stamps, the tap error range measured, and any deviation from sections 2.x found during implementation.

- [ ] **Step 5: Lint for em dashes**

Run: `grep -nP "\xe2\x80\x94" docs/MM_TERRARIUM.md docs/superpowers/specs/2026-09-08-metronome-bit-on-o2lite-design.md docs/superpowers/plans/2026-09-08-metronome-bit-on-o2lite.md harness/beat_tapper.py harness/o2_shroom.py harness/websim_leds.py harness/terrarium_boot.py bits/metronome/metronome_bit.py control/bit.py control/engine.py tests/test_beat_tapper.py`
Expected: no output from any file this branch touched (pre-existing em dashes elsewhere in MM_TERRARIUM.md are out of scope).

- [ ] **Step 6: Commit**

```bash
git add docs/MM_TERRARIUM.md docs/superpowers/specs/2026-08-20-metronome-bit-design.md docs/superpowers/specs/2026-09-08-metronome-bit-on-o2lite-design.md
git commit -m "docs: MetronomeBit live-verified on o2lite and o2ws; tap-bias root cause"
```
