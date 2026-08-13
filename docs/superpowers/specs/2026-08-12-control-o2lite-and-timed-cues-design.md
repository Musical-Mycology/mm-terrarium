# Control on o2lite, and timed cues

**Date:** 2026-08-12
**Status:** Design approved (brainstorm), pending spec review then implementation plan
**Repos touched:** `mm-terrarium` (the device transport, the cue type, the harness client). No luxaeterna change. No mm-tuneshroom change.

---

## 1. Why this slice exists

The Terrarium's device wire is a direct websocket to Control. Arco is not in it,
there is no O2, and there is no clock. `docs/MM_TERRARIUM.md` says so plainly,
and Slice 2's own spec lists "no clock sync" as a structural non-goal.

The consequence is that an event cannot reach audio and light at the same
moment. Control feeds a MIDI byte to `AudioBridge`, which reaches Arco
immediately; it feeds the same byte to a luxaeterna `LightSession`, which lands
on whatever 44 Hz frame comes next. The two are coherent in *value*, which is
what `harness/led_smoke.py`'s shared-bytes pattern buys, but they are not
aligned in *time*, and nothing in either repo currently expresses when an event
is supposed to happen.

This slice makes the Terrarium a real O2 participant and gives a cue a time.

### What is already true, and is easy to miss

The deep-dive's "no O2, no clock sync" line describes the **device wire**. The
Arco connection is a different story, and most of the machinery this slice needs
already exists:

- `o2litepy` is a working Python o2lite implementation, and pyarco uses it:
  `from o2litepy import o2lite` (`pyarco/arco_engine.py:12`).
- `arco.initialize()` **blocks until clock sync completes**:
  `while o2lite.time_get() < 0: ... o2lite.poll()` (`arco_engine.py:104-108`).
- `harness/arco_synth.py:95` calls exactly that. So whenever the Room's Arco
  voice is up, **mm-terrarium is already a clock-synced o2lite client of Arco.**
- pyarco's scheduler already runs on O2 time (`sched.time_get = o2lite_time_get`,
  `arco_engine.py:160`; `sched.rtsched.time = o2lite_time_get()`, line 166), and
  `pyarco/sched.py`'s header describes `cause(delta, ...)` and
  `cause(absolute(t), ...)` as accumulating logical time with "no drift or
  quantization due to execution time or finite polling rates."
- `o2lite.send(addr, timestamp, typespec, *args)` (`o2litepy/o2lite.py:363`)
  carries **the timestamp as the second positional argument of every send**, and
  `msg_timestamp` is parsed off every inbound message (line 729).

So the forward-scheduling machinery is present in the dependency tree. What is
missing is that Control does not offer a service over it, and cues carry no time.

### The invariant this slice preserves

**Exactly one full O2 process; everything else is an o2lite client.** This is
enforced in code, not merely documented:

| Process | Role | Evidence |
| --- | --- | --- |
| Arco server | **full O2** | `o2_initialize(prefs_ensemble_name())`, `arco/server/src/arco.cpp:1294` |
| pyarco (inside Control) | o2lite | `from o2litepy import o2lite`, `pyarco/arco_engine.py:12` |
| Arco's Serpent apps | o2lite | `o2lite_initialize()` in `apps/point`, `apps/tpt`, `apps/test` |
| Browsers (later) | o2lite over o2ws | served by Arco itself, `arco.cpp:1308` |

Arco both accepts o2lite clients (`o2lite_initialize()`, `arco.cpp:1299`) and
serves the websocket bridge itself (`o2_http_initialize`, `arco.cpp:1308`), so a
browser connects to Arco directly with no extra process. Nothing in this slice
adds a second full-O2 process, and nothing later should.

## 2. Goal and success criteria

Run the Terrarium with Control offering `game` on the Arco hub, connect a
clock-synced Python Tuneshroom, and have one gesture drive audio and light
against a single shared time.

Success is met when:

