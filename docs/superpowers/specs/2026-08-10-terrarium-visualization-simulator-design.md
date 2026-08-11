# Terrarium Visualization Simulator (TEST Room)

**Date:** 2026-08-10
**Status:** Approved (brainstorm session with Chris)
**Canonical architecture:** `docs/control-gameserver-design.md` (this repo)
**Builds on:** `docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md`
(Spec 1 — the `Room`/`RoomType`/`RoomBridge`/`boot()` interfaces this spec
implements the first concrete backend against) and
`docs/superpowers/specs/2026-07-27-devicelink-tuneshroom-simulator-design.md`
(the existing devicelink wire this spec's simulator reuses unmodified).
**Precedes:** a DEMO-room follow-up (the venue array's simulated backend),
out of scope here — see §7.

## 1. Purpose

Spec 1 defined `Room`, `RoomBridge`, and the `boot()` orchestration, but built
no renderer — `RoomBridge`'s light/audio sinks are never populated with
anything but `None` today, and Spec 1's own Post-Implementation Notes flag
that cue-routing into `RoomBridge` was deliberately left unwired, pending a
real consumer.

This spec is that consumer: the **first concrete Room backend**, for
`RoomType.TEST` only. It's a real, ordinary devicelink client — the same wire
a real Tuneshroom would use — rendering a browser-canvas LED view and playing
sound through the Arco server `boot()` already spawns. Closing this loop is
what turns "you can boot into a Room" (Spec 1) into "you can actually watch
and hear a Bit run" — the original ask that started this whole thread.

## 2. Scope

**In scope:**
- `harness/room_simulator.py` — a new driver script, following the
  `led_smoke.py`/`devicelink_smoke.py` convention already established in
  this repo. Connects to Control's existing `DeviceLinkServer` as an
  ordinary devicelink client using a Terrarium-assigned dev id, renders a
  Tuneshroom-shaped `WebSimBackend` LED view, and plays sound through Arco
  — audio is **always on**, not an opt-in flag, since `boot()` already
  guarantees a live Arco server exists before Room binding happens.
- `SimulatorProcess` (new, peer to `ArcoProcess`) — owns the simulator
  subprocess's lifecycle (`start()`/`shutdown()`) for `boot()` to manage
  symmetrically with Arco.
- Wiring `boot()`'s `simulator_factory` to actually spawn this subprocess
  (today it's an injected callable with no real implementation) and
  threading the resulting `SimulatorProcess` handle through to `shutdown()`
  so it tears down alongside Arco and the Bit.
- Closing the cue-routing gap Spec 1 deferred: `DeviceLinkAgent` recognizing
  a connecting dev as `gs.room.bound_dev` and attaching real
  `RoomLightSink`/`RoomAudioSink` implementations to `RoomBridge`; a new
  dispatch branch in `GameServer`'s cue delivery routing `gs.room.bound_dev`
  cues to `RoomBridge.feed_midi(...)` instead of the normal per-role path.
- A reference Room-capable Bit (extending `TestBit`, or a new fixture)
  declaring real Room light/sound instruments via `room_role()` — the act
  that exercises `RoomBridge` end-to-end for the first time, mirroring how
  `TestBit`'s `player` role froze the light-manifest v2 authored shape.
- Offline tests for everything except the actual subprocess/socket/browser
  rendering, matching this repo's existing test philosophy.

**Out of scope (explicitly deferred):**
- `RoomType.DEMO`'s simulated venue array — a focused follow-up spec,
  reusing this spec's devicelink-client architecture rather than inventing
  a second one.
- Any real-hardware Room backend.
- Browser UI polish beyond the bare LED canvas — no embedded device/role
  table (the existing Terrarium Console already covers that; see §4).
- "Shadow mode" — a simulator running alongside real hardware
  simultaneously to visualize what hardware *should* be doing. Named early
  in this project's brainstorming as a future direction, not this slice.
- Any change to `mm-tuneshroom`'s existing browser simulator, which serves
  a different concern (testing player devices) and is untouched by this
  spec.

## 3. Architecture

### The simulator subprocess

`harness/room_simulator.py` is a devicelink client, not a special case:
Control cannot tell it apart from a real Tuneshroom or a future real Room
controller. Concretely:

- Connects to the existing `DeviceLinkServer` (the same server player devices
  already use) and says hello using a dev id passed at launch
  (`--dev sim-room`), assigned by Terrarium rather than self-reported —
  see §4 for why.
- Renders via `shroom_capability()`/`luxaeterna.backends.websim.WebSimBackend`
  — the identical browser-canvas LED view `led_smoke.py` already uses —
  because TEST room's hardware *is* conceptually a Tuneshroom.
- Plays sound through Arco unconditionally: since `boot()` (Spec 1) already
  spawns and waits for Arco before Room binding, there is no "hasn't started
  Arco yet" state for this script to guard against, unlike `led_smoke.py`'s
  optional `--audio` flag.
- Runs until terminated (SIGTERM) — an always-on mode, since it lives for
  the whole Terrarium session rather than completing after a fixed demo
  duration.
- Default bind `127.0.0.1`; LAN exposure (`--host 0.0.0.0`) an explicit
  opt-in, matching every other harness script's trust model in this repo.

### `SimulatorProcess`

Peer to `control/arco_process.py`'s `ArcoProcess`, same shape: `start()`
spawns the subprocess via an injectable `popen` (fakeable in tests exactly
like `ArcoProcess`'s `FakePopen`), `shutdown()` sends SIGTERM. Unlike Arco
(an external upstream binary this repo doesn't own), this subprocess is our
own code — SIGTERM-based teardown is still used, for symmetry with Arco's
shutdown convention rather than because it's the only option.

## 4. Dev Id Assignment

`boot()`'s `_bind_room_fast_path` (Spec 1) calls `simulator_factory()`
synchronously and expects a `dev` string back *before* any real connection
exists — it immediately calls `room_binding.bind(room.room_type, dev)`. This
spec's `simulator_factory` closure therefore:

1. Chooses a fixed dev id (e.g. `"sim-room"`).
2. Spawns `SimulatorProcess` with that id passed as a launch argument.
3. Returns the id immediately — matching Spec 1's existing synchronous
   fast path exactly, no changes needed there.

The simulator subprocess's own devicelink connection, once it comes up,
just says hello using that same id. **No join or Registration Node ceremony
happens for this path at all** — that ceremony (§4 of Spec 1) exists
specifically for the case where Terrarium does *not* know the device's
identity in advance (an admin tapping an arbitrary physical device). Here,
Terrarium chose the id itself, so there's nothing to discover.

## 5. Closing the Cue-Routing Gap

Two changes, both flagged as deliberately deferred in Spec 1's
Post-Implementation Notes:

- **`DeviceLinkAgent`** gains Room-awareness: when a connecting dev equals
  `gs.room.bound_dev`, it builds real `RoomLightSink`/`RoomAudioSink`
  implementations (a `LightSession`-backed light sink, an `AudioBridge`-backed
  audio sink) and calls `RoomBridge.bind(dev, light=..., audio=...)` —
  instead of the normal per-role `DeviceBridge` it builds for player joins.
- **`GameServer`**'s cue delivery gets one new dispatch branch: a cue whose
  `dev` equals `gs.room.bound_dev` routes to `RoomBridge.feed_midi(status,
  d1, d2)` instead of the existing per-device `on_light_cue`/`on_play_cue`
  sinks. This is the `if dev == gs.room.bound_dev: room_bridge.feed_midi(...)`
  branch Spec 1's notes described as "harness glue, not new engine code" —
  now it has a real caller.

### Reference Room-capable Bit

Nothing exercises any of this without a Bit that actually declares Room
instruments. This spec extends `TestBit` (or adds a sibling fixture) with a
real `room_role(RoomType.TEST, light_manifest=..., ugen_manifest=...)`
declaration merged into its `role_table` — the same pattern Spec 1's
`RoomCapableBit` test fixture already established, but with genuine
instrument declarations instead of empty ones, so the simulator has
something real to render.

## 6. Load Sequence Integration

`boot()`'s signature and body (Spec 1, `control/boot.py`) already accept an
injected `simulator_factory`; this spec supplies the real one and threads
the `SimulatorProcess` handle it creates through to `shutdown()` so it tears
down in the same call as Arco and the Bit, rather than being orphaned
alongside them. The exact mechanics of carrying that handle (an addition to
`boot()`'s return tuple, or a small closure-captured teardown callback) are
left to the implementation plan — both are minor, mechanical choices with no
architectural weight.

## 7. Testing

- `SimulatorProcess` is unit-tested exactly like `ArcoProcess`: a `FakePopen`
  seam, no real subprocess spawned in the offline suite.
- `DeviceLinkAgent`'s Room-awareness and `GameServer`'s new cue-routing
  branch are tested with fakes (a fake devicelink connection, a fake
  `RoomBridge`) — no real socket, no real luxaeterna/pyarco, matching this
  repo's "whole suite runs fully offline" property.
- The actual end-to-end experience — spawn the real subprocess, watch LEDs
  move in a browser, hear sound — is manual/visual verification, the same
  treatment `led_smoke.py`/`devicelink_smoke.py` already get. It is not part
  of the automated suite.

## 8. Open Questions (deferred, not blocking)

- Exact mechanics of threading `SimulatorProcess`'s handle from
  `simulator_factory` through to `shutdown()` — a `boot()`/`shutdown()`
  signature detail, resolved during planning.
- Whether `RoomType.DEMO`'s eventual simulator reuses `harness/
  room_simulator.py` directly (parameterized by capability) or gets its own
  script — deferred to that follow-up spec, once DEMO's real hardware
  inventory is better known.
