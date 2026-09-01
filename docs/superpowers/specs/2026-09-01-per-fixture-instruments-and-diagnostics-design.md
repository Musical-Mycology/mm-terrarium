# Per-fixture instruments, operator diagnostics, and ABORT resilience

Date: 2026-09-01. Follow-up to the console-load-stabilization slice (PR #80,
merged). Driven by four defects Chris observed live on the TEST room during
the first sustained operator session.

## 1. The defects

1. **The TEST accent fixture is not a real instrument.** Firing the
   diagnostics built-ins (stop/flash/ping) at the Sim Room Accent canvas
   (o2lite dev `sim-room-accent`) does not act on it, and Stop does not stop
   its sound. Cause: both TEST fixtures share `instrument = "dev_strip"`, and
   the engine treats the Room as one logical surface:
   `GameServer._collapse_room_fanout` folds every Room-fixture dev to the
   canonical (first) fixture dev, so a fire aimed at the accent lands on the
   main strip. The Room's audio is a single shared voice (an
   `ArcoSynthPool` voice bound through `RoomBridge`), so no per-fixture
   audio mute is even expressible.
2. **Diagnostics button order.** The Console diagnostics row renders Flash,
   Stop, Ping. Stop is the panic button and must come first: Stop, Flash,
   Ping.
3. **Stale `stop` declarations.** Since the instrument-scripted-functions
   slice, `flash`/`stop`/`ping` are reserved built-in names, but the
   validator refuses them only on instrument declarations. TestBit's
   FunctionTable declares its own `stop`, which shadows the built-in
   `MuteCue` via `fire_function`'s rung 1 whenever TestBit is loaded.
4. **ABORT kills the whole stack.** Console ABORT takes Arco down by design
   (hard stop, post-state NO_ROOM). The persistent Testshroom `ie1` then
   crashes: `reconnect_recheck` runs every loop lap, and with the hub gone
   its `verify_service_ownership` send hits o2litepy's
   `assert False, "cannot send"`. The AssertionError exits the process with
   code 1, `run_stack` treats the child exit as fatal
   (`STACK RUN FAILED at stage 'child-exited'`), and the operator is left
   with a dead server: Room still listed as TEST, Load greyed out, nothing
   loadable. The intended post-ABORT path (Load Room, then Load Bit) is
   unreachable.

## 2. Decisions (made with Chris, 2026-09-01)

- **The Room owns no audio channel.** The previous room-scoped voice was a
  definition error introduced by the Room redesign. Instruments (fixtures)
  are the only things with audio channels; a Room has audio out only because
  a bound fixture's instrument is playing it. Room-targeted audio is
  therefore invalid: the Room's shared MIDI stream feeds light only.
- **TEST is defined by two distinct instruments.** The shared `dev_strip`
  entry is replaced by `dev_strip_main` and `dev_strip_accent`; each fixture
  is its own addressable surface with its own controls.
- **Reserved names are refused on Bit FunctionTables too.** This reverses
  the 2026-08-31 shadowing-by-design decision: a Bit can never shadow
  `flash`/`stop`/`ping`. TestBit's `stop` is deleted.
- **The ABORT crash is fixed in this slice** by hardening the persistent
  device loop against a hub-down send failure.
- **Scope split.** This slice keeps the Room's light side as ONE shared
  LightSession over the whole concatenated profile. Making each fixture a
  fully independent light instrument (its own session over its own slice,
  and a story for cross-fixture light effects) is a named follow-up, see
  section 9.

## 3. Fixture instruments (config)

- New catalog files `instruments/dev_strip_main.toml` and
  `instruments/dev_strip_accent.toml`, identical in capabilities to today's
  `dev_strip` (`light.surface`, `audio.flsyn`; accepted cues
  `midi`/`play`/`solid`/`mute`; same eight scripted functions).
  `instruments/dev_strip.toml` is deleted; the ~28 test references move to
  the new names.
- `terrarium.toml`'s TEST fixtures point at them: `main` gets
  `dev_strip_main`, `accent` gets `dev_strip_accent`.
- No behavioral difference between the two entries today. The point is
  identity: each fixture resolves its own instrument, its own builtins
  entry, and its own surface row in the Console.

## 4. Per-fixture audio channels (transport)

`AudioBridge` already manages voices per dev (`on_grant`/`on_release`), so
the machinery generalizes rather than grows:

- At room bind (`DeviceLinkAgent._setup_room`), every bound fixture whose
  instrument declares an `audio.*` capability gets its own
  `AudioBridge.on_grant(fixture_dev, ...)` and its own `_RoomAudioSink`,
  stored in a per-fixture map. The grant's declaration comes from the Bit's
  ROOM role audio declaration when one is loaded, else a built-in minimal
  declaration sufficient for the flsyn built-ins (note on/off plus
  expression). Unbind/unload releases every fixture voice.
- `RoomBridge` loses its audio sink entirely (`bind` drops the `audio`
  parameter, `feed_audio` is deleted). The Room's shared MIDI stream feeds
  light only. Nothing live depends on the old room voice: TestBit's aurora
  is CC-only, sample plays already go to devices, welcome cues already use
  per-device pool voices.
- Audio routing: a MIDI cue addressed to fixture dev F sounds on F's own
  voice. `_room_cues` payloads gain the dev, and the drain feeds that
  fixture's sink. The start_drone/stop_drone lane follows the granted
  fixture voices (drone on the first audio-capable fixture, documented).