1. **Registration crosses a real O2 hub.** The Python Shroom sends `/game/hello`
   then `/game/join` over o2lite through Arco; a granted join returns the
   composed role blob, still byte-identical to `JoinResult.config`.
2. **Control holds the clock as an asserted property.** `o2lite.time_get()` is
   positive before any device traffic is accepted, and a negative reading is a
   hard error rather than a silent zero.
3. **A cue carries a time, and every path honors it.** One gesture produces one
   target time `T`. The audio path schedules against `T` through pyarco's
   scheduler; the Room's light feeds its `LightSession` at `T`; a joined device
   displays its frame at `T` rather than on arrival. **Not met.** See "What is
   built but not yet load-bearing" below.
4. **The equality is tested offline.** A test asserts that a single cue yields
   the same `T` on the audio and light paths, with no Arco, no pyarco, and no O2.
   **Not met**, for the same reason as criterion 3: see below.
5. **The offline suite still runs fully offline.** No module under `control/`
   imports o2litepy, exactly as none imports pyarco today.
6. `harness/sync_bench.py` reports a measured dev-box delta between the audio
   call and the LED frame, labelled as a dev-box figure.

### What is built but not yet load-bearing

Criteria 3 and 4 are not met as this slice stands. Every piece of machinery
they describe is built and each piece is honestly unit-tested in isolation:
`control/timed_queue.py`'s `TimedQueue`, `control/cues.py`'s `LightCue`,
`AudioBridge.feed_midi(when=...)` and `ArcoSynthPool.schedule_at`
(`control/audio.py`), and the `when` argument threaded through
`_on_light_cue` and `/<dev>/leds`. But nothing in the tree drives any of it
end to end, so "one gesture, one shared `T`, on both audio and light" does
not currently happen anywhere outside a test file. Specifically:

- **No Bit can compute `T`.** Section 5.3's formula is
  `T = gesture_time + horizon`, but `Bit.verb_handlers()` handlers are called
  as `handler(dev, args)` (`control/bit.py:73`, `control/engine.py:162`),
  which hands a Bit neither the inbound envelope's timestamp nor the
  horizon. The timestamp is decoded off the envelope and then discarded
  before it reaches a handler (`devicelink/agent.py:280` decodes it;
  `_on_verb`, which eventually calls the handler, never receives it).
  `BootConfig.cue_horizon` reaches `DeviceLinkAgent`
  (`harness/terrarium_boot.py:126`) but is never injected into a Bit
  either. A Bit has no way to name a `T`, even one that wanted to.
- **No Bit returns a `LightCue`.** `bits/test_bit.py`, the only Bit in the
  tree, returns plain `(dev, status, data1, data2)` 4-tuples from every
  handler. Those decode as `when=None`, "apply on arrival", the
  pre-existing behavior `LightCue` was designed to sit alongside without
  breaking.
- **A per-device timed cue would be silently ignored if a Bit tried
  anyway.** The non-Room branch of `_on_light_cue`
  (`devicelink/agent.py:398-402`) takes `when` as a parameter and never
  reads it: `bridge.session.feed_midi(status, data1, data2)` drops it on
  the floor.
- **Room audio never receives `when` at all.** `RoomBridge.feed_midi` and
  both its `RoomLightSink`/`RoomAudioSink` protocols (`control/room_bridge.py`)
  take `(status, d1, d2)`, no time. `AudioBridge.feed_midi(when=...)` and
  `ArcoSynthPool.schedule_at` therefore have zero production callers; the
  only code that exercises the timed branch is their own unit tests
  (`tests/test_audio.py`, `tests/test_arco_synth.py`).
- **The one production use of a real `when` stamps Control's own clock, not
  the gesture's.** `_render_frames` (`devicelink/agent.py:253`) stamps
  every changed device frame with `self._clock() + self._horizon`,
  Control's render-time clock read at render time, not a time carried from
  the originating gesture. It is real timing, but it is not the `T`
  section 5.3 describes.

