# Making timed cues load-bearing

**Date:** 2026-08-14
**Status:** Implemented and live-verified on 2026-08-14. On a real Arco over
the o2lite transport: registration, clock sync, and the Room's drone all
worked; a tilt gesture visibly moved both the calling device's light and the
Room's light+drone together, from one shared `at`, with a small fixed-offset
lag consistent with the already-known too-small default `cue_horizon`; the
Room animated on its own with nobody joined (`Bit.cues(at)`); the player
device's clamp counter read 1405, then 1081 on a second run (the Room's own
clamp count was not captured). This document is a point-in-time design
record, not a living doc: for current behavior, constraints and known issues
read `docs/MM_TERRARIUM.md`.
**Repos touched:** `mm-terrarium` only. No luxaeterna change, no mm-tuneshroom
change, no Arco or pyarco change.
**Predecessor:** [`2026-08-12-control-o2lite-and-timed-cues-design.md`](2026-08-12-control-o2lite-and-timed-cues-design.md),
whose success criteria 3 and 4 are recorded as unmet in its own *What is built
but not yet load-bearing* section. This spec closes them.

---

## 1. Why this slice exists

The 2026-08-12 slice built every piece of machinery needed to give a cue a time,
and nothing drives any of it. `control/timed_queue.py`'s `TimedQueue`,
`control/cues.py`'s `LightCue`, `AudioBridge.feed_midi(when=...)`,
`ArcoSynthPool.schedule_at` and the `when` threaded through `_on_light_cue` are
each honestly unit-tested in isolation, and there is no path through the tree on
which one gesture produces one shared target time on both audio and light.

The predecessor spec is explicit about why: its own non-goal, "No new Bit", is
incompatible with its criteria 3 and 4. With `TestBit` as the only Bit in the
tree and no Bit able to compute or return a time, nothing could exercise the
path end to end. This spec's non-goals are written to be compatible with its
criteria, and section 9 says so in those words.

Everything else from that slice is verified working against a live Arco as of
2026-08-13 and is not re-litigated here: registration crosses a real O2 hub,
Control and the device agree on time to 7 ms, frames are delivered and rendered,
and the Room drone sounds. The transport, the shared clock and the rendering
work. Timing is the part that does not.

### 1.1 Six findings from the code that shaped this design

These were established by reading the tree and the pyarco/o2litepy checkout
before any option was proposed. Several contradict the predecessor's framing,
so they are recorded rather than assumed.

**F1. The Room is the only place audio and light coexist.** In the production
path (`harness/terrarium_boot.py`), `AudioBridge.on_grant` is called exactly
once, for the Room device (`devicelink/agent.py:146`). Player devices get light
only; their `PlayCue` samples are device-local and carry no scheduling. So "one
gesture reaches audio and light together" is entirely a Room-path question
today.

**F2. pyarco's scheduler has the same granularity as `TimedQueue`.**
`ArcoSynthPool.poll()` calls `sched.poll()`, which calls
`rtsched.poll(time_get())`, and pyarco's `Scheduler` dispatches **only** from
inside that call. `poll()` is driven once per 44 Hz agent tick via
`AudioBridge.tick()` from `DeviceLinkAgent._tick_audio()`. Splitting light and
audio into two schedulers therefore buys **zero** accuracy: both quantize to the
same tick.

**F3. Splitting them would not drift, it would fail loudly and then silently.**
pyarco's `Scheduler.cause` raises `RuntimeError` when the computed offset is
negative (`pyarco/sched.py:385`; the module global `allow_late` is `False`, and
the module-level `cause()` passes no `late_ok`). `TimedQueue` clamps a past time
and counts it. The two mechanisms have opposite past-time policies. The live
2026-08-13 run clamped 762 of 820 frames, so wiring `schedule_at` today would
raise on the majority of cues, be swallowed by `_render_room`'s `except`, and
produce silent audio dropout while light kept moving. That is worse than drift,
and it is the failure the timing work exists to prevent.

