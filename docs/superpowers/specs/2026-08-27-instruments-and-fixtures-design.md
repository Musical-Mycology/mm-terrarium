# Instruments and Fixtures

Spec 2 of the Room/Instrument/Trigger restructure (brainstormed 2026-08-26;
direction recorded in
`2026-08-26-terrarium-lifecycle-and-config-rooms-design.md` section 12).
Spec 1 (the Terrarium lifecycle and config-defined rooms) is merged (PR #57)
and this spec instantiates inside `load_room`'s world.

Binding decisions from the 2026-08-26/27 brainstorm, restated so this spec
cannot drift from them:

1. **Instrument is a first-class entity** whose elements are the existing
   light-manifest v2 + ugen-manifest shapes, plus advertised
   **capabilities**, **functions**, and **accepted triggers**.
2. **A Fixture IS an Instrument a room loads** -- the Instrument structure
   plus placement (blocks/zones) and device binding, unified with (not
   duplicating) today's `RoomFixture`.
3. **Rooms declare their instrument set in `terrarium.toml`.**
4. **A standard Tuneshroom instrument definition exists once** and is
   instantiated per joined carrier device, with a role grant binding the
   carried instrument into a Bit's requirement slot (device = instrument
   carrier; roles keep their scoring semantics).
5. **Bits declare instrument REQUIREMENTS as capability contracts** (not
   instrument names), resolved at `load_bit`, `BitLoadError` on no match.

Also binding: this slice must compose with PR #56's landed SURFACE trigger
targeting / `SolidCue` / mute machinery, and the Console's
one-list-light-plus-audio-discriminated-by-`kind` property (both fed from
one shared MIDI stream) is load-bearing and must survive.