**Why this happened.** Section 3's own non-goal, "No new Bit", is
incompatible with criteria 3 and 4. With `TestBit` as the only Bit in the
tree and no Bit emitting a `LightCue` or reading a horizon, nothing can
exercise the `T = gesture_time + horizon` path end to end. That
contradiction should have been caught at spec-review time, not discovered
after 11 tasks of implementation. It is not a coding mistake: every task
did what it said it would, and each piece is honestly tested where it
sits. The spec asked for a fully wired path while also ruling out the one
kind of change, a Bit, that could wire it.

**The architectural decision this leaves for whoever finishes the
wiring.** Room audio and light are currently coupled through a single
synchronous call, `RoomBridge.feed_midi` (`control/room_bridge.py`),
invoked once per cue from `_render_room`'s drain of `self._room_cues`
(`devicelink/agent.py:180-189`). Because it is one call feeding both
sinks, audio and light cannot drift apart: whichever tick releases the
cue, both receive it in that same call. Finishing criteria 3 and 4 for the
Room means giving light and audio independently-scheduled delivery,
`TimedQueue` for light (already built) and pyarco's
`sched.cause(absolute(T), ...)` for audio (also already built), two
different scheduling mechanisms with no shared release point. That
reintroduces exactly the risk this design exists to remove: audio and
light computing the same `T` but firing on it separately, with no
guarantee they land on the same rendered instant. Finishing the wiring is
therefore a real design decision, how to keep two schedulers from
drifting apart, not a mechanical connect-the-dots exercise.

**A second, separate gap, now closed for o2lite: the shared clock.**
`DeviceLinkAgent` now stamps every frame off `o2lite.time_get()` on the
o2lite transport -- `harness/terrarium_boot.py`'s `main()` hands that
clock to `build()`, which threads it into the agent's already-injectable
`clock=` parameter, once the agent is constructed (after
`arco.initialize()` has connected and synced it). `harness/o2_shroom.py`'s
tick loop reads the same `o2lite.time_get()` as `now`. Because o2litepy is
a module-level singleton (section 5.2), both processes are reading the
same clock, and that holds over a real network, not just on one machine --
it is the actual fix, not a same-machine coincidence. The websocket
transport is a different story: `harness/shroom_client.py`'s tick loop
still reads `time.monotonic()`, matching `DeviceLinkAgent`'s default
clock, and two machines' `monotonic()` clocks share no epoch. That still
only works by construction for the locally-spawned room simulator
(`harness/terrarium_boot.py` always spawns it as a subprocess of Control)
and would not hold for a real over-network websocket device. This was a
second, independent reason "one shared time" did not fully hold, not the
same gap as the missing Bit wiring described above; a reader should take
away that the o2lite half is now closed while the Bit-wiring gap above
remains open.

## 3. Non-goals

- **No Dart, no browser.** The Flutter simulator needs a pure-Dart o2ws client
  because `dart:ffi` does not exist on Flutter web. That is a named follow-up,
  not this spec.
- **No DEMO room.** `RoomType.DEMO` still has no backend after this slice.
- **No RGBW wire widening.** Blob-encoded LED frames make it cheap, and this
  spec notes where it lands, but the 36 to 48 decision stays open.
- **No `arcoserver/` build configuration.** o2lite is already enabled in the
  `apps/pytest/server` build in use, evidenced by pyarco connecting through it
  today. The HTTP bridge, enabled iff an `http_root` is configured
  (`arco/server/src/prefs.cpp:209`), is only needed by the browser follow-up.
- **No venue-box timing figures.** The box does not exist. Every number this
  slice produces is a dev-box figure and is labelled as one.
- **No new Bit, and no Room-targeted ambient cues.** `TestBit` stays the fixture.

## 4. Architecture

```
                    +---------------------------+
                    |  Arco server (full O2)    |
                    |  clock master             |
                    |  o2lite enabled           |
                    +---------------------------+
                       ^                    ^
              o2lite   |                    | o2lite
                       |                    |
        +--------------+--+          +------+---------+
        | Control process |          | Python         |
        |  pyarco owns    |          | Tuneshroom     |
        |  the connection |          | (harness)      |
        |  services       |          |  WebSim LEDs   |
        |  "actl,game"    |          +----------------+
        +-----------------+
```

