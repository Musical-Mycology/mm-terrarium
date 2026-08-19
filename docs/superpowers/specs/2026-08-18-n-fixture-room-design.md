# The N-fixture Room (Spec C)

**Date:** 2026-08-18
**Status:** Draft, awaiting review. Live-verified against a real Arco: not yet.
**Prior slices:** Spec A (Room panel and Room fixtures, 2026-08-17, PR #31) and
Spec B (Bit-declared triggers and cue scripts, 2026-08-17, PR #34), both merged.
This is the slice Spec B's section 4.2 deferred by name.

A real venue Room is N light fixtures around a physical room, each its own
o2lite client with its own unique service name, because o2lite service names
are first-come-first-served with no client-side error on a collision. Today
the Room is shaped as exactly one bound device. This spec generalizes the
Room to N fixtures rendered from one shared stream.

---

## 1. Findings from the code

- **F1.** `Room.bound_dev` is a single string (`control/rooms.py:68`);
  `room_role()` hardcodes `capacity=1`; `RoomBindingRegistry` maps one dev per
  `RoomType`; `RoomProfile` declares one surface (`room_test`, 60 px, three
  zones); `DeviceLinkAgent` holds one `_room_dev` / `_room_bridge` /
  `_room_light` and renders once in `_render_room()`.
- **F2.** `GameServer._resolve_target` (`control/engine.py:254`) already
  returns a list, as Spec B promised, so no Bit's trigger declaration changes
  in this slice. `_resolve_dev` (the `cues.ROOM` sentinel resolution) returns
  a single dev and must widen.
- **F3.** Every luxaeterna light ugen renders over `ctx.positions`, a
  normalized 0.0 to 1.0 pixel-position axis for its target zone
  (`luxaeterna/synth/ugens.py`, `Bloom` uses it for its gaussian). The
  pipeline is spatial; `aurora` merely paints uniformly via `Fill`. A
  scrolling gradient is a new preset, not a renderer change.
- **F4.** `RoomProfile.channel_count` is the single source of truth for frame
  width, honored by the agent's slice, `ShroomClient.expected_channels`, and
  both simulator entry points. A wrong-width frame is dropped, never
  truncated.
- **F5.** `RoomBridge` holds exactly one light sink and one audio sink; the
  light sink is the Room's one `LightSession`. `harness/terrarium_boot.py`
  hardcodes `SIM_DEV = "sim-room"` and spawns one simulator subprocess.
- **F6.** The checked-in diagrams on this branch imply flows ahead of the
  code, and two of them encode the singular Room this spec removes:
  `cue-path.d2` labels its dispatch edge "ROOM resolved to bound dev" and
  `boot-teardown.d2` shows one "Room simulator" pop slot. Separately,
  `player-flow.seq` shows the `/ie1/role` composed config blob delivered over
  the wire, which nothing implements on the o2lite transport; that flow is
  out of scope here (non-goal N8) and the diagram stands as aspiration.
- **F7.** Carried from Spec B's section 4.2 and not re-litigated: lights are
  not wrapped in Arco ugens. Lux Aeterna and Arco are siblings; light traffic
  transits Arco as a relay. N fixtures is a light-side concept: a new fixture
  costs an o2lite client, a unique service name and a binding. An audio
  instrument costs a channel from a pool and no client, so the Room's audio
  stays single-instance in this slice.
- **F8.** Roughly 44 test references to `bound_dev` and `capacity=1` pin the
  single-fixture shape and update with it.

## 2. The decided model: one logical light, N surfaces, namespaced zones

Decided during brainstorming, recorded here as the frame for everything below.

The N fixtures are one logical light. One Room MIDI stream feeds one
`LightSession` built over a **concatenated virtual surface**: the fixtures'
pixel runs laid end to end, in declaration order, with every fixture's zones
offset into the run and renamed `<fixture>.<zone>`. Each render tick produces
one frame for the whole virtual surface, which the agent slices at fixture
boundaries and ships to each fixture's own o2lite client, all stamped with
the same `when`.

What this buys, concretely:

- **Cross-fixture spatial effects work with zero renderer changes.** An
  instrument targeting `primary` (luxaeterna synthesizes it for the whole
  surface) sees one continuous position axis spanning every fixture. A
  scrolling rainbow renders as one gradient across all 90 pixels and crosses
  the physical gap between fixtures without a seam in space or time. The
  per-fixture-sessions alternative cannot do this at all: each session's
  positions would restart at 0.0, so each fixture would show its own complete
  copy of the effect.
- **`RoomBridge` keeps exactly one light sink and one audio sink.** The
  fan-out is in frames, not in MIDI or sessions.
- **Fixture-specific content remains reachable through zones.** An instrument
  targeting `accent.glow` renders only on that fixture's pixels. This is the
  hybrid: shared stream, zone-level spatial addressing.

Two invariants follow and are load-bearing:

- **Declaration order is physical order.** Position 0.0 is the first pixel of
  the first declared fixture. A bottom-to-top effect means the profile
  declares fixtures bottom-to-top. A venue whose wiring order disagrees with
  visual order fixes the declaration, not the effect.
- **One `color_order` per Room this slice.** The single session renders one
  channel stream; per-fixture color orders would need per-slice conversion.
  A mixed-order profile is rejected at construction (non-goal N4).

## 3. The declaration: `RoomFixture` and the widened `RoomProfile`

`control/room_profile.py`, still pure, still imports nothing outside stdlib
and `control/`.

```python
@dataclass(frozen=True)
class RoomFixture:
    name: str                      # unique within the profile; a path-safe token
    pixel_count: int
    color_order: str
    zones: tuple[RoomZone, ...]    # fixture-local start offsets


@dataclass(frozen=True)
class RoomProfile:
    surface_id: str
    fixtures: tuple[RoomFixture, ...]   # declaration order IS physical order
```

Derived properties, each with one job:

- `pixel_count`: sum over fixtures (TEST: 90).
- `channel_count`: `pixel_count * 3`, keeping its F4 single-source-of-truth
  role for the whole virtual surface.
- `zones`: the namespaced union. Each fixture's zones are offset by the
  fixture's start pixel and renamed `<fixture>.<zone>` (`main.left`,
  `accent.glow`). Every zone gets the prefix, including single-zone fixtures;
  uniformity beats brevity.
- `fixture_slices()`: `(name, channel_start, channel_count)` per fixture, in
  declaration order, for the agent's render slicing and each client's
  `expected_channels`.

Validated in `__post_init__`, failing at import or boot and never mid-run:
at least one fixture; unique fixture names; one shared `color_order`; total
pixels within a single universe (170 px RGB, the same cap comment the module
carries today); zone runs within their fixture.

**TEST declares two asymmetric fixtures**, the smallest N that exercises
fan-out, distinct service names, distinct frame widths and namespaced zones,
with asymmetry so identical-fixture assumptions cannot hide:

| fixture | pixels | zones | channels |
|---|---|---|---|
| `main` | 60 | `left`/`center`/`right`, 20 px each (the existing surface) | 180 |
| `accent` | 30 | `low`/`high`, 15 px each | 90 |

Existing zone names therefore change: `left` becomes `main.left`. TestBit's
Room `light_manifest` and every test naming a Room zone update with it.

## 4. `Room`, binding, and roles

**`Room`** (`control/rooms.py`): `bound_dev: str | None` is replaced by
`bound: dict[str, str]` (fixture name to dev) plus `fully_bound(profile)`.
There is no compatibility alias; every reader updates (F8).

**`RoomBindingRegistry`** (`control/room_binding.py`) keys by
`(RoomType, fixture)`:

- `bind(room_type, fixture, dev)` / `bound_device(room_type, fixture)` /
  `release(room_type, fixture=None)` (None releases all fixtures).
- `arm(room_type, fixture, window_seconds)`: arming names the fixture the
  next Room-node join binds. `armed_fixture(room_type)` returns it while the
  window is open. One fixture armed at a time per RoomType; arming a second
  replaces the first, matching today's replace-on-rearm behavior.
- `save()`/`load()` persist the fixture-keyed nested mapping
  `{room_type: {fixture: dev}}`. An old single-dev file is ignored with one
  log line: it is dead data, nothing calls `load()` from `boot()` yet, and a
  guessed migration could bind a stale dev to the wrong fixture.

**Roles** (`control/rooms.py`, `control/roles.py`): one ROOM-class role per
Bit, unchanged in name and manifests. `room_role()` sets
`capacity=len(room_profile(room_type).fixtures)`. `RoleClass.ROOM`'s comment
updates from "capacity 1" to "capacity = fixture count". The fixture identity
lives in the binding registry, not in the Bit's declaration, so a venue with
a different fixture count changes its profile and no Bit. Console and uplink
filtering (`non_room_counts()` and both agents) is untouched: still exactly
one role name to hide, and the Spec A addition-not-relaxation rule holds.

**Engine binding** (`control/engine.py`): `join()` against the Room node
while armed asks the registry `armed_fixture()` and binds that fixture:
`_bind_room(dev)` becomes `_bind_room(fixture, dev)`, writing both
`room.bound[fixture]` and the registry. Rebinding an already-bound fixture
replaces it, today's rebind semantics per fixture.

## 5. Engine resolution: the promised one-method change, and cue fan-out

`_resolve_target` returns **all bound fixture devs in declaration order** for
`ROOM` (and prepends them for `ALL`, deduplicated as today). This is the
change Spec B pre-paid for; no trigger declaration moves.

`_resolve_dev` (the `cues.ROOM` sentinel path for ordinary light/play cues)
is where a singular answer no longer exists. Decision: **Room light MIDI is
fed to the session once, and frame fan-out is the only per-fixture step.**
Concretely:

- `_dispatch_cues` resolves a `ROOM` cue to the Room's **canonical dev**: the
  first bound fixture in declaration order. One `on_light_cue` call reaches
  the agent, which feeds the one shared session once. The rendered
  consequence reaches every fixture through `_render_room()`'s slicing, which
  is what "one logical light" means. Feeding the session N times for N
  fixtures would double-apply relative MIDI and is exactly the bug this
  decision exists to prevent.
- The agent treats **any** bound fixture dev as Room-owned for cue routing
  (`dev in room devs`), so a cue that somehow names the second fixture's dev
  still lands on the shared session rather than a per-player path.
- `PlayCue`s targeting `ROOM` resolve the same way: once, to the canonical
  dev, onto the single Room audio sink (F7).
- A `ROOM` cue with zero bound fixtures drops with the existing
  once-per-Bit-load warning, unchanged.

`fire_trigger`'s `TriggerFired.devs` carries the full fixture-dev list, so
the observer record and the Console show what a fire actually reached.

## 6. `DeviceLinkAgent`: render once, slice, send N

- `_room_dev: str | None` becomes `_room_devs: dict[str, str]` (fixture to
  dev, declaration order). `_room_light` stays one `_RoomLightSink`: one
  session, one universe sized to the whole virtual surface.
- `_setup_room()` builds the session from the concatenated capability.
  `harness/room_surface.py`'s `to_capability()` emits the namespaced-zone
  union over the full pixel run; `primary` remains synthesized by luxaeterna
  for the whole surface and is still never declared or drawn on the Console.
- `_render_room()` keeps its shape: drain due Room audio cues, render into
  the one universe once, then loop `fixture_slices()` and send
  `frame[start:start+count]` to each **bound** fixture's dev, every slice
  stamped with the same `when`. Per-dev change detection stays in
  `_last_frames`, so a cue moving only `accent`'s pixels resends nothing to
  `main`.
- **Partial binding renders.** A Room with `main` bound and `accent` not yet
  bound renders and ships `main`'s slice. One unplugged fixture must not
  black out the rest of the room mid-show.
- The Room drone start/stop and `_tick_audio()` are untouched: audio is
  keyed off the Room being bound at all (any fixture), not per fixture.
- `on_room_frame` fires once per fixture dev per changed slice, which the
  Console consumes per dev exactly as it already does.

`RoomBridge` is unchanged in shape. Its `bind()` call site bookkeeping moves
with `_setup_room()`; it still holds one light sink and one audio sink.

## 7. Boot, simulators, teardown

- `_bind_room_fast_path` loops the profile's fixtures. For TEST it spawns
  **one simulator subprocess per fixture**, dev-named by a shared helper
  `sim_dev(fixture)` returning `sim-room-<fixture>` (`sim-room-main`,
  `sim-room-accent`): unique o2lite service names, which is the entire reason
  fixtures are separate clients. Each subprocess is pushed onto the
  `TeardownStack` at spawn, so the LIFO teardown guarantee extends per
  fixture with no new mechanism.
- `wait_for_room_binding` holds in SETUP until `room.fully_bound(profile)` or
  the existing timeout. On timeout with partial binding, boot proceeds
  partially bound (section 6 renders what it has) and logs which fixtures are
  missing; failing the whole boot for one dead fixture would turn a degraded
  show into no show.
- `harness/room_simulator.py` and `harness/o2_shroom.py` need no structural
  change: each instance takes its `--dev` and its **fixture's** channel count
  (passed explicitly; today both derive it from the whole profile, which is
  now wrong by construction). `--exit-with-parent` and the orphan guards
  apply per instance unchanged.
- `harness/terrarium_boot.py` drops `SIM_DEV` for the helper and spawns per
  fixture in both websocket and o2lite modes.

## 8. Console

Addition, never relaxation, same as Spec A section 3:

- The `room` snapshot/`room_changed` key gains `fixtures`: per fixture its
  name, pixel count, bound flag, and its zones with virtual-surface offsets.
  The node id, role name and registration counts stay hidden behind the
  untouched filters. No existing key changes shape, so an old browser tab
  degrades to today's behavior.
- `room.js` renders one strip per fixture, each drawing that fixture's
  relayed frame under its name, keeping the no-rebuild-per-event discipline
  the panel learned from its strip-rebuild defect. Frame relay is unchanged:
  latest-frame-per-dev, ~10 Hz, drop-never-queue.
- The arm control gains a fixture picker. `arm_room` grows a `fixture` field,
  parsed in `console/protocol.py`'s admin parser (a trusted-operator action,
  same separation as `fire_trigger`).
