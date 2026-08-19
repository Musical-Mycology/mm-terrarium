# DEMO Room and Block Build-Out Unit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `RoomType.DEMO` a real, real-scale (864 px) `RoomProfile` built from a new `RoomBlock` physical build-out unit, make `TestBit` support DEMO so Scored/Jam validation works there, wire the existing simulator/boot harness to bring a DEMO room up end to end, and add an `--identify-blocks` visual debug flag.

**Architecture:** `RoomBlock` is a new declarative sub-structure inside `RoomFixture` describing which physical LED device drives which pixel range; the 170 px single-DMX-universe cap moves from whole-profile to per-block. Rendering is untouched — `devicelink/agent.py` already renders one seamless `LightSession` across the profile and slices per fixture. The simulators (`harness/room_simulator.py`, `harness/o2_shroom.py`) already parametrize on `--room-type`/`--fixture`; only `harness/terrarium_boot.py`'s factories and `main()` hardcode `"TEST"`.

**Tech Stack:** Python 3, pytest, dataclasses. Offline suite only — no live Arco, no browser needed for any test in this plan.

**Spec:** `docs/superpowers/specs/2026-08-19-demo-room-and-block-profile-design.md`

## Global Constraints

- **Run everything through the project venv:** `.venv/bin/python -m pytest tests -v`. There is NO bare `python`/usable `python3` for this suite. If this worktree has no `.venv`, create the symlink first: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` (run from the worktree root; `.gitignore` already ignores it).
- `control/` must import nothing outside the standard library and `control/` itself (module-level imports; pinned by an existing test).
- Blocks are **declarative-only** this slice: no renderer, backend, or engine code consumes block boundaries except the `--identify-blocks` harness tool.
- `_MAX_PROFILE_PIXELS = 170` keeps its name and value; only what it caps changes (per-block, not per-profile).
- No RGBW widening: `color_order="GRB"`, 3 channels per pixel, unchanged.
- Suite baseline at branch start: **1037 passed, 1 skipped**. Every task ends with the full suite green (task-targeted runs during TDD are fine; run the full suite before each commit).

---

### Task 1: `RoomBlock` + reshaped `RoomFixture` (per-block cap, explicit blocks in TEST)

**Files:**
- Modify: `control/room_profile.py`
- Test: `tests/test_room_profile.py`

**Interfaces:**
- Produces: `RoomBlock(name: str, start: int, count: int)` frozen dataclass, exported from `control.room_profile`.
- Produces: `RoomFixture(name: str, color_order: str, blocks: tuple[RoomBlock, ...], zones: tuple[RoomZone, ...])` — **`pixel_count` becomes a derived property** (sum of block counts), no longer a constructor field. Field order is exactly as written here.
- All existing `RoomProfile` derived properties (`pixel_count`, `channel_count`, `zones`, `fixture_slices()`) keep identical signatures and values for the existing TEST profile.

Note for the implementer: `RoomFixture` is constructed in `ROOM_PROFILES` and in several test files. `grep -rn "RoomFixture(" control/ tests/ harness/` and update every constructor call to the new signature (each existing fixture gets exactly one block spanning its full size). Do NOT add a default for `blocks` — every fixture's build-out is always explicit (spec §2).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_room_profile.py` (adjust imports at top of file to include `RoomBlock`):