Baseline: **1442 passed, 1 skipped**, fully offline
(`.venv/bin/python -m pytest tests -q`; a fresh worktree needs
`ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first).

## 1. What changes, in one picture

Today "instrument" is a word that appears only *inside* manifest dicts: a
Bit authors `light_manifest`/`ugen_manifest` entries, `control/room_view.py`
harvests them into Console cards, and the Room's hardware shape
(`RoomFixture`) knows nothing about what plays on it. A Bit can demand
nothing of a room except its name (`launch.room_types`), and a device is
whatever `shroom_capability()` hardcodes.

After this spec:

```ascii
terrarium.toml                          control/instrument.py
[instruments.venue_array]               TUNESHROOM (defined once, in code)
  capabilities, functions,                capabilities: light.pixels,
  accepted_triggers,                        audio.samples, gesture.tap,
  ambient light/ugen manifests             gesture.tilt ...
        |                                        |
        v                                        v
[rooms.DEMO.fixtures]                   instantiated per joined carrier
  instrument = "venue_array"            device (dev = instrument carrier)
  + blocks/zones (placement)                     |
  + device binding (RoomBindingRegistry)         |
        |                                        |
        v                                        v
   Fixture = Instrument + placement + binding    |
        |                                        |
        +---------------+------------------------+
                        v
        Bit.instrument_requirements()  (capability contracts, not names)
          room slots  -> resolved against the room's fixtures at load_bit
                         (BitLoadError on no match)
          role slots  -> a Role names a slot; a join is granted only if
                         the joining device's carried instrument
                         satisfies that slot's contract
```

Nothing about the render path changes: one shared MIDI stream still feeds
light and audio, the Room still renders one `LightSession` sliced per
fixture, and a device still receives its composed `/ie<N>/role` blob.

## 2. The Instrument entity (`control/instrument.py`)

A new pure-stdlib module (same discipline as `control/room_profile.py`:
no luxaeterna, no pyarco).

```python
@dataclass(frozen=True)
class Instrument:
    name: str
    description: str = ""
    capabilities: frozenset[str] = frozenset()
    functions: tuple[str, ...] = ()          # named, declarative; Spec 3
                                             # gives them behavior
    accepted_triggers: tuple[str, ...] = ()  # cue kinds this instrument
                                             # accepts: "midi", "play",
                                             # "solid", "mute"
    light_manifest: dict = field(default_factory=dict)   # light-manifest v2
    ugen_manifest: dict = field(default_factory=dict)    # ugen manifest v0
```

- **Elements are the existing manifest shapes** (binding decision 1), and
  they are validated by the existing validators
  (`control/role_config.py`'s `_validate_light_manifest` /
  `validate_ugen_manifest`), refactored only enough to be callable with an
  instrument-shaped error prefix. No new manifest schema.
- An Instrument's own manifests are its **ambient declaration**: what the
  fixture renders when no Bit overlays it (section 6). They may be empty.
- `capabilities` is a flat set of string tags. Schema v1 vocabulary
  (validated -- an unknown tag is a config/load error, not a silent
  never-matches):
  `light.pixels`, `light.surface` (a linear multi-zone surface, i.e. a
  Room-style array), `audio.flsyn` (an Arco FluidSynth voice reachable),
  `audio.samples` (local sample playback), `gesture.tap`, `gesture.tilt`.
  The vocabulary is one frozenset constant (`CAPABILITY_VOCABULARY`) so
  growing it is one edit and every consumer validates against the same
  set.
- `functions` and `accepted_triggers` are **declarative strings this
  slice**: `functions` names are recorded, surfaced on the Console, and
  reserved for Spec 3 (which turns them into generator/stream Functions);
  `accepted_triggers` is enforced at one seam (section 7).

```python
@dataclass(frozen=True)
class InstrumentRequirement:
    slot: str                       # the Bit-local name of the slot
    capabilities: frozenset[str]    # contract: required tags
    min_pixels: int = 0             # 0 = no pixel demand
    optional: bool = False          # unresolved optional slot: no error,
                                    # slot stays empty

def satisfies(instrument, requirement, *, pixel_count=None) -> str | None:
    """None if the instrument satisfies the contract, else the reason."""
```

`satisfies` is the whole matching algorithm: capability-superset plus the
one numeric facet, returning a reason string (never raising) in the
house convention. Matching is on **contracts, not names** (binding
decision 5): nothing anywhere resolves an instrument by its name except
config parsing itself.

**The standard Tuneshroom instrument is defined once, in code:**

```python
TUNESHROOM = Instrument(
    name="tuneshroom",
    description="Handheld 12-LED Tuneshroom (8-ring + 4-stem)",
    capabilities=frozenset({"light.pixels", "audio.samples",
                            "gesture.tap", "gesture.tilt"}),
    functions=("tap", "tilt"),
    accepted_triggers=("midi", "play", "solid", "mute"),
)
```

In code rather than in `terrarium.toml` because it describes the device
fleet, not any one room -- every venue's Tuneshrooms are the same
hardware, and a config-defined copy per installation would drift.
(What would change the call: per-venue Tuneshroom variants. None are
planned; revisit in Spec 4's bundling world if that changes.)

```python
@dataclass(frozen=True)
class CarriedInstrument:
    instrument: Instrument
    dev: str                        # the carrier device
```

Instantiated per joined carrier device (binding decision 4): the
`DevicePool` entry gains the carried instrument at hello time (every
device that speaks the Tuneshroom wire carries `TUNESHROOM` today;
the seam is per-device so a future non-Tuneshroom device kind is a data
change, not a redesign).

## 3. Fixture = Instrument + placement + binding (unifying `RoomFixture`)

Binding decision 2 says unify, not duplicate. `RoomFixture`
(`control/room_profile.py`) is the placement half and stays where it is;
it gains one field:

```python
@dataclass(frozen=True)
class RoomFixture:
    name: str
    color_order: str
    blocks: tuple[RoomBlock, ...]
    zones: tuple[RoomZone, ...]
    instrument: Instrument          # NEW -- the instrument this fixture IS
```

- The runtime Fixture concept is therefore exactly: the `Instrument`
  structure (via `fixture.instrument`), plus placement
  (`blocks`/`zones`, unchanged), plus device binding
  (`RoomBindingRegistry`, unchanged keying `(room_name, fixture) -> dev`).
  No new parallel class; `RoomFixture` *is* the Fixture.
- `RoomProfile.__post_init__` additionally validates each fixture's
  instrument against the capability vocabulary and cross-checks the
  placement: a fixture whose instrument advertises `light.surface` or
  `light.pixels` must have pixels (it always does today); `min_pixels`-
  style demands live on the requirement side, not here.
- `pixel_count`, `channel_count`, `fixture_slices()` and every existing
  consumer are untouched.

**`terrarium.toml` declares the room's instrument set** (binding
decision 3). Schema stays 1 -- the additions are new tables and one new
key, and `parse_terrarium_config` already fails hard on malformed
content; a pre-this-slice file with no `[instruments]` and no
`instrument =` keys is REJECTED with a located error telling the author
what to add, because a fixture without an instrument is no longer a
representable thing. (Recommended over silently defaulting: the file is
repo-local, there is exactly one of it plus test fixtures, and a silent
default instrument is exactly the kind of dual-vocabulary drift Spec 3
forbids for triggers.)

```toml
[instruments.venue_array]
description = "6 m SK6812 venue array"
capabilities = ["light.surface", "audio.flsyn"]
functions = []
accepted_triggers = ["midi", "play", "solid", "mute"]
  [instruments.venue_array.ambient]
  # optional; light-manifest v2 / ugen v0 shapes, same as a Bit authors
  [instruments.venue_array.ambient.light]
  instruments = [ { instrument = "aurora", target = "primary" } ]
  [instruments.venue_array.ambient.ugen]
  instruments = [ { instrument = "flsyn", program = 89,
                    drone = { key = 48, velocity = 80 } } ]

[rooms.DEMO]
# ... existing keys unchanged ...
  [[rooms.DEMO.fixtures]]
  name = "array"
  color_order = "GRB"
  instrument = "venue_array"      # NEW -- references [instruments.*]
  # blocks / zones unchanged
```

`control/terrarium_config.py` parses `[instruments.<name>]` into
`Instrument` values (the `ambient.light`/`ambient.ugen` tables become the
instrument's `light_manifest`/`ugen_manifest`), validates them with the
shared validators at parse time (a typo'd manifest fails at config load,
located, before any room loads), and resolves each fixture's
`instrument = "<name>"` reference -- an unknown name is a
`TerrariumConfigError`. The reference-by-name is config-file-internal
plumbing only; everything downstream holds the resolved `Instrument`
value.

## 4. Bit requirements as capability contracts, resolved at `load_bit`

The `Bit` interface (`control/bit.py`) gains:

```python
def instrument_requirements(self) -> tuple[InstrumentRequirement, ...]:
    return ()        # base default: no demands
```

Two kinds of slot, distinguished by how they resolve, not by type:

- **Room slots** -- resolved at `load_bit` against the loaded room's
  fixtures. `GameServer.load_bit` (inside the existing try/except that
  wraps everything in `BitLoadError`) walks each non-optional
  requirement whose contract any fixture's instrument must satisfy;
  the *room's aggregate* is what's checked (`min_pixels` checks
  `profile.pixel_count`; capability tags must each be advertised by at
  least one fixture's instrument). **No match -> `BitLoadError`** with
  the `satisfies` reason strings for every fixture, so the error names
  what was missing rather than just "no match" (binding decision 5).
- **Role slots** -- a `Role` gains one field:

  ```python
  requires: str | None = None      # names a slot in
                                   # instrument_requirements()
  ```

  A role with `requires` set is grantable only to a device whose
  carried instrument satisfies that slot's contract; the grant is the
  binding of the carried instrument into the slot (binding decision 4).
  `RegistrationState.join` refuses with the `satisfies` reason (the
  existing refusal-reason path devices already render). Roles keep
  their scoring semantics untouched -- `requires` composes with
  `role_class`/`scored`/capacity, it does not replace any of them.
  `load_bit` validates structurally that every `requires` names a
  declared slot (a typo is a `BitLoadError`, never a mid-installation
  surprise -- same rule `validate_trigger_table` established for
  verbs).

**The engine-synthesized ROOM role now rides the same machinery.** Today
`load_bit` synthesizes a ROOM role whenever `Bit.room_manifests()` is
non-empty. That stays, and additionally synthesizes the implicit room
slot it implies: a non-empty light manifest implies
`{"light.surface"}`, a non-empty ugen manifest implies
`{"audio.flsyn"}` (slot name `"room"`, reserved). So every existing Bit
gets contract checking against the loaded room with **zero Bit code
changes** -- `TestBit` loaded into a hypothetical room whose fixtures
advertise no `audio.flsyn` fails at `load_bit` with a located reason
instead of silently rendering half of itself. A Bit that declares an
explicit `"room"` slot overrides the implication (its contract wins;
declaring both non-empty manifests and a weaker explicit contract is on
the Bit author, same trust the manifests themselves already get).

`bit.toml` is unchanged this slice. Requirements live in Python beside
`role_table`, which already lives in Python -- they are load-time
behavior, not launch-time defaults, and `[launch]` stays about how the
harness starts a Bit. (What would change the call: Spec 4's external
Bits wanting requirements visible to `--list-bits` without importing
code; that spec owns the manifest-surface question.)

`bits/test/test_bit.py` (`TestBit`) becomes the reference exemplar, as
it is for every other seam: it declares
`InstrumentRequirement(slot="player", capabilities={"light.pixels",
"gesture.tilt"})` and sets `requires="player"` on its `player` role, so
the role-slot grant path is exercised by the durable regression fixture
and not only by unit Bits.

## 5. Carrier instantiation and the grant path

- `DevicePool` records `carried: Instrument` per device (default
  `TUNESHROOM` at hello -- the wire only speaks Tuneshroom today).
  `CarriedInstrument(instrument, dev)` is the value handed around.
- `GameServer.join` consults `role.requires` (section 4) before
  granting. On grant, the binding is recorded on the `JoinResult`
  (`slot`, `instrument.name`) and stamped into the composed role blob by
  `compose_role_config` alongside the existing provenance keys -- a
  device can see which slot it fills, and the Console can show it.
- ROOM-class joins are untouched: a fixture binding is already "this
  dev renders this fixture['s instrument]", which is the same statement
  in the new vocabulary.

## 6. Rendering: ambient instrument manifests, Bit overlay unchanged

- **Bit loaded (today's behavior, unchanged):** the Room renders the
  Bit's `room_manifests()` exactly as now; per-device sessions render
  the granted role's `light_manifest` exactly as now. Nothing in the
  hot path changes.
- **No Bit loaded (new):** on `ROOM_READY` with fixtures bound,
  `DeviceLinkAgent` builds the Room `LightSession` from the fixtures'
  instruments' **ambient** manifests (concatenated per fixture order --
  each fixture's ambient light declarations target its own namespaced
  zones or `primary`) instead of leaving the room dark until
  `load_bit`. This is the concrete visible payoff of instrument-owned
  elements, and it makes the lifecycle spec's live-checklist line
  ("load TEST from the panel; fixtures bind; ambient light") true
  without a Bit. `load_bit` swaps the session to the Bit's declaration;
  Bit unload swaps back to ambient. An empty ambient manifest renders
  nothing, exactly like an empty role manifest today.
- The one-shared-MIDI-stream property is untouched: ambient audio (an
  instrument's ugen manifest with a drone) rides `AudioBridge` the same
  way the ROOM role's does.

## 7. Composition with SURFACE triggers, SolidCue and mute (PR #56)

- `accepted_triggers` is enforced at **one seam**:
  `GameServer.fire_trigger`'s target resolution. A fire whose expanded
  cues would land on a surface whose instrument does not accept that cue
  kind (`solid` for `SolidCue`, `play` for `PlayCue`, `mute` for
  `MuteCue`, `midi` for plain tuples/`LightCue`) is refused with a
  reason string -- the existing never-raises convention, surfaced on the
  Console like any other refusal. Every shipped instrument accepts all
  four kinds, so nothing observable changes until someone declares a
  narrower instrument (e.g. a future audio-only fixture refusing
  `solid`).
- Mute stays universal in mechanism (the latch lives on the surface,
  not the instrument) -- only the *fire* is gated, and `TUNESHROOM` and
  every config example accept `mute`.
- `_dispatch_cues`, `TimedQueue`, `SolidCue` application at the two
  send seams: all untouched.

## 8. Console

- `control/room_view.py`'s `room_view` now sources each fixture's card
  from its `Instrument` (name, description, capabilities, functions,
  accepted triggers) plus the **live** manifest actually rendering
  (Bit overlay when loaded, ambient otherwise). The instruments list
  stays **one list discriminated by `kind`** -- the load-bearing
  property is asserted by the existing tests and must not regress into
  per-kind tables.
- `fixtures_view` rows gain `instrument` (name + capabilities).
- The rail's per-role cards show a role's `requires` slot and contract,
  so an operator can see why a join was refused.
- Front-end: `surface.js` instrument cards render the new fields under
  the existing declaration-signature discipline (a new field in the
  card = part of the signature; live values still patch in place).

## 9. What this deliberately does not change

- **No Trigger/Function rename** -- that is Spec 3, whole and atomic.
  `functions`/`accepted_triggers` here are declarative data Spec 3 will
  animate.
- **No `bit.toml` requirements surface** -- Spec 4's discovery/manifest
  question.
- **No wire change** -- devices are dumb pixel/sample sinks; the role
  blob gains provenance-style keys only.
- **No RGBW / hardware backend work**, no per-device Arco audio.
- **`ugen_manifest` stays v0** -- instrument-owned, same shallow
  validation; the real audio-manifest freeze remains future work.

## 10. Testing

Offline throughout, `.venv/bin/python -m pytest tests -q` green at every
task boundary. New coverage, by seam:

1. `control/instrument.py`: vocabulary validation, `satisfies` (each
   refusal reason), frozen/hashable.
2. `terrarium_config`: instrument tables parse; unknown capability tag,
   unknown instrument reference, missing `instrument` key on a fixture,
   malformed ambient manifest -- each a located `TerrariumConfigError`;
   `version` still content-addressed.
3. `room_profile`: fixture-instrument validation; existing geometry
   tests unchanged.
4. Engine: implicit room slot derivation; explicit room slot; no-match
   -> `BitLoadError` naming per-fixture reasons; `requires` typo ->
   `BitLoadError`; role-slot grant granted/refused on carried
   instrument; refusal reason reaches the device path.
5. Ambient render: room session built from instrument ambients with no
   Bit; swap to Bit declaration at `load_bit` and back at unload;
   empty ambient renders nothing.
6. Trigger gating: a narrow instrument refuses a `SolidCue` fire with a
   reason; shipped instruments accept everything (pin, so a vocabulary
   edit cannot silently start refusing).
7. Console: `room_view` one-list-`kind` property re-pinned with
   instrument-sourced cards; `surface.js` node tests for the new card
   fields under the existing DOM-discipline tests.
8. `TestBit` exemplar: player slot requirement exercised through the
   full engine (grant + refusal) as a tested behavior.

The full-cycle pin (`tests/test_terrarium_cycle.py`) grows one leg:
ambient light before `load_bit`, Bit overlay during, ambient again
after unload.

## 11. Live verification checklist (real Arco, dev box; post-merge)

- [ ] 1. Load TEST from the Console with no Bit: fixtures bind, ambient
      light renders from the instrument declaration, drone if ambient
      ugen declared.
- [ ] 2. `load_bit TestBit`: room look swaps to the Bit's declaration;
      unload: ambient returns.
- [ ] 3. A device joins `player`: grant recorded with slot binding
      visible on the Console; a hypothetical role with an unsatisfiable
      contract refuses with the reason on the device.
- [ ] 4. Fire `flash_device` at the Room and at a device (SURFACE
      picker): accepted as today; verify mute still latches and
      un-mutes on next fire.

## Status

Spec written 2026-08-27. Not yet implemented.