- New browser behavior gets real `vm`-based tests in
  `tests/js/room_panel_behavior.test.js` (the established pattern, never
  substring greps): N strips render; one fixture's frame update leaves the
  other strips' nodes intact; an unbound fixture displays as unbound.

## 9. The `rainbow` preset (companion change in luxaeterna)

A new registered preset in `luxaeterna/synth/presets.py`, the same
registration pattern as `aurora` (F3): hue mapped along `ctx.positions` and
scrolled by a phase advancing on the render clock,
`hue(pixel) = base_hue + span * position + phase(t)`, with params `speed` and
`span` plus aurora's optional cc-driven `hue`/`level` lanes. Roughly twenty
lines; renderer untouched.

In mm-terrarium, TestBit's Room `light_manifest` declares `rainbow` (either
replacing `aurora` on `primary` or alongside it), making the cross-fixture
property the thing the reference fixture visibly exercises. Tests for the
gradient itself live in luxaeterna; one mm-terrarium test pins that a spatial
instrument's rendered output differs across fixture slices, the cross-fixture
property in one assertion.

Sequencing: the luxaeterna PR lands first; mm-terrarium's declaration change
rides this slice.

## 10. Error handling

- Invalid profiles (dup names, mixed color order, universe overflow, zone
  overrun) raise at construction: import or boot time, never mid-run.