```python
def _fixture(name="main", blocks=None, zones=(), color_order="GRB"):
    if blocks is None:
        blocks = (RoomBlock("b1", 0, 60),)
    return RoomFixture(name=name, color_order=color_order,
                       blocks=blocks, zones=zones)


def test_fixture_pixel_count_is_sum_of_blocks():
    f = _fixture(blocks=(RoomBlock("b1", 0, 30), RoomBlock("b2", 30, 50),
                        RoomBlock("b3", 80, 20)))
    assert f.pixel_count == 100


def test_overlapping_blocks_are_refused():
    with pytest.raises(ValueError, match="overlapping blocks"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 0, 40), RoomBlock("b2", 30, 30))),))


def test_block_gaps_are_refused():
    # Blocks define the fixture's own extent, so unlike zones they must
    # tile it exactly: a gap means a pixel range no physical device drives.
    with pytest.raises(ValueError, match="do not tile"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 0, 30), RoomBlock("b2", 40, 30))),))


def test_block_not_starting_at_zero_is_refused():
    with pytest.raises(ValueError, match="do not tile"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 10, 30),)),))


def test_duplicate_block_names_are_refused():
    with pytest.raises(ValueError, match="duplicate block names"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 0, 30), RoomBlock("b1", 30, 30))),))


def test_a_block_over_170px_is_refused():
    with pytest.raises(ValueError, match="single-universe"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("big", 0, 171),)),))


def test_a_fixture_over_170px_is_fine_when_each_block_is_under():
    # The cap moved from whole-profile to per-block: this is the whole
    # point of blocks (spec section 2).
    profile = RoomProfile(surface_id="p", fixtures=(
        _fixture(blocks=(RoomBlock("b1", 0, 170), RoomBlock("b2", 170, 170))),))
    assert profile.pixel_count == 340


def test_zero_or_negative_block_count_is_refused():
    with pytest.raises(ValueError, match="positive"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 0, 0),)),))


def test_test_profile_declares_explicit_blocks():
    profile = room_profile(RoomType.TEST)
    main, accent = profile.fixtures
    assert [b.name for b in main.blocks] == ["main"]
    assert main.pixel_count == 60
    assert [b.name for b in accent.blocks] == ["accent"]
    assert accent.pixel_count == 30
```

Also find the existing test asserting the whole-profile 170 px cap (grep `test_room_profile.py` for `_MAX_PROFILE_PIXELS` / `single-universe` / a >170 px total profile expected to raise) and **delete or invert it** — a multi-block profile over 170 px total is now valid (covered by `test_a_fixture_over_170px_is_fine_when_each_block_is_under`).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_profile.py -v`
Expected: FAIL — `ImportError: cannot import name 'RoomBlock'` (and/or TypeError on the new `RoomFixture` signature).

- [ ] **Step 3: Implement in `control/room_profile.py`**

```python
@dataclass(frozen=True)
class RoomBlock:
    """A physical LED device's own pixel range within a Fixture -- the
    build-out unit ("which literal LED device drives this pixel range").
    Individually capped at _MAX_PROFILE_PIXELS (one DMX universe / one
    controller's worth). Purely declarative this slice: no simulator or
    backend consumes block boundaries for output routing yet -- a future
    real per-controller adapter reads them off the profile with no further
    data-model change needed. Only harness/room_simulator.py's
    --identify-blocks debug tool reads them today. A different axis from
    RoomZone, which is gameplay/Console targeting; blocks are hardware
    composition. See
    docs/superpowers/specs/2026-08-19-demo-room-and-block-profile-design.md
    section 2."""
    name: str
    start: int
    count: int


@dataclass(frozen=True)
class RoomFixture:
    """One physical (or simulated) light fixture -- its own o2lite client,
    its own unique service name, once bound. One continuous physical run,
    decomposed into blocks (see RoomBlock). See design spec section 3 and
    the 2026-08-19 block spec section 2."""
    name: str
    color_order: str
    blocks: tuple[RoomBlock, ...]
    zones: tuple[RoomZone, ...]

    @property
    def pixel_count(self) -> int:
        """Derived, not stored: the sum of this fixture's blocks, which
        must tile the fixture exactly (validated in RoomProfile)."""
        return sum(b.count for b in self.blocks)
