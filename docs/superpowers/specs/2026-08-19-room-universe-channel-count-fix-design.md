# Fix: DEMO Room's light never renders (`Universe` is hardcoded to 512 channels)

**Date:** 2026-08-19
**Status:** Draft, awaiting review. Live-verified against a real Arco: not yet
(this spec exists *because* a live-verify run surfaced the bug).
**Prior slices:** the DEMO room and Block build-out unit (2026-08-19), which
made DEMO's profile real-scale (864 px / 2592 channels) and is what exposed
this defect.

## 0. The defect

`PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m harness.run_stack
--room-type DEMO --devices 1 --ci --seconds 45` against a real Arco: audio
opened correctly, but the Room's light never rendered a single frame. Every
tick threw

```
luxaeterna.exceptions.ChannelError: Range 0:2592 exceeds universe bounds
```

(2172 occurrences over 45s), caught and silently skipped by
`devicelink/agent.py`'s `_render_room()`:

```python
try:
    self._room_light.session.render_into(universe)
except Exception:
    logger.exception("Room render failed; skipping frame")
    return
```

## 1. Findings from the code

- **F1.** `devicelink/agent.py:172`'s `_setup_room()` unconditionally
  constructs the Room's light sink with a bare `Universe()`:
  `self._room_light = _RoomLightSink(session, Universe())`.
- **F2.** luxaeterna's `Universe` (`luxaeterna/universe.py`) is a fixed
  512-channel `bytearray` buffer. Every bound check (`set`, `get`,
  `set_range`, `fill`) and the initial allocation reference the module-level
  `DMX_CHANNELS` constant (512), not an instance-level size.
  `engine.render_into()` (`luxaeterna/synth/engine.py:67`) calls
  `universe.set_range(0, to_dmx_bytes(surface, self.cap.color_order))` with
  however many bytes the surface renders to — for DEMO, 2592 — which always
  raises against a 512-channel buffer.
- **F3.** This was latent, not new. Before the Block build-out slice
  (2026-08-19), `RoomProfile.__post_init__` capped the **whole profile** at
  170 px (`_MAX_PROFILE_PIXELS`), so nothing could construct a `RoomProfile`
  wider than one DMX universe — F1's hardcoded `Universe()` was never
  exercised past that size. That slice moved the cap from whole-profile to
  per-`RoomBlock`, specifically so DEMO could declare its real 864 px scale —
  which is exactly what now overflows F1's buffer.
- **F4.** `_render_room()` already reads `frame =
  bytes(universe.get_frame()[:self._room_profile.channel_count])`
  (`devicelink/agent.py:304`) — the slice already assumes a buffer at least
  `channel_count` wide. Before this fix that slice was a silent truncation of
  a too-small buffer (masked entirely by the render exception above, since
  `render_into` never got far enough to populate it); after this fix it is a
  correctly-sized no-op slice.
- **F5.** `self._room_light.universe` is **never** handed to any luxaeterna
  output backend or `OutputLoop`/`MultiUniverseOutputLoop` — it is a private
  buffer read only inside `_render_room()`, sliced per-fixture, and shipped
  over `devicelink`'s own JSON-envelope protocol. None of the Art-Net/sACN/
  serial-Enttec backends, which do assume exactly 512 channels per wire
  universe, ever see this object.