- Arming with an unknown fixture name is refused with a located error at the
  admin parser.
- A join against the Room node with no armed window, or after the window
  lapsed, is refused exactly as today.
- An old-format binding persistence file is ignored with one log line.
- A fixture dev disconnecting mid-run behaves as today's stale-device
  situation; no new liveness machinery (the standing deferral stands).
- A wrong-width frame at any fixture is dropped and logged, never truncated
  (F4, now per fixture).

## 11. Doc and diagram sync obligations

- `cue-path.d2`: "ROOM resolved to bound dev" becomes "ROOM resolved to
  bound fixture devs"; regenerate via `tools/render_diagrams.py`.
- `boot-teardown.d2`: "Room simulator" becomes "Room simulators (one per
  fixture)"; regenerate.
- `docs/MM_TERRARIUM.md`: the Spec C deferred entry moves to a Landed
  subsystems section; the Room-panel section's single-surface description
  updates.
- `player-flow.seq` is deliberately untouched (F6, non-goal N8).

## 12. Non-goals

- **N1. Per-fixture cue targeting and named-fixture trigger targets.** Spec
  B's rejection stands. The fixture registry this slice creates makes a
  future named-fixture target validatable, deliberately not taken now.
- **N2. Per-fixture manifests.** One Room role, one manifest; fixture-level
  variation is expressed through namespaced zones.