- The light half of a fixture-addressed MIDI cue feeds the shared
  LightSession only when the dev is the canonical fixture. Light MIDI from
  a non-canonical fixture dev is dropped at the transport seam. This is the
  stated limitation of the scope split: an instrument-scripted light
  function fired explicitly at the accent has no light half until the
  per-fixture-sessions follow-up. The diagnostics built-ins are unaffected,
  they use per-fixture overrides and voices, not the shared session.

## 5. Per-fixture targeting and mute (engine)

- **Explicit fixture targeting is never collapsed.** In
  `GameServer.fire_function`, a SURFACE fire at a concrete dev (not `@all`)
  uses that dev as-is; `_collapse_room_fanout` applies only to fanning
  lanes (ROOM targets, `@all`, PLAYERS). Rungs 2/3 resolve per real dev, so
  `@all` reaches every fixture (each fixture's own builtins on its own
  voice/slice) plus every connected device.
- **Mute is per fixture.** `GameServer.muted` holds the real fixture dev.
  On mute of fixture F the transport: installs a blackout override for F,
  applied to F's slice; purges F's queued light cues and F's queued room
  audio cues; silences F's own voice (expression 0 and all-off). Un-mute
  (any non-mute fire at F) restores F only. A Stop at `@all` now mutes each
  fixture individually; there is no canonical-dev special case left in the
  mute path.
- **Overrides become per-fixture.** `_render_room` applies
  `_overrides[fixture_dev]` to that fixture's slice; the whole-frame
  override keyed by the canonical dev is removed. A `SolidCue` resolved to
  a fixture dev affects that slice only. Consequence, accepted: a
  ROOM-addressed `SolidCue` (which resolves to the canonical dev) now
  affects the canonical fixture's slice rather than the whole frame; no
  live Bit currently fires one.

## 6. Console changes

- Diagnostics buttons ordered **Stop, Flash, Ping** in both
  `console/static/functions.js` arrays (`buildDiagRow`,
  `refreshDiagButtons`), with `console/protocol.py`'s builtins ordering and
  `control/builtins.py` docs aligned.
- Each bound fixture already appears in `surface_instruments` under its dev;
  with distinct instruments the picker's per-surface builtins resolve per
  fixture. Picker options for bound fixture devs are labeled with the
  fixture name (e.g. `sim-room-accent (accent)`), so the operator can tell
  fixtures from lobby devices.
- The `surface_instruments["room"]` special key is removed along with its
  first-bound-fixture rule; its only consumer was the pre-PR-#80 diag Room
  option. (Verified: no remaining `.js` consumer.)

## 7. Reserved names (validator and sweep)

- `validate_function_table` refuses `RESERVED_NAMES` for **both** owners.
  The docstring already claims this; the code now matches. A Bit declaring
  `flash`/`stop`/`ping` fails at load with a located error.
- TestBit's `stop` Function is deleted (`bits/test/test_bit.py`). Sweep
  confirms no other stale declarations: no `stop`/`flash`/`ping` in
  `instruments/*.toml` function tables, none on the TUNESHROOM literal, and
  no console-side list that surfaces a Bit-declared stop card once TestBit's
  entry is gone.
- The deep-dive's 2026-08-31 "shadowing by design" paragraph gets a
  superseded note.

## 8. ABORT resilience (harness)

- Every o2lite send inside `harness/o2_shroom.py`'s persistent loop
  (`reconnect_recheck`'s verify, the hello/join resends) is guarded: a send
  failure while the hub is down (o2litepy's AssertionError, or any OSError)
  means "hub away", not death. The device logs the transition once, idles
  (short sleep), keeps polling, and re-runs the ownership re-check when
  o2lite reconnects and stamps a new bridge id. o2litepy itself is not
  modified.
- `run_stack` is unchanged: a child exit stays fatal, because after this
  fix a child exit again means a real crash.
- Intended post-ABORT operator flow, restored: ABORT, devices idle in the
  lobby, Load Room (~15 s Arco spawn), devices reconnect and rejoin, Load
  Bit.

## 9. Deferred (named follow-up)

**Per-fixture light sessions and cross-fixture light effects.** Each fixture
gets its own LightSession over its own slice; the Bit ROOM-role light
manifest contract is reworked (per-fixture manifests, or a manifest slicing
rule); a design for cross-fixture effects (chases, sweeps spanning main and
accent) is part of that slice. Until then the Room renders one shared
session and scripted light functions are only fixture-scoped through
overrides, not through the session.

## 10. Testing

- Unit tests per seam: engine (no collapse on explicit fixture target,
  `@all` fanout including every fixture, per-fixture mute set), transport
  (per-fixture voice grant/release, per-fixture override slicing,
  mute purge and silence per fixture, light-MIDI drop for non-canonical
  fixture devs), validator (Bit-declared reserved name refused, located),
  console (surface_instruments without "room", picker labels, diag order in
  the JS test), harness (hub-down send failure survives, recovery after
  reconnect, simulated with a fake o2lite whose send raises).
- Full suite via `.venv/bin/python -m pytest tests -q`; baseline before
  this slice is 1887 passed, 1 skipped.
- Live verification checklist (operator, TEST room): Stop/Flash/Ping at
  `sim-room-accent` acts on the accent only and Stop silences it; Stop at
  `@all` silences everything; ABORT leaves ie1 alive and Load Room then
  Load Bit recovers; diag row reads Stop, Flash, Ping.