### Protecting the offline suite

The deep-dive calls the fully-offline test suite load-bearing and says it is
pinned by tests. Making Control an O2 participant threatens that directly, so
this slice follows the precedent already set for pyarco: **o2lite lives behind
an injected transport that `control/` never imports.** `control/audio.py` proves
the pattern by never importing pyarco while `harness/arco_synth.py` supplies the
real backend. The `game` transport and the audio scheduler get the same
treatment.

## 5. Component design

### 5.1 The o2lite transport (`devicelink/`)

Slice 2's server/agent split pays off here as designed. `DeviceLinkAgent`
requires exactly three things of its transport, at `devicelink/agent.py:150`,
`151`, and `383`:

```
drain_new_clients()  -> list
drain_inbound()      -> list of (client, msg)
send(client, msg)
```

So this is a transport swap, not a rewrite. `GameServer`, the agent brains,
`DeviceBridge`, `RoomBridge`, cue routing, and the `Bit` interface are untouched.

`drain_new_clients()` has no o2lite equivalent: there is no connection to
accept, because a device announces itself with `/game/hello` and is anonymous
until it does. The o2lite transport implements it as a no-op returning an empty
list, which is already what `agent.py:150` tolerates on a quiet tick.

**The package stays `devicelink/`.** It is the device link regardless of wire,
and keeping `DeviceLinkServer` alive gives the offline suite a real in-process
transport to test against. The o2lite transport lands beside it.

**The client map moves down into the transport.** Today `_send(dev, msg)`
(`agent.py:379`) looks up `self._clients` and calls `server.send(client, msg)`.
Under O2 there is no connection object, only an address, so the interface
becomes `send(dev, msg)` and the websocket transport keeps its own
dev-to-connection map privately. This makes the agent transport-agnostic in the
way it already claims to be.

**Dev ids become O2 service names.** The device offers `ie<N>` and Control
addresses `/ie<N>/leds`. o2litepy refuses a service name longer than 31
characters (`o2lite.py:697`), so `hello` validates the dev id rather than
letting it fail silently later.

**Blobs replace the JSON shims.** The role config becomes an `O2blob`
(`o2lite.py:254`) rather than a JSON dict argument, and LED frames become a blob
of bytes rather than 36 separate ints (`devicelink/protocol.py:86`). The current
protocol docstring already anticipates this as Design Rule 5. This is also where
a future RGBW widening lands naturally, since a byte blob has no per-int cost.

### 5.2 Control as a guest on pyarco's connection

o2litepy ships a module-level singleton (`o2lite = O2lite()`, `o2lite.py:1080`),
and pyarco already calls `set_services("actl")` (`arco_engine.py:98`).
`set_services` **replaces rather than appends**: its body is
`self.services = services` (`o2lite.py:707`) over a comma-separated string.

So a naive `set_services("game")` from Control would **silently drop `actl`**,
and Arco's control replies would stop arriving. Control and pyarco therefore
share one connection and one services string.

Boot order becomes:

1. Start Arco (`control/arco_process.py`, unchanged).
2. `arco.initialize()` connects o2lite and blocks for clock sync.
3. Control registers its `/game/*` handlers via `method_new` (`o2lite.py:907`)
   and sets services to `"actl,game"` as one string.
4. Assert `o2lite.time_get() > 0` before accepting device traffic.

This settles the ownership question the deep-dive leaves ambiguous: the
connection is pyarco's, and Control is a guest on it offering `game` alongside.
It also means the clock is available before any device traffic, which 5.3
depends on.

A consequence worth stating: this makes Arco a hard prerequisite for **any**
device traffic, where today `devicelink/` runs with no Arco at all.
`harness/terrarium_boot.py`'s `build()` already made audio unconditional, so no
existing driver changes, but the fake transport becomes the only no-Arco path.

