# DEMO room and the Block build-out unit

**Date:** 2026-08-19
**Status:** Draft, awaiting review. Live-verified against a real Arco: not yet.
**Prior slices:** the Room concept and load sequence (2026-08-10), the
Terrarium Visualization Simulator / TEST room (2026-08-10), the Room panel
and Room fixtures (Spec A, 2026-08-17), Bit-declared triggers (Spec B,
2026-08-17), the N-fixture Room (Spec C, 2026-08-18).

`RoomType.DEMO` exists as an enum value, a recipe, and a Registration Node id,
but has no `RoomProfile` (`room_profile(RoomType.DEMO)` raises
`NotImplementedError`) and no Bit supports it (`Bit.room_types` defaults to
`{RoomType.TEST}` and `TestBit` never widens it). This closes both gaps with
a synthetic-but-real-scale DEMO room, and introduces a new `Block`
sub-structure inside `RoomFixture` so a fixture's physical build-out (which
literal LED device drives which pixel range) can be declared independently
of its gameplay-targeting zones and independently of the single-DMX-universe
pixel cap.

---

## 1. Findings from the code

- **F1.** `ROOM_PROFILES` (`control/room_profile.py`) only has an entry for
  `RoomType.TEST`; `room_profile()` raises `NotImplementedError` for
  `RoomType.DEMO`, by design (`tests/test_room_profile.py` pins this: "DEMO's
  backend is a deferred follow-up spec").
- **F2.** `Bit.room_types: set[RoomType] = {RoomType.TEST}` is the class-level
  default (`control/bit.py:28`); `TestBit` never overrides it. `boot()`
  checks `room.room_type not in bit_cls.room_types` and raises `BootFailure`
  before ever consulting the room profile (`control/boot.py:104`) — so
  `TestBit` cannot run in a DEMO room today independent of F1.
- **F3.** `control/boot_config.py`'s `array_backend_configured` and
  `control/rooms.py`'s `resolve_room_type()` already treat DEMO as a real,
  resolvable target (`RoomRecipe(requires_array_backend=True)`,
  `tests/test_boot_config.py`, `tests/test_rooms.py`) — resolution is not the
  blocker; the profile and the Bit gate are.
- **F4.** `tests/test_room_binding.py` already exercises
  `RoomBindingRegistry.bind(RoomType.DEMO, "array", "array-1")` — an existing
  test fixture anticipates DEMO's one fixture being named `"array"`. This
  spec's profile honors that name.
- **F5.** `RoomProfile.__post_init__` currently caps the **whole profile** at
  170 px (`_MAX_PROFILE_PIXELS`, one luxaeterna `Universe`'s worth of DMX
  channels ÷ 3). This is a real ceiling on today's `RoomFixture`, which has
  no internal structure below the fixture: `pixel_count` is a stored field,
  not derived.
- **F6.** `devicelink/agent.py`'s `_render_room()` already renders **one**
  `LightSession` across the whole concatenated profile and slices the result
  per fixture for transport (`RoomProfile.fixture_slices()`) — multi-unit
  rendering is already seamless at the fixture level; nothing about this
  spec's Block addition needs to touch that rendering path, because Blocks
  are declared *within* one fixture, which already renders as one continuous
  span.
- **F7.** The real hardware (`MM_HARDWARE_DESIGN.md` §7.1) is a single
  continuous 6 m run of 12 V SK6812 RGBW at 144 LED/m — 864 px total — driven
  by Art-Net/WLED, which chunks that one continuous run into DMX universes
  purely as a wire-transport concern invisible above the transport layer.
  This is structurally identical to the Block concept this spec introduces:
  one logical fixture, N ≤170px build-out chunks.
- **F8.** `RoomZone` already exists for a different purpose — a named region
  a light instrument can *target* (gameplay/Console semantics). It is
  deliberately not reused for Blocks, which describe physical hardware
  composition, not targeting.

## 2. The decided model

**A `RoomFixture` is one continuous physical run, decomposed into `Block`s.**
A Block is a physical build-out unit — literally "which LED device drives
this pixel range" — capped individually at `_MAX_PROFILE_PIXELS` (one DMX
universe / one controller's worth). A fixture's `pixel_count` becomes the sum
of its blocks' `count`, so a fixture's total size is no longer capped; only
each block is. Zones stay exactly what they are today — gameplay/Console
targeting regions — declared independently of block boundaries, on a
different axis.

```python
@dataclass(frozen=True)
class RoomBlock:
    """A physical LED device's own pixel range within a Fixture -- the
    build-out unit. Individually capped at _MAX_PROFILE_PIXELS (one DMX
    universe / one controller's worth). Purely declarative this slice: no
    simulator or backend consumes block boundaries yet, but a future real
    per-controller output adapter reads them off the profile with no
    further data-model change needed."""
    name: str
    start: int
    count: int

@dataclass(frozen=True)
class RoomFixture:
    name: str
    color_order: str
    blocks: tuple[RoomBlock, ...]
    zones: tuple[RoomZone, ...]

    @property
    def pixel_count(self) -> int:
        return sum(b.count for b in self.blocks)
```

- `__post_init__` validates blocks the same way it already validates zones:
  non-overlapping, non-overrunning (against the fixture's own `pixel_count`),
  and **each block individually ≤ `_MAX_PROFILE_PIXELS`** — replacing the old
  whole-profile-total check. `RoomProfile.__post_init__`'s existing
  `pixel_count > _MAX_PROFILE_PIXELS` check is removed; the per-block check
  in `RoomFixture.__post_init__` is the new, tighter invariant it replaces.
- Every existing derived property (`RoomProfile.pixel_count`,
  `channel_count`, `zones`, `fixture_slices()`) is unaffected — they already
  go through `RoomFixture.pixel_count`, which is now a property instead of a
  stored field but has the identical value for every existing (single-block)
  fixture.
- **TEST's profile is updated, not left as-is**: `main` and `accent` each
  gain one explicit block spanning their full existing size (`main`: one
  60 px block; `accent`: one 30 px block). No implicit single-block
  defaulting is introduced — every fixture's build-out is always explicit,
  so the model has one shape, not two.
- **Blocks are purely declarative this slice.** No simulator, renderer, or
  backend consumes block boundaries for actual multi-controller output
  routing — that is real Art-Net/per-controller wiring, explicitly deferred
  (see *Non-goals*). The one consumer this slice adds is the block-
  identification debug tool (§4).

### 2.1 DEMO's profile

```python
RoomType.DEMO: RoomProfile(
    surface_id="room_demo",
    fixtures=(
        RoomFixture(
            name="array", color_order="GRB",
            # 144 LED/m x 6m real array (MM_HARDWARE_DESIGN.md SS7.1), one
            # block per physical meter run.
            blocks=(
                RoomBlock("m1", 0, 144), RoomBlock("m2", 144, 144),
                RoomBlock("m3", 288, 144), RoomBlock("m4", 432, 144),
                RoomBlock("m5", 576, 144), RoomBlock("m6", 720, 144),
            ),
            zones=(RoomZone("left", 0, 288), RoomZone("center", 288, 288),
                  RoomZone("right", 576, 288)),
        ),
    ),
),
```

864 px total (6 × 144), matching the real array's actual scale — unlocked by
Blocks decoupling fixture size from the old whole-profile 170 px cap. One
fixture (`"array"`, matching the name `tests/test_room_binding.py` already
uses), one continuous run, rendered seamlessly per F6. 3 zones (thirds),
deliberately not 1:1 with the 6 blocks, demonstrating zones and blocks are
independent axes.

### 2.2 `TestBit` gains DEMO support

- `TestBit.room_types = {RoomType.TEST, RoomType.DEMO}`, overriding the
  `Bit` default of `{RoomType.TEST}`.
- `TestBit.role_table` merges a second `room_role(RoomType.DEMO, ...)`
  alongside its existing TEST one, with its own `light_manifest`/
  `ugen_manifest` (the same `rainbow`/`flsyn` declarations the TEST Room role
  already uses are sufficient — an instrument targets `primary`/zones, never
  blocks, so it needs no new knowledge to run against DEMO's profile).
- `player` (scored, shared) and `jammer` (unscored, jam) are already
  room-agnostic and need no change. Once DEMO can resolve and boot, the
  existing Scored/Jam validation applies to it for free — this was the
  original goal: a test Bit that validates a room works for both roles,
  for both TEST and DEMO.

## 3. Boot and simulator wiring

- A DEMO-room simulator entry point, mirroring `harness/room_simulator.py`:
  its own `ShroomClient` (`expected_channels=2592`, i.e. 864 × 3), its own
  `WebSimBackend` canvas, its own `sim-room-array` o2lite service name.
  Wired into `boot()`'s `simulator_factory` exactly like TEST's fixture is
  today, so `harness/run_stack.py` / `harness/terrarium_boot.py` can bring up
  a DEMO room end-to-end the same way they already do for TEST.
- No changes to `control/boot.py`'s orchestration logic itself — DEMO simply
  becomes a second `RoomType` with a real profile and a real simulator
  factory entry, which is exactly the seam `boot()` was already built
  against (F1–F3).

## 4. Block-identification debug tool

A `--identify-blocks` flag on the DEMO room simulator entry point. When
passed, the simulator **bypasses its normal incoming-frame rendering** for
that run and instead paints each of the 6 blocks a fixed, distinct solid
color — one per physical meter-segment — directly onto the canvas, reading
block boundaries straight off `room_profile(RoomType.DEMO)`. Palette: a fixed
6-color sequence (red, orange, yellow, green, blue, violet), assigned to
blocks in declaration order.

This is a harness-side-only tool: it does not touch `control/`, the engine,
the cue path, or the Console. It exists purely so a human can visually
confirm the 6 meter-segments map to the expected canvas regions before any
real per-controller output wiring exists to verify against.

## 5. Non-goals

- **N1. No real Art-Net/per-controller output backend.** `harness/
  array_smoke.py`'s `PixelSpan`/`UniverseSet`/`PowerLimiter` machinery is not
  wired into `boot()` this slice. DEMO's simulator remains a browser-canvas
  stand-in, same trust/backend model as TEST's.
- **N2. No heterogeneous blocks.** Every block in DEMO's profile is the same
  size (144 px), mirroring one real product. Blocks that differ from each
  other (different sizes/devices within one fixture) are explicitly the next
  phase after this one, not built here.
- **N3. No Console UI for block visualization.** The debug tool is a
  simulator-launch flag, not a live Console toggle. A Console overlay is
  real UI work (`console/static/room.js`, `console.js` event dispatch) and is
  deferred.
- **N4. No block-level simulation or per-block o2lite clients.** Blocks stay
  declarative; the DEMO simulator connects and renders at the fixture level,
  identical in shape to how TEST's simulator does today.
- **N5. No RGBW widening.** `color_order="GRB"` and the existing
  3-channel-per-pixel wire assumption are unchanged; RGBW is a separate, open
  decision already flagged in `control/room_profile.py`'s `channel_count`
  docstring and is not reopened here.

## 6. Testing

- `tests/test_room_profile.py`: `RoomBlock` validation (overlap, overrun
  against the fixture's own size, per-block cap enforcement replacing the
  old whole-profile cap); DEMO's profile shape (1 fixture, 6 blocks × 144 px,
  3 zones, 864 px / 2592 channels total); TEST's profile still validates
  under the new per-block cap with its explicit single-block-per-fixture
  declarations.
- `tests/test_rooms.py` / `tests/test_boot.py`: `TestBit` now resolves and
  boots successfully for `RoomType.DEMO` — the tests that today assert this
  fails (per F1/F2) flip to asserting success.
- `tests/test_room_simulator_demo.py`, a DEMO-simulator-equivalent of
  `tests/test_room_simulator.py`, covering the new entry point's `build()`/
  `main()` split and the `--identify-blocks` palette-assignment logic,
  headless (no browser, no live Arco) — matching the existing simulator
  test pattern.

## 7. Live-verify plan (per this repo's convention: offline-suite-green is
not "done")

Once implemented and offline-tested: boot a DEMO room via `run_stack`
against a real Arco, confirm a simulated Tuneshroom can still join
`TEST_PLAYER_NODE`/`TEST_JAM_NODE` (player/jammer are Bit-level nodes,
unaffected by which RoomType resolved), complete a scored round and an
unscored jam join, and confirm the Room's `rainbow` cue sweeps the full
864 px canvas with no seam at any of the 6 meter-block boundaries. Separately, run the DEMO
simulator with `--identify-blocks` and visually confirm 6 distinct solid
colors, each spanning exactly 144 px, in declaration order.