- **N3. Audio fan-out.** One drone, one Flsyn channel, single audio sink
  (F7).
- **N4. Per-fixture color order.** One `color_order` per Room, validated.
- **N5. Multi-universe Rooms.** The 170 px RGB single-universe cap stands;
  the venue array's PixelSpan/UniverseSet path remains the DEMO follow-up.
- **N6. Real-hardware Room backends and the DEMO profile.**
- **N7. Device liveness/heartbeat.** The standing stale-device deferral is
  unchanged.
- **N8. The `/ie<N>/role` blob over o2lite.** Diagrammed in
  `player-flow.seq`, still unbuilt, still out of scope.

## 13. Success criteria

1. `RoomProfile` declares N named fixtures with derived
   `pixel_count`/`channel_count`/namespaced `zones`/`fixture_slices()`;
   invalid profiles fail at construction. TEST declares `main` (60 px, 3
   zones) and `accent` (30 px, 2 zones).
2. `RoomBindingRegistry` binds, arms, releases and persists per
   `(RoomType, fixture)`; arming names the fixture the next Room-node join
   binds, and the engine binds exactly that fixture.
3. `room_role()` capacity equals the profile's fixture count; Console and
   uplink still never reveal the Room's node id, role name or counts
   (existing filter tests still pass byte-identical).