### 5.3 Timed cues

**Absolute O2 time, not deltas.** `control/cues.py` grows a `when` field holding
absolute O2 seconds. Absolute, because it is the one shared reference both paths
already have and because `sched.cause(absolute(t), ...)` consumes it directly.
Deltas would need a per-hop base and would reintroduce exactly the drift
`sched.py`'s logical-time model exists to avoid.

**The device stamps the gesture; Control adds the horizon.** `T = gesture_time +
horizon`, where `gesture_time` comes from the clock-synced device rather than
from Control's receipt. This is Design Rule 4, timestamps at the source, and the
telemetry-capture slice already set the precedent with `/game/capture`'s `t0`.
It is also the only arrangement in which jitter on the way up does not become
jitter in the output. Control clamps a `T` that has already passed.

**The horizon is measured, not chosen.** It must clear the 44 Hz frame
quantization (22.7 ms) plus Arco's block and buffer latency plus network time.
No venue-box figures exist, so the horizon is a configured value with a
measurement tool behind it and never a literal in the source. See section 8.

The horizon is **one installation-wide constant on `BootConfig`**, not a
per-cue or per-role value. A single number is what makes "audio and light read
the same time" checkable; a per-cue horizon would let two cues in one gesture
land on different frames and would make the clamp counter in section 6
uninterpretable.

**The apply-at-time queue lives in mm-terrarium, shared, and is generic over its
payload.** It holds `(T, payload)` and releases it at the tick covering `T`. It
goes here rather than in luxaeterna because boundary rule 3 puts timing policy
upstream of the renderer.

It has to be payload-generic because the two sides hold different things, and
this slice does **not** change which:

- **Control** holds `(T, midi)` and feeds a `LightSession` at `T`. This is the
  Room's path, and it is in-process at zero hops per boundary rule 4.
- **The device** holds `(T, frame)` and lights its LEDs at `T`. Today Control
  renders every joined device's light and streams finished frames as
  `/<dev>/leds`; the device does not run a `LightSession` at all. So `/<dev>/leds`
  gains a timestamp and the device displays on time rather than on arrival.

Boundary rule 4 anticipates a future in which a Tuneshroom runs its own renderer
and takes `/light/midi` at 2 hops. That is a different architecture from the one
in the tree, and moving to it is out of scope here. The queue being
payload-generic is what keeps that move cheap: the device swaps which payload it
holds, not how it schedules.

**Audio schedules through the injected pool.** `control/audio.py` must never
import pyarco, so `AudioBridge` cannot call `sched` directly. `ArcoSynthPool`
grows `schedule_at(T, fn)` wrapping `sched.cause(absolute(T), ...)`; the fake
pool records `(T, call)` pairs. The offline property survives intact.

**`when=None` still means apply on arrival.** The change is additive, so every
existing test keeps passing unchanged.

### 5.4 The Python simulated Tuneshroom

`harness/shroom_client.py` was built for this moment; its docstring already says
the transport half sits in `main()` "because that is the part o2lite replaces."

One structural change. Today `ShroomClient.handle(msg)` decodes an envelope and
dispatches by address itself. Under o2lite, `method_new` does the dispatch. So
the payload logic (`_on_role`, `_on_leds`, `_on_release`) stays exactly as it is
and becomes the shared target: the JSON transport keeps reaching it through
`handle()`, and the o2lite transport registers each address straight onto it.
That preserves the socket-free testability the module was designed around.

The rest is assembly of parts that already exist. It joins `TEST_PLAYER_NODE` to
take `TestBit`'s scored `player` role, which is the role carrying the real
light-manifest v2 declaration and both cc lanes. It displays through luxaeterna's
`WebSimBackend`, which `harness/room_simulator.py` already wires up via
`WebSimLeds`. It holds each inbound frame until its timestamp using the shared
queue from 5.3. It stamps its own gestures with `o2lite.time_get()`.