- **F6.** luxaeterna already has multi-universe machinery for "one logical
  light spans more than 512 channels" — `PixelSpan`/`UniverseSet`
  (`luxaeterna/pixelspan.py`, `luxaeterna/universeset.py`), used standalone by
  `harness/array_smoke.py` for the real Art-Net venue array. It does **not**
  fit here: `PixelSpan` requires `channels_per_pixel` to divide 512 evenly
  ("Three-channel RGB strips do straddle; supporting them is out of scope by
  decision" — its own docstring), and DEMO's profile is `color_order="GRB"`,
  3 channels/pixel. `control/room_profile.py`'s own header comment
  independently confirms this route was already considered and deferred
  (non-goal N5 of the prior spec) for the *real hardware output* path — this
  fix does not reopen that; it only fixes the *internal render buffer*, which
  is a different, private concern (F5).
- **F7.** `luxaeterna` is consumed by mm-terrarium as a live editable install
  (`pip install -e`) pointing at `~/projects/luxaeterna`'s `main` checkout —
  confirmed via `pip show luxaeterna` (`Editable project location:
  /Users/chris/projects/luxaeterna`). An edit there takes effect immediately;
  no publish or version bump is needed for mm-terrarium to pick it up.
- **F8.** No test in either repo's offline suite drives `Universe.set_range`
  or `_render_room()` above 512 channels. 1056 mm-terrarium tests plus
  luxaeterna's own suite stayed green through the Block slice landing; only
  the live Arco run caught this.

## 2. The decided fix

**Widen `Universe` to accept an optional `channel_count`, default unchanged,
and pass the Room profile's real size at construction.** Two repos, two small
edits, additive and backward-compatible in both.

### 2.1 luxaeterna: `luxaeterna/universe.py`

```python
def __init__(self, universe_id: int = 0, channel_count: int = DMX_CHANNELS) -> None:
    self.universe_id = universe_id
    self._channel_count = channel_count
    self._data = bytearray(channel_count)
    self._lock = threading.Lock()
    self._dirty = True
```

- Add `_channel_count` to `__slots__`.
- Replace every bounds-check/allocation use of the module-level `DMX_CHANNELS`
  inside the class body with `self._channel_count`: `set`'s and `get`'s range
  checks, `set_range`'s `end > DMX_CHANNELS` check, `fill`'s default `count =
  DMX_CHANNELS - start` and its own bounds check, `reset`'s reallocation, and
  `__len__`.
- No other file in luxaeterna changes. Every existing call site
  (`UniverseSet.__init__`, `luxaeterna/__init__.py`'s smoke check,
  `websim_demo.py`, mm-terrarium's per-device `Universe()` calls in
  `devicelink/agent.py`) constructs with no `channel_count` argument and gets
  today's exact 512-channel object — this is purely additive.
- Docstring gains one sentence: a non-default `channel_count` is for a caller
  managing its own internal render buffer (e.g. a simulator rendering a
  wider-than-512-channel logical surface before slicing it up itself); real
  DMX-512 wire universes — the Art-Net/sACN/serial-Enttec backends — still
  assume exactly 512 and are untouched by this change (per F5/F6, this
  instance never reaches them).

### 2.2 mm-terrarium: `devicelink/agent.py`

One-line change at the `_setup_room()` call site (line 172):

```python
self._room_light = _RoomLightSink(
    session, Universe(channel_count=self._room_profile.channel_count))
```

`self._room_profile.channel_count` is already computed
(`control/room_profile.py`) as the sum of every fixture's pixel count times
its `color_order` width — 2592 for DEMO, 180 for TEST — so no new value needs
computing. No change to `_render_room()`'s existing slicing logic (F4) — it
already expected a buffer this size.

## 3. Non-goals

- **No RGBW widening, no PixelSpan/UniverseSet adoption for this buffer.**
  F6 explains why they don't fit; this fix does not reopen that question.
- **No change to any real output backend** (Art-Net, sACN, serial-Enttec) or
  to `harness/array_smoke.py`'s standalone multi-universe wiring — untouched,
  per F5.
- **No versioning/publish step for luxaeterna.** Per F7, a direct commit to
  its `main` is sufficient; mm-terrarium's editable install picks it up
  immediately with no further step.

## 4. Testing

- **luxaeterna:** no `tests/test_universe.py` exists today — `Universe` is
  only exercised indirectly (`tests/test_output_hook.py`,
  `tests/test_universeset.py`, `tests/synth/*`). Add it, covering
  construction/bounds-check cases for a non-default `channel_count` — e.g. a
  2592-channel `Universe` accepts a 2592-byte `set_range(0, ...)` that a
  default-512 `Universe` would reject, and still rejects a range past *its
  own* wider bound — plus the zero-arg constructor path (pin that it's
  unchanged: still exactly 512 channels).
- **mm-terrarium (`tests/test_devicelink_agent.py`):** this is the offline
  gap identified in F8 — nothing today drives `_render_room()` above 512
  channels. Add a DEMO-flavored sibling of the existing
  `_room_ready_game_server()` helper (binds `RoomType.DEMO`'s `array` fixture
  instead of TEST's `main`), and a test that builds a real
  `DeviceLinkAgent`, calls `agent._render_room()`, and asserts it does not
  raise and produces a frame consistent with the 2592-channel profile —
  mirroring `test_render_room_sends_leds_event_when_frame_changes` but sized
  to DEMO instead of TEST. This is a regression test for exactly this bug
  class: a `RoomProfile` wider than one DMX universe must render, not throw.

## 5. Live-verify plan (per this repo's convention: offline-suite-green is not "done")

Re-run the exact reproduction command that surfaced this bug:

```bash
cd ~/projects/mm-terrarium (main, once both fixes have merged, or a fresh worktree off it)
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m harness.run_stack --room-type DEMO --devices 1 --ci --seconds 45
```

Success: no `ChannelError` in `control.log` across the full 45s run, and the
DEMO room simulator canvas shows the Room's light actually rendering (not
just audio). The player device may still hit the pre-existing, documented,
unrelated headless clock-sync defect noted in `docs/MM_TERRARIUM.md`'s "Not
yet built" section — out of scope for this fix, don't chase it.

## 6. Process

1. Edit and commit directly to `luxaeterna`'s `main` (`~/projects/luxaeterna`)
   — §2.1 plus its test.
2. Branch mm-terrarium off `main` for §2.2 plus its test, normal PR flow.
3. Live-verify per §5 once both have landed.