**F4. The existing doubles conceal exactly that.**
`tests/test_arco_synth.py`'s `FakeSched.absolute()` returns a marker tuple and
its `cause()` never raises, while the real `absolute()` returns
`t - current.time` (a float) and the real `cause()` refuses a negative offset.
`control/audio.py`'s `FakePool.schedule_at` records without validating anything.
This is the same shape as the `FakeO2Lite.deliver()` failure that produced
boundary rule 5.

**F5. Today's synchronous coupling guarantees a shared release tick, not a
shared instant.** `_render_room` sends the Room's frame with
`protocol.leds_event(dev, frame)` and no `when` (defaulting to `0.0`, "display
on arrival"), while `_render_frames` passes `when=self._clock() + self._horizon`
(`devicelink/agent.py:277`). So the Room's light already crosses a ~67 ms O2 hop
to the simulator and displays on arrival, while the Room's audio reaches Arco
from that same tick. There is a real, unmodelled Room skew today, and two
schedulers are not its cause.

**F6. The gesture time is already on the wire and already decoded.**
`harness/o2_shroom.py:194` sends `/game/tilt` stamped with `o2lite.time_get()`;
`devicelink/o2_transport.py:251` copies `msg_timestamp` onto the envelope;
`DeviceLinkAgent._handle` decodes it. Only the last two hops discard it. The
websocket path is different and matters: `devicelink/protocol.py`'s `_event`
defaults `timestamp=0.0` and no websocket client stamps a gesture, so that path
has no gesture time at all and needs an explicit fallback.

### 1.2 The decisions those findings forced

- **Room audio and light keep a single anchor and pyarco's scheduler stays out
  of the path entirely.** F2 and F3: splitting gains no accuracy and costs a
  second, contradictory past-time policy. `AudioBridge.feed_midi(when=...)` and
  `ArcoSynthPool.schedule_at` are deleted rather than wired up. Leaving unused
  timing machinery in the tree is what produced this gap.
- **The `Bit` interface grows, deliberately.** It has twice been left alone
  rather than extended, and that restraint is what left both this gap and the
  Room-ambient-cue gap open. One coherent change closes both.
- **`at` is a presentation time, not a dispatch time.** See section 2.

## 2. The timing model

**One rule.** Every cue Control dispatches carries `at`: the intended
**presentation** time, the instant the audience should see and hear the
consequence. It is `origin + cue_horizon`, computed once, in Control.

| Cue origin | `origin` is |
| --- | --- |
| Gesture from a clock-synced device | the device's own timestamp on the inbound envelope |
| Gesture with no usable stamp (websocket path, `timestamp == 0.0`) | Control's tick clock |
| Self-driven cue from `Bit.cues(at)` | Control's tick clock |

**Two output paths, one anchor.** Neither path invents a time of its own.

- **Light.** Every light session lives in Control, the Room's and every
  per-device one alike. The session is fed as early as possible, the frame it
  renders is stamped `when = at`, and the device's own `TimedQueue` holds that
  frame until `at`. The horizon is spent where it is actually needed, on the
  wire, and it is spent exactly once. One exception: a Bit-declared cue further
  out than one horizon is held until `at - horizon` before the session is fed,
  so a future state cannot leak into an intervening breath frame.
- **Room audio.** It goes straight into Arco from Control with no wire, so the
  cue waits on the Room's `TimedQueue` until `at` and then dispatches.

**Why this is stronger coupling than today's single synchronous call.** One
`RoomBridge.feed_midi` call guarantees a shared release *tick*, not a shared
*instant*, and it leaves the light's delivery hop entirely unmodelled (F5).
Under this model both halves are anchored to a single `at` that Control computed
once, and the light half's arrival at `at` is **enforced by the device** rather
than assumed. Nothing schedules independently: pyarco's scheduler is out of the
path, so the whole design has one clock and one queue type.

**Why the frame carries `at` and not `clock() + horizon`.** Adding a horizon to
the cue and another to the frame charges the same constant twice, and a
gesture's light would land at `gesture + 2 x horizon`. Stamping the frame with
the cue's own `at` spends it once. It also repairs the predecessor's complaint
properly rather than arguing it away: the frame's time now *is* the gesture's
time. Frames with no cue behind them (breath-only) keep `clock() + horizon`,
which is correct, because their origin genuinely is Control's tick.

**Where the residual goes, stated plainly.** `at` is honored to within one 44 Hz
tick (22.7 ms) on both halves, because both come off tick-driven drains. After
that, audio adds Arco's block and buffer latency and light adds the device's own
display step. Neither is measured and neither is compensated here. When the
horizon is too small to cover the round trip, the frame arrives past `at`, the
device clamps, and the clamp is counted. That counter is this design's honesty
mechanism and is what the separate horizon-measurement task consumes.

**A note on the TEST room specifically.** Its Room renderer is a simulator
subprocess reached over the device wire, so the Room's light carries a delivery
hop that a venue's in-process Art-Net array will not (boundary rule 4).
Compensating for that hop with a per-sink lead constant would be calibrating
against a simulator artifact. When a real in-process array backend lands, its
lead is its own constant, not this one.

## 3. Architecture

```
device                 Control                                    Arco
------                 -------                                    ----
gesture at t0
  /game/tilt (t0) ---> O2LiteTransport
                       DeviceLinkAgent._handle   (keeps env.timestamp)
                       GameServer.data(gesture_time=t0)
                         at = t0 + cue_horizon
                         handler(dev, args, at) -> cues
                         resolve ROOM -> room.bound_dev
                         stamp when = at
                       DeviceLinkAgent._on_light_cue(dev, ..., when=at)
                         |
                         +-- light: feed session now  --> frame stamped when=at --+
                         |                                                        |
                         +-- Room audio: TimedQueue until at ---------------------|--> Arco
                                                                                  |
  /<dev>/leds (at) <--------------------------------------------------------------+
  TimedQueue holds
  until at, then
  displays
```

Per-tick, `Bit.cues(at)` with `at = clock() + cue_horizon` enters the same
dispatch path at `GameServer.tick`, so a self-driven cue is indistinguishable
downstream from a gesture-driven one.

### Protecting the offline suite

Unchanged and load-bearing: no module under `control/` imports `o2litepy` or
`pyarco`, at module level or anywhere. `control/audio.py` remains the exemplar.
Nothing in this slice adds an import to `control/`; `GameServer` gains a plain
`clock` callable, which is the same pattern `control/audio.py`,
`harness/device_bridge.py` and `DeviceLinkAgent` already use.

## 4. Component design

### 4.1 `control/cues.py`

Gains one export:

```python
ROOM = "@room"
```

A sentinel dev id a Bit uses to target the Room without holding a runtime
device id. `GameServer` resolves it; nothing downstream ever sees it.
`LightCue` needs no change, it already carries `when`.

`PlayCue` stays untimed, and this is deliberate: a device-local sample is
triggered by name and the device owns when it fires, so there is nothing on this
path to schedule. Documented at the type.

### 4.2 `control/bit.py`

One signature change and one new hook.

```python
def verb_handlers(self) -> dict:
    """... A handler is called as handler(dev, args, at), where `at` is the
    absolute O2 time at which this gesture's consequence should be presented
    (Control has already added the installation's cue_horizon). Return a
    list of cues or a str refusal, as before."""

def cues(self, at: float) -> list:
    """Self-driven cues for this tick, in the same vocabulary a verb handler
    returns (plain 4-tuples, LightCue, PlayCue, and the ROOM target).
    Called once per RUNNING tick. Default: nothing to emit."""
    return []
```

`at` is the already-computed presentation time. The Bit never sees
`cue_horizon` and never sees a raw gesture stamp, so `origin + horizon` lives in
exactly one place.

`update(dt)`'s bool contract is untouched. `update()` answers "am I done" and
`cues()` answers "what should happen"; folding both into one return value would
put a union type on the one hook every Bit and most engine tests depend on, and
`GameServer.data()` has already had to be hardened once against
guessing-at-tuple-arity.

### 4.3 `control/engine.py` (`GameServer`)

Construction gains two parameters, both with defaults, so every existing
construction site is unchanged:

```python
def __init__(self, bit_registry, room_binding=None,
             cue_horizon: float = 0.0, clock=time.monotonic):
```

`_MAX_GESTURE_LEAD` is a module constant in `control/engine.py`, alongside the
other engine-level bounds.

`data(dev, verb, args, gesture_time=None)`:

```python
origin = gesture_time if (gesture_time and gesture_time > 0
                          and gesture_time <= self._clock() + _MAX_GESTURE_LEAD) \
         else self._clock()
at = origin + self._horizon
...
cues = handler(dev, args, at)
```

`tick(dt)` calls `update(dt)` first. If the Bit did not signal completion, it
then calls `cues(at)` with `at = self._clock() + self._horizon` and dispatches
the result. Skipping `cues()` on the completing tick avoids dispatching a cue
for a Bit that is already tearing down.

Both routes share one new private `_dispatch_cues(cues, at)`, which is the
existing per-cue guarded loop plus two additions:

- resolve `cue.dev == ROOM` to `self.room.bound_dev`, dropping the cue when no
  Room is bound;
- stamp `when = at` on any cue whose `when` is `None`, so an explicit
  `LightCue(when=...)` from a Bit survives untouched.

Everything the existing loop guarantees is preserved: `data()` still never
raises, an arity-wrong cue from a buggy Bit is still caught per-cue, and a
handler-returned `str` is still checked before the cue list's truthiness.

### 4.4 `control/room_bridge.py`

`feed_midi(status, d1, d2)` is **replaced** by:

```python
def feed_light(self, status: int, d1: int, d2: int) -> None: ...
def feed_audio(self, status: int, d1: int, d2: int) -> None: ...
```

Replaced, not kept alongside them. Under this model a call that fans out to both
sinks at once is the one remaining way to silently lose the anchor, and leaving
it available is the same mistake as leaving `schedule_at` available.

The `RoomLightSink` / `RoomAudioSink` protocols and their fakes keep their
`(status, d1, d2)` shape, so no scheduling policy leaks into a sink.

### 4.5 `devicelink/agent.py`

This module carries the timing.

- `_handle` stops discarding `env.timestamp`; `_on_verb` forwards it to
  `GameServer.data` as `gesture_time`.
- `_room_cues` narrows to Room **audio** only, payload `(status, d1, d2)`,
  drained in `_render_room` into `RoomBridge.feed_audio`.
- New `_light_cues: TimedQueue` for **deferred light-session feeds**, payload
  `(dev, status, d1, d2)`, used only when `at - horizon` is still in the future.
  A gesture cue's `at - horizon` is the gesture time itself, which is already
  past by the time Control sees it, so a gesture cue feeds immediately and never
  touches this queue. That is what keeps the clamp counter meaningful: an
  already-due feed is applied directly rather than pushed, so `TimedQueue` never
  sees it and never counts it as a clamp.
- `_on_light_cue(dev, status, d1, d2, when)` therefore does two things. It
  routes the **light** half either straight to the target session (the Room's
  `_room_bridge.feed_light` when `dev` is the Room's, else
  `bridges[dev].session.feed_midi`) or onto `_light_cues` for the same routing
  later. And when `dev` is the Room's, it additionally pushes
  `(status, d1, d2)` onto `_room_cues` with `when` for the **audio** half.
- `poll()` drains `_light_cues` before `_render_frames` and `_render_room`, so a
  cue released this tick is reflected in the frame rendered this tick. This is
  the same ordering rule `_render_room` already documents.
- New `_pending_at: dict[str, float]`, set at the moment a session is actually
  fed, and consumed by that dev's next render. Two rules, both load-bearing:
  - **Earliest wins.** When several cues feed one dev's session in a single
    tick, `_pending_at[dev]` keeps the smallest `at`. One frame carries all of
    them, so it must not be late for the soonest deadline.
  - **Cleared on every render attempt for that dev, changed frame or not.** A
    cue can feed a session without changing the rendered frame; if the entry
    survived, a stale `at` would attach to some later frame and manufacture a
    spurious clamp. `_render_frames` and `_render_room` therefore pop
    unconditionally and use the popped value only when they actually emit.
- `_render_frames` stamps `when = ` the popped `_pending_at` when there was one,
  else `clock() + horizon`. An explicit `is not None` check, never truthiness:
  `0.0` is a legal O2 time.
- `_render_room` follows the same rule and passes `when` on its `leds_event`,
  which it does not do at all today (F5).
- `clamped` stays `_room_cues.clamped` and its docstring says what it now means:
  Room audio cues that arrived already past their `at`, which is the signal that
  `cue_horizon` is smaller than the upstream delivery time. The device-side
  counter (`ShroomClient.clamped`) reports the downstream half.

### 4.6 `control/audio.py` and `harness/arco_synth.py`

Deletions, per the F2/F3 decision:

- `AudioBridge.feed_midi` loses its `when` parameter.
- `SynthPool` protocol loses `schedule_at`.
- `FakePool` loses `schedule_at` and `scheduled`.
- `ArcoSynthPool` loses `schedule_at` and `_run_scheduled`.
- `tests/test_arco_synth.py::test_schedule_at_delegates_to_the_pyarco_scheduler`
  and the timed-branch tests in `tests/test_audio.py` go with them.

`harness/arco_synth.py` keeps a comment recording **why**, so nobody re-adds it:
`sched.poll()` runs on the same 44 Hz tick as `TimedQueue` so it bought no
accuracy, and `Scheduler.cause` raises `RuntimeError` on a negative offset with
`allow_late = False`, which on the measured live run would have fired on 93% of
cues. `ArcoSynthPool.poll()` stays; it is what pumps o2lite.

### 4.7 `control/boot.py` and `harness/terrarium_boot.py`

Thread `cue_horizon` and `clock` into `GameServer` from the same place that
already feeds `DeviceLinkAgent`. Two clock bases is precisely the bug that made
the 2026-08-13 run dark, so this is pinned by a test rather than a comment (see
section 7).

### 4.8 `bits/test_bit.py`

Where this becomes load-bearing.

- All three handlers take `at`.
- `_on_tilt` returns two cues at the same `at`: the calling device's `cc:74` hue
  lane, and a `ROOM`-targeted `cc:74`. The Room's role declares that lane on
  both its `light_manifest` (aurora hue) and its `ugen_manifest` (FluidSynth
  cutoff), so one tilt moves the Room's colour and the Room's drone timbre
  against one `at`. This is the gesture criterion 4 tests and criterion 11
  observes.
- New `cues(at)` drifts the Room's hue as a deterministic function of the
  already-tracked `_elapsed`, so the Room animates with nobody joined.
  Deterministic because that is what makes it assertable.
- The stale NOTE comment near the Room declaration, which records that the Bit
  interface has no cue-emission mechanism, is removed.

### 4.9 `bits/capture_bit.py`

Two handler signatures gain `at`, unused. Mechanical.

## 5. Data flow, worked

**A tilt on the o2lite path.** The device reads `o2lite.time_get()` as `t0` and
sends `/game/tilt` stamped `t0`. `O2LiteTransport` puts `msg_timestamp` on the
envelope. `DeviceLinkAgent._handle` forwards `t0`. `GameServer` computes
`at = t0 + cue_horizon` and calls `TestBit._on_tilt(dev, args, at)`, which
returns a cue for `dev` and a cue for `ROOM`. `_dispatch_cues` resolves `ROOM`
to the bound Room dev and stamps both `when = at`. For each: `at - horizon`
equals `t0`, already past, so both sessions are fed this tick and both devs get
`_pending_at = at`. The Room's audio cue goes onto `_room_cues` for release at
`at`. This tick's `_render_frames` and `_render_room` emit both changed frames
stamped `when = at`. Each device holds its frame until `at`. At the tick
covering `at`, `_render_room` drains `_room_cues` into `feed_audio` and the
drone's cutoff moves. Light and sound present at `at`, within a tick.

**A tilt on the websocket path.** Identical, except `env.timestamp` is `0.0`, so
`origin` falls back to Control's clock and `at = now + cue_horizon`. The gesture
loses the up-leg compensation it never had, and nothing else changes.

**An ambient cue.** `GameServer.tick` computes `at = clock() + cue_horizon`,
`TestBit.cues(at)` returns a `ROOM` cue, and the rest is the paragraph above.
`at - horizon` equals `now`, so the session is fed this tick and the frame
carries `at`, which is exactly what `_render_frames` already does for breath
frames. Ambient cues are therefore a no-op change to existing render behaviour.

**A Bit-declared future cue.** A handler returns
`LightCue(dev, 0xB0, 74, v, when=at + 0.5)`. `_dispatch_cues` leaves the
explicit `when` alone. `at + 0.5 - horizon` is in the future, so the cue goes
onto `_light_cues` and the session is not fed until then, keeping the future
state out of intervening breath frames. No clamp is counted.

## 6. Error handling

- **No usable gesture stamp.** `origin` falls back to Control's clock when the
  stamp is `0.0` (the websocket path, which never stamps), negative (o2lite
  returns -1 before sync), or absent. One expression covers both transports and
  neither can produce a garbage `at`.
- **Absurd stamp.** A device with a broken clock could otherwise park a cue
  hours out and hold a queue entry through teardown. An `origin` further ahead
  than `_MAX_GESTURE_LEAD` falls back to Control's clock and increments a
  counter on `GameServer`. This is a venue failure mode, not a hypothetical:
  o2lite clock sync makes devices agree, and a device that is wrong about the
  time is wrong loudly.
- **`at` already past.** `TimedQueue` clamps and counts, in Control for Room
  audio and on the device for frames. With pyarco's scheduler out of the path
  this is the design's only past-time policy, which is the point: there is no
  second mechanism with a different opinion.
- **`ROOM` cue with no Room bound.** Dropped and logged **once per Bit load**,
  not once per cue. A 20 Hz tilt stream would otherwise flood the log.
- **`Bit.cues()` raises.** Guarded exactly like `update()`. The tick continues
  and COMPLETING stays reachable, matching the existing rule that a misbehaving
  Bit must never wedge Control.
- **Transport exceptions.** Unchanged. Boundary rule 2's existing guards around
  `on_release` and `on_light_cue` in `control/engine.py` cover the new dispatch
  path too, since it is the same loop.
- **Arco dies mid-run.** Unchanged: `_serve_until_done` fails loud.

## 7. Testing

The offline suite stays fully offline. No module under `control/` imports
o2litepy or pyarco, pinned by the existing test.

New coverage:

- `at` computation across all four origin cases: real stamp, `0.0`, negative,
  and beyond `_MAX_GESTURE_LEAD`.
- A handler actually receiving `at`.
- `ROOM` resolved to the bound dev; `ROOM` dropped when no Room is bound.
- `when = at` stamped on untimed cues, while an explicit `LightCue.when`
  survives.
- `cues(at)` called once per RUNNING tick, skipped on the completing tick, and
  guarded when it raises.
- A per-device cue's `at` arriving as `/<dev>/leds`'s `when`, and a breath-only
  frame carrying `clock() + horizon`.
- A far-future cue not feeding its session until `at - horizon`, and **not**
  counting a clamp.
- The two `_pending_at` rules: two cues in one tick yielding a frame stamped
  with the **earlier** `at`, and a cue that feeds a session without changing the
  frame leaving no stale `at` behind for a later frame to inherit.
- `terrarium_boot.build()` handing `GameServer` and `DeviceLinkAgent` the
  identical clock callable.
- A test pinning the removal of `schedule_at`, so it cannot quietly return.

**The equality test, which is criterion 4.** One gesture with a known stamp, a
fake transport, and fake Room sinks. Assert that the Room's audio sink receives
the cue on the tick covering `at` and not before, that the frame emitted for the
Room carries `when == at`, and that the light sink was fed before that. No Arco,
no pyarco, no O2.

### 7.1 Test-double strictness (boundary rule 5)

Three things the doubles must encode, each because the real thing differs on
that exact dimension:

1. **The fake transport must not invent a timestamp.** Real o2lite delivers
   `msg_timestamp`; the real websocket path delivers `0.0`. The fake must be
   able to deliver both, and the suite must cover the `0.0` fallback, or this
   design ships working only when a device stamps.
2. **The fake device must clamp the way the real one does.**
   `harness/shroom_client.py` already uses the real `TimedQueue`, so the
   requirement is that the tests assert the clamp counter and not only the frame
   contents.
3. **The fake Room sinks must record arrival tick, not just bytes.** "Audio at
   `at`, light before it" is otherwise unassertable.

Nothing new stands in for pyarco, because `schedule_at` is gone. That is the
strictest answer available: the double that could not be made faithful (F4) is
deleted rather than repaired.

### 7.2 Live verification

Needs a real Arco and an interactive TTY (`ArcoProcess` cannot spawn Arco
headless), so it is run by hand.

**RUN ON: MYCOLOGICAL**

```bash
PYTHONPATH=/Users/chris/projects/arco python -m harness.terrarium_boot --transport o2lite --hold --setup-seconds 20
```

Observe: a tilt visibly moves the Room's hue and audibly moves the Room's drone
timbre; with no device joined the Room still animates from `cues(at)`; both
clamp counters are reported at teardown.

## 8. Success criteria

1. A gesture's device stamp reaches the Bit's handler as
   `at = stamp + cue_horizon`.
2. A Bit can name the Room via `ROOM`, resolved by the engine to the bound dev
   and dropped safely when none is bound.
3. `TestBit` emits a Room-targeted cue from `tilt` and a self-driven cue from
   `cues(at)`.
4. One gesture, one `at`, honored on both Room halves, asserted offline with no
   Arco, no pyarco and no O2.
5. A per-device light cue's `when` reaches the wire instead of being dropped.
6. Room frames carry `when`, which they do not today.
7. An `at` already past clamps and counts, with no exception reaching a sink.
8. `AudioBridge.feed_midi(when=)`, `SynthPool.schedule_at` and
   `ArcoSynthPool.schedule_at` are gone, with the reason recorded at the site.
9. `GameServer` and `DeviceLinkAgent` share one clock callable, pinned by a
   test.
10. The offline suite still runs fully offline.
11. A live-Arco run shows gesture-driven Room light and audio moving together,
    and player-free Room animation.

## 9. Non-goals

- **No sub-tick audio placement.** Both paths quantize to the 44 Hz tick.
  Beating that needs timestamped O2 messages into Arco so Arco applies them
  sample-accurately, which requires an Arco-side contract that does not exist
  and is not this repo's call. Named as the future path in section 10.
- **No presentation-simultaneity guarantee** beyond one tick plus an unmeasured
  per-path residual (Arco's block and buffer latency for audio, the device's
  display step for light). Section 2 states this in full.
- **No `cue_horizon` measurement.** That is a separate task. This slice makes
  the clamp counters mean something; it does not set the number.
- **No per-device player audio.** Player devices remain light-plus-local-sample.
- **No DEMO room, no RGBW wire widening, no Dart o2ws client.** Unchanged from
  the predecessor's follow-up list.

**And explicitly not a non-goal: changing the `Bit` interface, and changing
`TestBit`.** The predecessor's "No new Bit, and no Room-targeted ambient cues"
is exactly what made its criteria 3 and 4 unreachable, because with no Bit able
to name a time or a target, nothing could exercise the path. Criteria 3, 4 and
11 above depend on both changes, and nothing in these non-goals forbids them.
That contradiction is the one this spec exists to repair, and it must not be
reintroduced by narrowing scope during implementation.

## 10. Follow-ups this slice deliberately leaves

- **Measuring `cue_horizon`.** The 2026-08-13 run clamped 762 of 820 frames
  against a 60 ms default with ~67 ms measured end-to-end delivery.
  `harness/sync_bench.py` is the tool; every figure it produces is a dev-box
  figure and the venue box does not exist.
- **Timestamped messages into Arco**, the only path to sub-tick audio placement.
  Needs an Arco-side contract and Roger's input.
- **A real in-process Room backend** (Art-Net array). Its light path has no
  device-delivery hop, so its lead is its own constant and the frame-stamping
  rule in section 2 will need a second case.
- **Per-device player audio**, which would make the per-device path a second
  place where audio and light must agree.
- **`RoomType.DEMO`'s backend**, the RGBW wire widening, and the Dart o2ws
  client, all unchanged from the predecessor's list.