Note the `--setup-seconds` trap the deep-dive already records: a scored role is
refused once the Bit is `RUNNING`, so the driver must hold in `SETUP` long
enough for this client to join.

For a gesture source on a laptop with no accelerometer, it uses a **synthetic
tilt sweep** reusing `led_smoke.py`'s ping-pong ramp shape, because it is
deterministic and therefore assertable, with `--hold` to keep it watchable.

## 6. Error handling

- **Clock not synced.** `o2lite.time_get()` returns -1 before sync. A cue
  scheduled against -1 is garbage, so a negative reading is a hard error, never a
  silent zero. pyarco already blocks for sync at init, so this is an assertion
  rather than a wait.
- **`T` already in the past.** Clamp to apply-now and **count it**. A rising
  clamp count is precisely the signal that the horizon is too small, which makes
  the measured horizon self-reporting rather than something anyone has to
  remember to re-measure.
- **Arco dies mid-run.** o2lite loses the hub and devices go quiet. Fail loud and
  abort the Bit: silent degradation in a venue is worse than a visible stop.
- **Transport exceptions.** Boundary rule 2 already holds via the existing guards
  around `on_release` and `on_light_cue` in `control/engine.py`. The o2lite
  transport inherits them unchanged.

## 7. Testing

- **The offline suite stays fully offline.** No module under `control/` imports
  o2litepy, the same rule that already keeps pyarco out. The suite runs with no
  Arco, no pyarco, and no O2.
- **`FakeO2Lite`** records `(addr, timestamp, typespec, args)`, making timed
  sends assertable with no hub.
- **The plumbing proof:** one cue, assert equal `T` on the audio and light paths.
- **Clamp behavior:** a past `T` applies immediately and increments the counter.
- **Services string:** a regression test that Control's registration produces
  `"actl,game"` and never drops `actl`. This one exists because the failure mode
  is silent.
- **`harness/sync_bench.py`** reports measured dev-box deltas between the audio
  call and the LED frame, in the same terms `render_bench.py` already uses.
- **The live-Arco integration test is opt-in** and skipped in CI, following the
  `importorskip` precedent in `tests/test_array_smoke.py`.

## 8. Open questions

- **The horizon value.** Unknown until measured, and the measurement is a dev-box
  figure that does not carry to the venue box. `sync_bench.py` produces it; the
  clamp counter reports when it is wrong in production.
- **Arco is clock master only once its audio is up.** `arco.cpp:1295` notes
  `o2_clock_set` is called by `audio_initialize`. Combined with the documented
  macOS trap where only the first client after a server start gets working audio,
  clock availability is coupled to Arco's audio starting cleanly. The readiness
  assertion in 5.2 step 4 makes the failure visible, but the underlying coupling
  is upstream and not fixed here.
- **Whether the shared apply-at-time queue eventually belongs in luxaeterna.** If
  the Dart client needs the same logic, a written contract matters more than a
  shared Python module, and that is the moment to revisit.

## 9. Follow-ups this slice deliberately leaves

- **The Dart o2ws client**, which turns the existing Flutter simulator into a
  real clock-synced O2 participant. The riskiest remaining piece, written against
  O2's text encoding (`arco/../o2/src/websock.h`) with reference clients at
  `o2/test/www/o2ws.js` but no Dart precedent.
- **`RoomType.DEMO`'s backend**, the simulated venue array.
- **The RGBW wire widening**, 36 to 48, across `devicelink/protocol.py`,
  `shroom_capability`, luxaeterna's WebSim backend, and
  `mm-tuneshroom/lib/link/envelope.dart`.
- **Room-targeted ambient cues.** `Bit.update(dt)` still has no way to emit a
  cue, so a Room's light still cannot animate on its own. Note that the routing
  half is already complete: `devicelink/agent.py:358` sends any cue whose `dev`
  equals the Room's bound dev straight into `RoomBridge.feed_midi`. What is
  missing is only a Bit's ability to name the Room as a target.