4. `_resolve_target(ROOM)` returns all bound fixture devs in declaration
   order; no Bit or trigger declaration changes (Spec B's promise, landed).
5. Room light MIDI reaches the shared session exactly once per cue; the
   rendered frame is sliced at fixture boundaries and each bound fixture
   receives its slice stamped with the same `when`. A partially bound Room
   renders to the fixtures it has.
6. TEST boot spawns one simulator per fixture with unique service names
   (`sim-room-main`, `sim-room-accent`), each on the `TeardownStack`; all
   are reaped on every exit path.
7. The Console shows one strip per fixture with live frames and per-fixture
   bound state, arm takes a fixture, and the new browser behavior is covered
   by `vm`-based tests, not greps.
8. The full suite passes offline via `.venv/bin/python -m pytest tests -v`
   with no O2, no Arco, no pyarco and no luxaeterna import at module level
   under `control/`.
9. The `rainbow` preset exists in luxaeterna with its own tests; one
   mm-terrarium test pins that a spatial instrument's output differs across
   fixture slices.
10. Live verification: against a real Arco with both simulator tabs open,
    fire a rainbow-bearing cue (or TestBit's ambient `cues(at)`) and observe
    one gradient scrolling continuously across both canvases with no seam;
    `cue-path.d2` and `boot-teardown.d2` regenerated to match.