```

In `RoomProfile.__post_init__`, per fixture, BEFORE the existing zone checks (zone overrun validates against `fixture.pixel_count`, which is now block-derived):

```python
        for fixture in self.fixtures:
            if not fixture.blocks:
                raise ValueError(
                    f"fixture {fixture.name!r} declares no blocks")
            block_names = [b.name for b in fixture.blocks]
            if len(block_names) != len(set(block_names)):
                raise ValueError(
                    f"fixture {fixture.name!r} has duplicate block names: "
                    f"{block_names}")
            for block in fixture.blocks:
                if block.count <= 0:
                    raise ValueError(
                        f"block {fixture.name!r}.{block.name!r} must have a "
                        f"positive count, got {block.count}")
                if block.count > _MAX_PROFILE_PIXELS:
                    raise ValueError(
                        f"block {fixture.name!r}.{block.name!r} is "
                        f"{block.count} px, over the {_MAX_PROFILE_PIXELS} px "
                        f"single-universe cap")
            spans = sorted((b.start, b.start + b.count) for b in fixture.blocks)
            for i in range(1, len(spans)):
                if spans[i][0] < spans[i - 1][1]:
                    raise ValueError(
                        f"fixture {fixture.name!r} has overlapping blocks")
            expected = 0
            for start, end in spans:
                if start != expected:
                    raise ValueError(
                        f"fixture {fixture.name!r}'s blocks do not tile the "
                        f"fixture: gap before pixel {start}")
                expected = end
```

**Delete** the whole-profile cap check (`if self.pixel_count > _MAX_PROFILE_PIXELS: raise ...`) from `RoomProfile.__post_init__` — the per-block check replaces it. Update `_MAX_PROFILE_PIXELS`'s comment to say it caps one BLOCK (one device / one universe), not the profile.

Update `ROOM_PROFILES[RoomType.TEST]` to the new signature:

```python
            RoomFixture(
                name="main", color_order="GRB",
                blocks=(RoomBlock("main", 0, 60),),
                zones=(RoomZone("left", 0, 20),
                      RoomZone("center", 20, 20),
                      RoomZone("right", 40, 20))),
            RoomFixture(
                name="accent", color_order="GRB",
                blocks=(RoomBlock("accent", 0, 30),),
                zones=(RoomZone("low", 0, 15),
                      RoomZone("high", 15, 15))),
