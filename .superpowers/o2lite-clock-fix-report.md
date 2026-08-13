# o2lite clock-base fix

## Bug

On the o2lite transport, a device never displayed any LED frame. Root
cause: `DeviceLinkAgent` stamped every frame with
`when = self._clock() + self._horizon`, and `harness/terrarium_boot.py`'s
`build()` never gave it anything but its default `clock=time.monotonic`,
regardless of transport. `harness/o2_shroom.py` ticks its device against
the O2 clock (`o2lite.time_get()`, which starts near zero when Arco
boots). `control/timed_queue.py` correctly held every frame until its
`when` arrived, but `when` (real wall-clock time, hundreds of thousands of
seconds) never arrived on a device ticking a clock that starts near zero.
Both ends were correct; the inputs disagreed.

## Fix

`harness/terrarium_boot.py`:

- `build()` gained an optional `clock=time.monotonic` parameter, threaded
  straight into the `DeviceLinkAgent(...)` constructor call (which already
  supported an injectable `clock`). Default matches
  `DeviceLinkAgent.__init__`'s own default exactly, so every existing
  caller (all websocket-mode tests, and the websocket production path)
  sees no behavior change.
- `main()`, inside the existing `--transport o2lite` branch (the only
  place `o2litepy` is already imported), now sets `clock = o2lite.time_get`
  before calling `build(..., clock=clock)`. `build()` itself still never
  imports `o2litepy`; it only receives a plain callable. By the time
  `build()` constructs the agent, `arco.initialize()` (run inside
  `build()`'s own room_audio pool.start()) has already connected and
  clock-synced o2lite, so the clock is valid the moment it's read.

I chose the "optional `clock` parameter on `build()`, supplied only in
o2lite mode" shape exactly as the task suggested: `DeviceLinkAgent`
already had the injectable seam, so this is pure wiring, and it keeps
`control/` and `devicelink/o2_transport.py` free of any o2litepy import
(verified: `python -c "import o2litepy"` fails in this venv, and the full
suite, including `harness/terrarium_boot.py`, still passes clean).

## Other `_clock` consumers

`agent.py`'s breath origin (`:100`), the per-device `DeviceBridge(...,
clock=self._clock)` (`:314`) and the Room's own `build_session(...,
clock=self._clock)` (`:142`), the Room cue queue drain (`:187`), and the
breath value (`:210`) all read the single `self._clock` attribute set at
construction. None of them compares against any externally-supplied time
reference of their own -- they're internally self-consistent as long as
they share one clock, which they now do by construction. Moving the whole
agent to O2 time in o2lite mode moves all of them together correctly; I
agree this is the right (and only sane) outcome.

## Tests (TDD)

Added to `tests/test_terrarium_boot.py`:

1. `test_o2lite_frame_is_released_across_the_shared_clock` -- the crossed-
   clock regression. Builds the real production stack (`build()` with an
   `O2LiteTransport` adopted onto a `FakeO2Lite`, whose `now` starts near
   zero) and a real `ShroomClient` ticked off that same `FakeO2Lite`'s
   `time_get`. Verified RED by temporarily calling `build()` with the
   pre-fix signature (no `clock=` override at all, i.e. today's actual
   behavior: the agent silently defaults to real `time.monotonic()`
   regardless of transport) -- the agent's `/ie1/leds` sends existed, but
   `client.tick()` never released any of them (`assert leds.shown` failed
   with `[]`), which is precisely "frame never being released," not an
   interface error. Restored the fix and added `clock=fake_o2.time_get` to
   the `build()` call -- GREEN.
2. `test_build_passes_the_supplied_clock_to_the_agent` -- `agent._clock is
   fake_clock` when supplied.
3. `test_build_omitting_clock_keeps_the_existing_default` -- calls `build()`
   with no `clock` kwarg at all and asserts `agent._clock is time.monotonic`.

Full suite: 611 passed, 1 skipped (up from 608 passed, 1 skipped -- 3 new
tests), zero failures, zero errors.

## Docs

- `docs/MM_TERRARIUM.md`: rewrote the "Device frame timing is only correct
  on one machine" bullet. It now says the o2lite transport shares one
  clock by construction (works over a real network, not a same-machine
  coincidence), while the websocket transport still relies on a shared
  `time.monotonic` epoch that only holds because the simulator is a local
  subprocess.
- `docs/superpowers/specs/2026-08-12-control-o2lite-and-timed-cues-design.md`:
  same correction in the "second, separate gap" paragraph of *What is
  built but not yet load-bearing*.