```

Then fix every other `RoomFixture(` constructor call found by `grep -rn "RoomFixture(" control/ tests/ harness/` the same way: one block named after the fixture, `start=0`, `count=` the old `pixel_count`, and drop the `pixel_count=` kwarg.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass (1037+9-ish passed, 1 skipped — new tests added, one cap test removed/inverted).

- [ ] **Step 5: Commit**

```bash
git add control/room_profile.py tests/test_room_profile.py $(git diff --name-only)
git commit -m "feat(room): add RoomBlock build-out unit; cap per block, not per profile"
```

---

### Task 2: DEMO's real-scale profile

**Files:**
- Modify: `control/room_profile.py` (add `RoomType.DEMO` entry to `ROOM_PROFILES`)
- Test: `tests/test_room_profile.py`

**Interfaces:**
- Consumes: `RoomBlock`, `RoomFixture` from Task 1.
- Produces: `room_profile(RoomType.DEMO)` returns a valid profile — 1 fixture `"array"`, 864 px, 2592 channels, 6 blocks `m1..m6` of 144 px, 3 zones `left`/`center`/`right` of 288 px.

- [ ] **Step 1: Write the failing tests**

In `tests/test_room_profile.py`, find the existing test asserting `room_profile(RoomType.DEMO)` raises `NotImplementedError` (near the "DEMO's backend is a deferred follow-up spec" comment, ~line 85) and **replace it** with:

```python
def test_demo_profile_matches_the_real_array_scale():
    """864 px = 6 m x 144 LED/m, the real Terrarium array
    (MM_HARDWARE_DESIGN.md section 7.1), one block per meter run."""
    profile = room_profile(RoomType.DEMO)
    assert profile.surface_id == "room_demo"
    (array,) = profile.fixtures
    assert array.name == "array"          # matches tests/test_room_binding.py
    assert array.pixel_count == 864
    assert profile.channel_count == 2592
    assert [b.name for b in array.blocks] == ["m1", "m2", "m3", "m4", "m5", "m6"]
    assert all(b.count == 144 for b in array.blocks)
    assert [z.name for z in array.zones] == ["left", "center", "right"]
    assert all(z.count == 288 for z in array.zones)


def test_demo_zones_and_blocks_are_independent_axes():
    """3 zones over 6 blocks, deliberately not 1:1 -- zones target
    gameplay, blocks describe hardware (spec section 2.1)."""
    (array,) = room_profile(RoomType.DEMO).fixtures
    zone_bounds = {(z.start, z.start + z.count) for z in array.zones}
    block_bounds = {(b.start, b.start + b.count) for b in array.blocks}
    assert zone_bounds != block_bounds
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_profile.py -v -k demo`
Expected: FAIL with `NotImplementedError: DEMO has no room profile`.

- [ ] **Step 3: Add the profile**

In `ROOM_PROFILES` (after the TEST entry), plus update `room_profile()`'s
docstring/error if it names only TEST:

```python
    RoomType.DEMO: RoomProfile(
        surface_id="room_demo",
        fixtures=(
            RoomFixture(
                name="array", color_order="GRB",
                # 144 LED/m x 6 m real array (MM_HARDWARE_DESIGN.md
                # section 7.1), one block per physical meter run. Synthetic
                # backend, real scale: unlocked by the per-block cap.
                blocks=(
                    RoomBlock("m1", 0, 144), RoomBlock("m2", 144, 144),
                    RoomBlock("m3", 288, 144), RoomBlock("m4", 432, 144),
                    RoomBlock("m5", 576, 144), RoomBlock("m6", 720, 144),
                ),
                # Gameplay/Console targeting thirds -- deliberately not 1:1
                # with the 6 blocks: zones and blocks are different axes.
                zones=(RoomZone("left", 0, 288),
                      RoomZone("center", 288, 288),
                      RoomZone("right", 576, 288)),
            ),
        ),
    ),
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass. If `tests/test_engine_triggers.py`'s empty-DEMO-room test (~line 279) relied on `room_profile(RoomType.DEMO)` raising `NotImplementedError`, update that test to use a `RoomType` mock or an empty `Room` whose profile lookup path it actually exercises — read the test's docstring first; it is asserting "an empty Room must never reach the canonical-dev path", which stays assertable with a DEMO room whose `bound` dict is empty.

- [ ] **Step 5: Commit**

```bash
git add control/room_profile.py tests/test_room_profile.py tests/test_engine_triggers.py
git commit -m "feat(room): real-scale DEMO profile, 864px in six 144px meter blocks"
```

---

### Task 3: `TestBit` supports DEMO

**Files:**
- Modify: `bits/test_bit.py`
- Test: `tests/test_boot.py` (or `tests/test_test_bit.py` if role-table tests live there — grep for `room_role` in `tests/` and put these beside the existing TEST-room-role tests)

**Interfaces:**
- Consumes: `room_role(RoomType, ...)` from `control.rooms` (already imported in `bits/test_bit.py`).
- Produces: `TestBit.room_types == {RoomType.TEST, RoomType.DEMO}`; `TestBit().role_table` contains roles `room_test` AND `room_demo`, with nodes `ROOM_TEST_NODE` / `ROOM_DEMO_NODE` in `node_map`.

- [ ] **Step 1: Write the failing tests**

```python
def test_test_bit_supports_test_and_demo_rooms():
    assert TestBit.room_types == {RoomType.TEST, RoomType.DEMO}


def test_test_bit_declares_a_room_role_per_supported_room_type():
    table = TestBit().role_table
    assert "room_test" in table.roles
    assert "room_demo" in table.roles
    assert table.node_map["ROOM_TEST_NODE"] == ["room_test"]
    assert table.node_map["ROOM_DEMO_NODE"] == ["room_demo"]
    # Same declared instruments: an instrument targets primary/zones,
    # never blocks, so nothing about the declaration is room-specific.
    assert (table.roles["room_test"].light_manifest
            == table.roles["room_demo"].light_manifest)
    # capacity is each profile's own fixture count (room_role reads it off
    # the profile): TEST has 2 fixtures, DEMO has 1.
    assert table.roles["room_test"].capacity == 2
    assert table.roles["room_demo"].capacity == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_boot.py -v -k "test_bit"`
Expected: FAIL — `room_types` is the inherited `{RoomType.TEST}` and `room_demo` is absent.

- [ ] **Step 3: Implement in `bits/test_bit.py`**

Add the class attribute right under `version = "0.1"`:

```python
    # TestBit is the reference fixture for BOTH shipped room types, so the
    # Scored/Jam validation loop works in either. control/boot.py reads
    # this off the class before instantiation.
    room_types = {RoomType.TEST, RoomType.DEMO}
```

In `role_table`, refactor the existing single `room_role(RoomType.TEST, ...)` call: extract the two manifest dicts to local variables (`room_light`, `room_ugen` — the exact dicts currently passed inline), then build both rooms from them:

```python
        room_entries = [
            room_role(rt, light_manifest=room_light, ugen_manifest=room_ugen)
            for rt in sorted(self.room_types, key=lambda t: t.name)
        ]
        roles = {"player": player, "jammer": jammer}
        node_map = {"TEST_PLAYER_NODE": ["player"],
                    "TEST_JAM_NODE": ["jammer"]}
        for room_name, room, room_node in room_entries:
            roles[room_name] = room
            node_map[room_node] = [room_name]
        return RoleTable(roles=roles, node_map=node_map)
```

(Keep the existing explanatory comment about `rainbow`/no-note-lane/no-cc:11 on the extracted `room_light` variable — it is load-bearing documentation.)

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass. Watch specifically for Console/uplink filtering tests (`tests/test_console_agent.py`'s room-hiding test) — `non_room_counts()` filters by `RoleClass.ROOM`, so the second room role should be filtered automatically; if a test pins the exact role-name list of TestBit, update it to include neither room role.

- [ ] **Step 5: Commit**

```bash
git add bits/test_bit.py tests/
git commit -m "feat(bits): TestBit supports the DEMO room type"
```

---

### Task 4: Thread `--room-type` through the boot harness

**Files:**
- Modify: `harness/terrarium_boot.py` (`_SimulatorFactory`, `_O2SimulatorFactory`, `main()`)
- Modify: `harness/run_stack.py` (pass-through flag)
- Test: `tests/test_terrarium_boot.py` (grep for the existing `_SimulatorFactory` tests and add beside them)

**Interfaces:**
- Consumes: `room_profile(RoomType.DEMO)` (Task 2), `TestBit.room_types` (Task 3).
- Produces: `_SimulatorFactory(server_url, *, popen=..., horizon=None, room_type="TEST")` and `_O2SimulatorFactory(ensemble, *, popen=..., room_type="TEST")` — both emit `--room-type <value>` in the spawned command instead of the hardcoded `"TEST"`. `terrarium_boot.main()` gains `--room-type {TEST,DEMO}` (default `TEST`); `run_stack` gains the same flag and forwards it.

- [ ] **Step 1: Write the failing tests**

Follow the existing `_SimulatorFactory` test pattern in `tests/test_terrarium_boot.py` (it uses a fake `popen` capturing `command`):

```python
def test_simulator_factory_spawns_with_its_room_type():
    captured = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            captured.append(command)
            self.pid = 4242
        def poll(self):
            return 0
        def wait(self, timeout=None):
            return 0

    factory = _SimulatorFactory("ws://x/ws", popen=FakePopen,
                                room_type="DEMO")
    teardown = TeardownStack()
    dev = factory(teardown, "array")
    assert dev == "sim-room-array"
    command = captured[0]
    i = command.index("--room-type")
    assert command[i + 1] == "DEMO"
```

(Mirror the same assertion for `_O2SimulatorFactory` if it has an existing fake-popen test; same `room_type="DEMO"` kwarg, same `--room-type DEMO` expectation.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -v -k room_type`
Expected: FAIL — `TypeError: unexpected keyword argument 'room_type'`.

- [ ] **Step 3: Implement**

In `_SimulatorFactory.__init__`, add `room_type: str = "TEST"` keyword, store as `self._room_type`; replace the hardcoded `command += ["--room-type", "TEST"]` with `command += ["--room-type", self._room_type]`. Same change in `_O2SimulatorFactory` (its command builds `"--room-type", "TEST"` at ~line 103).

In `build()`, both factory constructions pass `room_type=config.room_type.name` (config is already in scope).

In `main()`:

```python
    ap.add_argument("--room-type", default="TEST", choices=["TEST", "DEMO"],
                    help="Which RoomType to boot. DEMO configures the "
                         "simulated array backend (spec 2026-08-19); its "
                         "864 px canvas is otherwise identical in kind to "
                         "TEST's.")
```

and replace the hardcoded config line (~486):

```python
    room_type = RoomType[args.room_type]
    config = BootConfig(
        room_type=room_type, bit_name="TestBit",
        # DEMO's recipe requires an array backend (control/rooms.py);
        # "simulator" is the Terrarium-spawns-one value BootConfig already
        # defines. TEST ignores the field.
        array_backend="simulator" if room_type is RoomType.DEMO else None)
```

In `harness/run_stack.py`: add the same `--room-type` argparse flag (same default/choices/help) and append `["--room-type", args.room_type]` to the `terrarium_boot` child command it builds (grep for where it assembles the `harness.terrarium_boot` command; follow the pattern `--horizon` uses).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py harness/run_stack.py tests/test_terrarium_boot.py
git commit -m "feat(harness): thread --room-type through terrarium_boot and run_stack"
```

---

### Task 5: `--identify-blocks` debug tool

**Files:**
- Modify: `harness/room_simulator.py`
- Test: `tests/test_room_simulator.py` (add beside the existing `build()` tests; if that file does not exist, grep `tests/` for `room_simulator` and use the file that imports it)

**Interfaces:**
- Consumes: `room_profile(RoomType)`, `RoomFixture.blocks` (Task 1/2).
- Produces: `identify_blocks_frame(profile, fixture_name: str) -> bytes` in `harness/room_simulator.py` — one frame, `fixture.pixel_count * 3` bytes, each block painted a distinct solid color from a fixed 6-color palette in declaration order, bytes laid out per the fixture's `color_order`. Plus a `--identify-blocks` CLI flag.

- [ ] **Step 1: Write the failing tests**

```python
from control.room_profile import room_profile
from control.rooms import RoomType
from harness.room_simulator import BLOCK_PALETTE, identify_blocks_frame


def test_identify_blocks_frame_paints_demo_blocks_distinctly():
    profile = room_profile(RoomType.DEMO)
    frame = identify_blocks_frame(profile, "array")
    (array,) = profile.fixtures
    assert len(frame) == array.pixel_count * 3          # 2592
    # First pixel of each 144px block carries that block's own palette
    # color, GRB order per the profile.
    for i, block in enumerate(array.blocks):
        r, g, b = BLOCK_PALETTE[i % len(BLOCK_PALETTE)]
        offset = block.start * 3
        assert frame[offset:offset + 3] == bytes((g, r, b))
    # Adjacent blocks differ at their boundary.
    for prev, cur in zip(array.blocks, array.blocks[1:]):
        last_of_prev = (cur.start - 1) * 3
        first_of_cur = cur.start * 3
        assert frame[last_of_prev:last_of_prev + 3] != \
            frame[first_of_cur:first_of_cur + 3]


def test_identify_blocks_frame_works_for_a_single_block_fixture():
    profile = room_profile(RoomType.TEST)
    frame = identify_blocks_frame(profile, "accent")
    assert len(frame) == 30 * 3
    r, g, b = BLOCK_PALETTE[0]
    assert frame[:3] == bytes((g, r, b))
    assert frame == frame[:3] * 30
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py -v -k identify`
Expected: FAIL — `ImportError: cannot import name 'identify_blocks_frame'`.

- [ ] **Step 3: Implement in `harness/room_simulator.py`**

```python
# Fixed identification palette, assigned to blocks in declaration order
# (red, orange, yellow, green, blue, violet). RGB triples; laid out per the
# fixture's color_order when painted. Repeats past six blocks.
BLOCK_PALETTE: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0), (255, 128, 0), (255, 255, 0),
    (0, 255, 0), (0, 0, 255), (148, 0, 211),
)


def identify_blocks_frame(profile, fixture_name: str) -> bytes:
    """One static frame painting each of the fixture's blocks a distinct
    solid color, so a human can visually confirm the physical build-out
    mapping on the canvas. Harness-only: the one consumer of block
    boundaries this slice (blocks are otherwise declarative -- see
    control/room_profile.py's RoomBlock)."""
    fixture = next(f for f in profile.fixtures if f.name == fixture_name)
    order = fixture.color_order.upper()
    frame = bytearray(fixture.pixel_count * 3)
    for i, block in enumerate(fixture.blocks):
        rgb = dict(zip("RGB", BLOCK_PALETTE[i % len(BLOCK_PALETTE)]))
        px = bytes(rgb[ch] for ch in order)
        frame[block.start * 3:(block.start + block.count) * 3] = \
            px * block.count
    return bytes(frame)
```

In `main()`, add the flag and the bypass branch (before the websocket connect — an identify run never talks to Control at all):

```python
    parser.add_argument("--identify-blocks", action="store_true",
                        help="Debug: skip Control entirely; paint each of "
                             "this fixture's declared blocks a distinct "
                             "solid color and hold until Ctrl-C, so the "
                             "physical build-out mapping can be confirmed "
                             "visually. See the 2026-08-19 spec section 4.")
```

and after `backend.open()` / the "Watch the Room at ..." print:

```python
    if args.identify_blocks:
        from control.room_profile import room_profile
        from control.rooms import RoomType

        profile = room_profile(RoomType[args.room_type])
        backend.send(identify_blocks_frame(profile, args.fixture))
        print(f"identify-blocks: {args.fixture} painted; Ctrl-C to exit",
              flush=True)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            backend.close()
        return
```

(Add `import time` to `main()`'s local imports. `sigterm_as_keyboard_interrupt()` is already installed above, so a supervisor's SIGTERM exits through the same `finally`.)

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/room_simulator.py tests/test_room_simulator.py
git commit -m "feat(harness): --identify-blocks paints each block a distinct color"
```

---

### Task 6: Docs sync + live-verify checklist stub

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (via the `mm-deepdive-sync` flow at closeout — do not hand-edit mid-plan)
- No code.

This task is a reminder, not implementation: after the branch is done, run the deep-dive sync (the DEMO room, RoomBlock, per-block cap, and `--identify-blocks` all change documented behavior), and record that live verification per spec §7 is still outstanding:

- [ ] **Step 1:** Confirm all five prior tasks are committed and `.venv/bin/python -m pytest tests -q` is green.
- [ ] **Step 2:** Note in the PR description the two outstanding live-verify items from spec §7 (DEMO boot against a real Arco; `--identify-blocks` visual confirmation) — these need a human at a browser and a hand-started stack, and are NOT claimed done by this branch.
- [ ] **Step 3:** Run the `mm-deepdive-sync` skill at closeout per house convention.

---

## Manual verification (post-merge, needs a live Arco + browser)

Not part of the automated plan; copied from spec §7 for convenience.

**RUN ON: MYCOLOGICAL** (or wherever the Arco checkout lives)

```bash
.venv/bin/python -m harness.run_stack --room-type DEMO --devices 1
```

Confirm: DEMO boots, a device joins player/jam nodes, the Room's `rainbow` sweeps the 864 px canvas with no seam at any block boundary.

**RUN ON: MYCOLOGICAL**

```bash
.venv/bin/python -m harness.room_simulator --dev sim-room-array --fixture array --room-type DEMO --server ws://127.0.0.1:8771/ws --identify-blocks
```

Confirm: 6 distinct solid colors (red/orange/yellow/green/blue/violet), each exactly 144 px, in order.
