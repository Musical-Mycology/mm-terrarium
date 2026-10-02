# Tower Fixture Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Commit the Tower's 14-pixel layout as a Room and an instrument, add load-time checks that keep a fixture's instrument (pixel count, model zones) consistent with its room, prove an artist's Tower model will import cleanly, and hand Sophia a brief.

**Architecture:** `rooms/TOWER.toml` and `instruments/tower.toml` declare the layout with no `model` line (the no-layout fallback). A pure function `fixture_instrument_mismatch` in `control/room_profile.py` is called from `_parse_room` in `control/terrarium_config.py`, so every published room load (and every draft, via the catalog's existing error capture) checks the instrument against the fixture. An import-readiness test builds 14 synthetic markers in a temp catalog to dry-run the import recipe.

**Tech Stack:** Python 3 stdlib, pytest, TOML. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md`

## Global Constraints

- Run Python only as `.venv/bin/python` (a bare `python3` collects a false import error). The worktree's `.venv` is a symlink to the main checkout's venv and already exists.
- Full suite: `.venv/bin/python -m pytest tests -q`. Baseline on this branch: 3372 passed, 1 skipped. It must stay green after every task.
- No em dashes in any file written for this repo (use commas, colons or parentheses).
- Do not create any 3D model file under `instruments/models/`. Synthetic `.glb` bytes exist only inside tests, in `tmp_path`.
- `instruments/tower.toml` must not carry a `model` line in this plan (a published instrument whose model file is missing fails to load).
- The layout contract (spec section 4), verbatim:

| px | zone | block | element | x_mm | z_mm | size |
|---|---|---|---|---|---|---|
| 0 | progress | lower | stage 1/8 | 0 | 480 | medium |
| 1 | progress | lower | stage 2/8 | 0 | 632 | medium |
| 2 | progress | lower | stage 3/8 | 0 | 785 | medium |
| 3 | progress | lower | stage 4/8 | 0 | 937 | medium |
| 4 | progress | upper | stage 5/8 | 0 | 1089 | medium |
| 5 | progress | upper | stage 6/8 | 0 | 1242 | medium |
| 6 | progress | upper | stage 7/8 | 0 | 1394 | medium |
| 7 | progress | upper | stage 8/8 | 0 | 1547 | medium |
| 8 | responder | responders | R1 lower-left | -330 | 900 | medium |
| 9 | responder | responders | R2 lower-right | 315 | 1045 | medium |
| 10 | responder | responders | R3 upper-left | -330 | 1370 | medium |
| 11 | responder | responders | R4 upper-right | 310 | 1510 | medium |
| 12 | beat | par | PAR left | -80 | 90 | large |
| 13 | beat | par | PAR right | 80 | 90 | large |

---

### Task 1: The TOWER room and the tower instrument

**Files:**
- Create: `rooms/TOWER.toml`
- Create: `instruments/tower.toml`
- Test: `tests/test_tower_room.py`

**Interfaces:**
- Consumes: `control.terrarium_config.load_terrarium_config(path: str) -> TerrariumConfig` (rooms from `rooms/`, instruments from `instruments/`, via `terrarium.toml`).
- Produces: room `TOWER` (one fixture `tower`, 14 px, `color_order = "GRB"`); instrument `tower` (`pixels = 14`, `light.surface`, no model). Task 3 copies both files verbatim.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tower_room.py
"""The TOWER Room: one 14-pixel fixture, the layout contract of spec
docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md section 4."""

from control.terrarium_config import load_terrarium_config

CONFIG = load_terrarium_config("terrarium.toml")


def _fixture():
    profile = CONFIG.rooms["TOWER"].profile
    assert [f.name for f in profile.fixtures] == ["tower"]
    return profile.fixtures[0]


def test_tower_is_one_14_pixel_grb_fixture():
    fx = _fixture()
    assert fx.pixel_count == 14
    assert fx.color_order == "GRB"
    assert fx.channels == 3


def test_tower_blocks_follow_the_physical_runs():
    fx = _fixture()
    assert [(b.name, b.start, b.count) for b in fx.blocks] == [
        ("lower", 0, 4), ("upper", 4, 4), ("responders", 8, 4), ("par", 12, 2)]


def test_tower_zones_are_progress_responder_beat():
    fx = _fixture()
    assert [(z.name, z.start, z.count) for z in fx.zones] == [
        ("progress", 0, 8), ("responder", 8, 4), ("beat", 12, 2)]


def test_tower_room_uses_devicelink():
    assert CONFIG.rooms["TOWER"].backends == ("devicelink",)
    assert CONFIG.rooms["TOWER"].node_id == "ROOM_TOWER_NODE"


def test_tower_instrument_runs_on_the_no_model_fallback():
    inst = _fixture().instrument
    assert inst.name == "tower"
    assert inst.pixels == 14
    assert "light.surface" in inst.capabilities
    assert inst.layout == ()
    assert inst.model_sha256 is None
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/test_tower_room.py -v`
Expected: FAIL with `KeyError: 'TOWER'`.

- [ ] **Step 3: Write the instrument**

```toml
# instruments/tower.toml
description = "Tower fixture: 8 progress indicators, 4 responders, 2 base PAR lights (14 px)"
pixels = 14
capabilities = ["light.surface"]
accepted_cues = ["midi", "solid", "mute"]
# No model line yet: docs/tower-model-brief.md, section "Importing the file".
```

- [ ] **Step 4: Write the room**

```toml
# rooms/TOWER.toml
description = "Tower: 2100 mm fixture, 14 px (8 progress, 4 responders, 2 PARs)"
backends = ["devicelink"]

[[fixtures]]
name = "tower"
color_order = "GRB"
instrument = "tower"
  [[fixtures.blocks]]
  name = "lower"
  start = 0
  count = 4
  [[fixtures.blocks]]
  name = "upper"
  start = 4
  count = 4
  [[fixtures.blocks]]
  name = "responders"
  start = 8
  count = 4
  [[fixtures.blocks]]
  name = "par"
  start = 12
  count = 2
  [[fixtures.zones]]
  name = "progress"
  start = 0
  count = 8
  [[fixtures.zones]]
  name = "responder"
  start = 8
  count = 4
  [[fixtures.zones]]
  name = "beat"
  start = 12
  count = 2
```

- [ ] **Step 5: Run the test and the full suite**

Run: `.venv/bin/python -m pytest tests/test_tower_room.py -v`
Expected: 5 passed.
Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass (baseline count plus 5). If a test that enumerates every room or instrument now fails, read it: update it only if it asserts the exact set of rooms or instruments, and say so in the report.

- [ ] **Step 6: Commit**

```bash
git add rooms/TOWER.toml instruments/tower.toml tests/test_tower_room.py
git commit -m "feat(rooms): TOWER room and tower instrument, the 14-pixel layout contract"
```

---

### Task 2: Fixture and instrument consistency checks

**Files:**
- Modify: `control/room_profile.py` (add `fixture_instrument_mismatch` after the `RoomFixture` class)
- Modify: `control/terrarium_config.py` (`_parse_room`, after the `RoomProfile` is built, about line 636)
- Test: `tests/test_room_fixture_checks.py`

**Interfaces:**
- Consumes: `RoomFixture` (`name`, `zones`, `instrument`, `pixel_count`), `Instrument` (`name`, `pixels`, `layout`), `PixelLayout(index, x_mm, y_mm, z_mm, size, zone)` from `control.model_layout`.
- Produces: `fixture_instrument_mismatch(fixture: RoomFixture) -> str | None` in `control/room_profile.py`; `_parse_room` raises `TerrariumConfigError` with message `room '<NAME>' <problem>` when it returns a string. Task 3 relies on the message containing `LED_<nnn>` for a wrong-zone marker.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_room_fixture_checks.py
"""A Room fixture's instrument must agree with the fixture: its declared
pixel count, and its model's marker zones. Spec
docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md section 5.3."""

import pytest

from control.instrument import Instrument
from control.model_layout import PixelLayout
from control.terrarium_config import TerrariumConfigError, _parse_room


def _room_raw(count=4, zones=(("a", 0, 2), ("b", 2, 2))):
    return {
        "backends": ["devicelink"],
        "fixtures": [{
            "name": "f", "color_order": "GRB", "instrument": "strip",
            "blocks": [{"name": "all", "start": 0, "count": count}],
            "zones": [{"name": n, "start": s, "count": c} for n, s, c in zones],
        }],
    }


def _layout(zones):
    return tuple(PixelLayout(index=i, x_mm=0, y_mm=0, z_mm=i * 10,
                             size="medium", zone=z)
                 for i, z in enumerate(zones))


def _parse(instrument, **room_kwargs):
    return _parse_room("R", _room_raw(**room_kwargs), source="t.toml",
                       instruments={"strip": instrument})


def test_undeclared_pixels_and_no_model_loads():
    spec = _parse(Instrument(name="strip"))
    assert spec.profile.fixtures[0].pixel_count == 4


def test_matching_pixels_loads():
    _parse(Instrument(name="strip", pixels=4))


def test_mismatched_pixels_is_refused_and_names_both_counts():
    with pytest.raises(TerrariumConfigError) as exc:
        _parse(Instrument(name="strip", pixels=5))
    message = str(exc.value)
    assert "room 'R'" in message
    assert "fixture 'f'" in message
    assert "pixels = 5" in message
    assert "total 4" in message


def test_model_zones_matching_the_room_load():
    _parse(Instrument(name="strip", pixels=4,
                      layout=_layout(["a", "a", "b", "b"])))


def test_marker_with_no_zone_loads():
    _parse(Instrument(name="strip", pixels=4,
                      layout=_layout(["a", None, "b", None])))


def test_marker_in_the_wrong_zone_is_refused_and_named():
    with pytest.raises(TerrariumConfigError) as exc:
        _parse(Instrument(name="strip", pixels=4,
                          layout=_layout(["a", "a", "a", "b"])))
    message = str(exc.value)
    assert "LED_002" in message
    assert "'a'" in message
    assert "['b']" in message


def test_marker_zone_may_be_any_covering_room_zone():
    _parse(Instrument(name="strip", pixels=4,
                      layout=_layout(["a", "all", "b", "b"])),
           zones=(("a", 0, 2), ("b", 2, 2), ("all", 0, 4)))


def test_marker_count_must_match_the_fixture():
    with pytest.raises(TerrariumConfigError, match="3 markers"):
        _parse(Instrument(name="strip", layout=_layout(["a", "a", "b"])))
```

- [ ] **Step 2: Run them to see the failures**

Run: `.venv/bin/python -m pytest tests/test_room_fixture_checks.py -v`
Expected: the 3 "refused" tests FAIL (`DID NOT RAISE`); the 5 "loads" tests pass already.

- [ ] **Step 3: Add the pure check to `control/room_profile.py`**

Insert directly after the `RoomFixture` class (after its `channels` property):

```python
def fixture_instrument_mismatch(fixture: RoomFixture) -> str | None:
    """Why this fixture's instrument disagrees with the fixture, or None.
    A declared `pixels` must equal the blocks' total, and a model layout
    must have one marker per pixel with each named zone covering that
    pixel in this fixture's zones. Spec 2026-10-02 tower fixture layout,
    section 5.3."""
    inst = fixture.instrument
    count = fixture.pixel_count
    if inst.pixels and inst.pixels != count:
        return (f"fixture {fixture.name!r}: instrument {inst.name!r} declares "
                f"pixels = {inst.pixels} but the fixture's blocks total {count}")
    if not inst.layout:
        return None
    if len(inst.layout) != count:
        return (f"fixture {fixture.name!r}: instrument {inst.name!r}'s model has "
                f"{len(inst.layout)} markers but the fixture has {count} pixels")
    for p in inst.layout:
        if p.zone is None:
            continue
        covering = [z.name for z in fixture.zones
                    if z.start <= p.index < z.start + z.count]
        if p.zone not in covering:
            return (f"fixture {fixture.name!r}: model marker LED_{p.index:03d} "
                    f"is in zone {p.zone!r} but the room puts pixel {p.index} "
                    f"in {covering}")
    return None
```

- [ ] **Step 4: Call it from `_parse_room`**

In `control/terrarium_config.py`, add `fixture_instrument_mismatch` to the existing `from control.room_profile import ...` line (find it with `grep -n "from control.room_profile" control/terrarium_config.py`). Then in `_parse_room`, directly after the `try: profile = RoomProfile(...) except ...: raise ...` block and before `arco = rraw.get("arco", {})`, add:

```python
    for fixture in profile.fixtures:
        problem = fixture_instrument_mismatch(fixture)
        if problem is not None:
            raise TerrariumConfigError(source=source, key=key,
                                       message=f"room {rname!r} {problem}")
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `.venv/bin/python -m pytest tests/test_room_fixture_checks.py tests/test_tower_room.py -v`
Expected: all pass.
Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass. No existing room fixture's instrument declares `pixels` or a model, so no existing test should change.

- [ ] **Step 6: Commit**

```bash
git add control/room_profile.py control/terrarium_config.py tests/test_room_fixture_checks.py
git commit -m "feat(rooms): refuse a fixture whose instrument's pixels or model zones disagree with the room"
```

---

### Task 3: Import-readiness dry run

**Files:**
- Test: `tests/test_tower_import.py`

**Interfaces:**
- Consumes: Task 1's `rooms/TOWER.toml` and `instruments/tower.toml` (copied byte for byte); Task 2's error message (contains `LED_<nnn>`); `tests.glb_builder.GlbBuilder` (`add_box_mesh(center_m, half_size_m) -> int`, `build(nodes) -> bytes`); `load_terrarium_config`.
- Produces: `TOWER_LAYOUT`, the spec table as test data, and proof that the import recipe (save the file, add the model line, load) works.

The axis convention, from `tools/generate_model_fixture.py::_layout_mm_to_gltf_m`: layout `(x, y, z)` mm maps to glTF `(x/1000, z/1000, -y/1000)` metres. Marker size comes from the box's full width: medium is 4 to under 8 mm (use 5 mm), large is 8 mm and over (use 10 mm).

- [ ] **Step 1: Write the test**

```python
# tests/test_tower_import.py
"""Dry run of the Tower model import recipe (spec
docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md section 6):
copy the real TOWER room and tower instrument into a temp Terrarium, drop in
a synthetic 14-marker .glb, add the model line, and load. This is not a Tower
model: it is 14 boxes at the contract's nominal positions."""

import shutil
from pathlib import Path

import pytest

from control.terrarium_config import TerrariumConfigError, load_terrarium_config
from tests.glb_builder import GlbBuilder

REPO = Path(__file__).resolve().parents[1]

# (px, zone, x_mm, z_mm, size): spec section 4.
TOWER_LAYOUT = [
    (0, "progress", 0, 480, "medium"),
    (1, "progress", 0, 632, "medium"),
    (2, "progress", 0, 785, "medium"),
    (3, "progress", 0, 937, "medium"),
    (4, "progress", 0, 1089, "medium"),
    (5, "progress", 0, 1242, "medium"),
    (6, "progress", 0, 1394, "medium"),
    (7, "progress", 0, 1547, "medium"),
    (8, "responder", -330, 900, "medium"),
    (9, "responder", 315, 1045, "medium"),
    (10, "responder", -330, 1370, "medium"),
    (11, "responder", 310, 1510, "medium"),
    (12, "beat", -80, 90, "large"),
    (13, "beat", 80, 90, "large"),
]
_WIDTH_MM = {"medium": 5.0, "large": 10.0}
_ZONES = ["progress", "responder", "beat"]


def _markers_glb(zone_override=None) -> bytes:
    """14 box markers under LEDs::<zone>; zone_override maps px to a
    different zone layer, to model an artist's mistake."""
    zone_override = zone_override or {}
    builder = GlbBuilder()
    nodes = [{"name": "LEDs", "children": [1, 2, 3]}]
    nodes += [{"name": z, "children": []} for z in _ZONES]
    for px, zone, x_mm, z_mm, size in TOWER_LAYOUT:
        center_m = (x_mm / 1000.0, z_mm / 1000.0, 0.0)
        mesh = builder.add_box_mesh(center_m, _WIDTH_MM[size] / 2000.0)
        nodes.append({"name": f"LED_{px:03d}", "mesh": mesh})
        layer = zone_override.get(px, zone)
        nodes[1 + _ZONES.index(layer)]["children"].append(len(nodes) - 1)
    return builder.build(nodes)


def _terrarium_with_model(tmp_path: Path, glb: bytes) -> str:
    (tmp_path / "instruments" / "models").mkdir(parents=True)
    (tmp_path / "rooms").mkdir()
    shutil.copy(REPO / "rooms" / "TOWER.toml", tmp_path / "rooms" / "TOWER.toml")
    inst_text = (REPO / "instruments" / "tower.toml").read_text()
    assert "\nmodel" not in inst_text  # the repo still runs on the fallback
    (tmp_path / "instruments" / "tower.toml").write_text(
        inst_text + 'model = "models/tower.glb"\n')
    (tmp_path / "instruments" / "models" / "tower.glb").write_bytes(glb)
    cfg = tmp_path / "terrarium.toml"
    cfg.write_text('schema = 1\n[terrarium]\nname = "tower-import"\n'
                   'instrument_paths = ["instruments"]\nroom_paths = ["rooms"]\n')
    return str(cfg)


def test_a_model_matching_the_contract_imports(tmp_path):
    config = load_terrarium_config(_terrarium_with_model(tmp_path, _markers_glb()))
    layout = config.rooms["TOWER"].profile.fixtures[0].instrument.layout
    assert len(layout) == 14
    for p, (px, zone, x_mm, z_mm, size) in zip(layout, TOWER_LAYOUT):
        assert p.index == px
        assert p.zone == zone
        assert abs(p.x_mm - x_mm) <= 1
        assert abs(p.z_mm - z_mm) <= 1
        assert p.size == size


def test_a_marker_in_the_wrong_layer_is_refused_by_name(tmp_path):
    path = _terrarium_with_model(tmp_path, _markers_glb({12: "responder"}))
    with pytest.raises(TerrariumConfigError, match="LED_012"):
        load_terrarium_config(path)
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_tower_import.py -v`
Expected: 2 passed (Tasks 1 and 2 are already in). If `test_a_model_matching_the_contract_imports` fails on size or position, the cause is in this test's marker construction, not in production code: re-check the axis mapping and widths above.

- [ ] **Step 3: Prove the wrong-layer test depends on Task 2**

Temporarily comment out the `raise` added to `_parse_room` in Task 2, re-run `tests/test_tower_import.py`, confirm `test_a_marker_in_the_wrong_layer_is_refused_by_name` FAILS, then restore the line and confirm both pass. Do not commit the temporary change.

- [ ] **Step 4: Full suite and commit**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass.

```bash
git add tests/test_tower_import.py
git commit -m "test(rooms): dry-run the Tower model import against the layout contract"
```

---

### Task 4: Sophia's brief, the artist guide, the deep-dive, superseded markers

**Files:**
- Create: `docs/tower-model-brief.md`
- Modify: `docs/instrument-model-guide.md` (section "LED markers", the first bullet that says "The number is the LED's position on the physical data chain")
- Modify: `docs/MM_TERRARIUM.md` (end of the "LED layout models (`control/model_layout.py`)" subsection, after the "Bake host" paragraph)
- Modify: `docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md` (heading "### 4.4 Tower..." at about line 186, and the "**Tower order, Week 4" paragraph at about line 319)
- Modify: `docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md` (headings "## Task A7", "## Task B3", "## Task C1")

**Interfaces:**
- Consumes: the Global Constraints table; Task 2's check names; the import recipe in spec section 6.
- Produces: documentation only.

- [ ] **Step 1: Write `docs/tower-model-brief.md`**

```markdown
# Tower model brief

What the Tower's 3D model needs, for the artist. The layout contract is
`docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md`; the general
modelling rules are `docs/instrument-model-guide.md`. This page is everything
specific to the Tower.

## Origin, units and axes

Model the whole 2100 mm fixture as drawn in "Fixture Tower - 2100 mm
Dimension Specification": ground center at the world origin, the Tower
standing up the Z axis, the drawing's front elevation facing the viewer (the
drawing's X is the model's X, viewer's right positive). Any unit is fine; the
exporter converts.

## LED markers: 14 spheres

One sphere per light, centred where that light emits, named and layered
exactly as below. Positions are nominal: keep each indicator within 5 mm of
its Z, and each responder and PAR within 25 mm of its X and Z. Depth (Y) is
yours: put each sphere on the face where the light actually comes out.

| Sphere | Layer | Light | X mm | Z mm | Sphere diameter |
|---|---|---|---|---|---|
| `LED_000` | `LEDs::progress` | indicator stage 1/8 | 0 | 480 | 5 mm |
| `LED_001` | `LEDs::progress` | indicator stage 2/8 | 0 | 632 | 5 mm |
| `LED_002` | `LEDs::progress` | indicator stage 3/8 | 0 | 785 | 5 mm |
| `LED_003` | `LEDs::progress` | indicator stage 4/8 | 0 | 937 | 5 mm |
| `LED_004` | `LEDs::progress` | indicator stage 5/8 | 0 | 1089 | 5 mm |
| `LED_005` | `LEDs::progress` | indicator stage 6/8 | 0 | 1242 | 5 mm |
| `LED_006` | `LEDs::progress` | indicator stage 7/8 | 0 | 1394 | 5 mm |
| `LED_007` | `LEDs::progress` | indicator stage 8/8 | 0 | 1547 | 5 mm |
| `LED_008` | `LEDs::responder` | responder R1, lower left | -330 | 900 | 5 mm |
| `LED_009` | `LEDs::responder` | responder R2, lower right | 315 | 1045 | 5 mm |
| `LED_010` | `LEDs::responder` | responder R3, upper left | -330 | 1370 | 5 mm |
| `LED_011` | `LEDs::responder` | responder R4, upper right | 310 | 1510 | 5 mm |
| `LED_012` | `LEDs::beat` | PAR light, left | -80 | 90 | 10 mm |
| `LED_013` | `LEDs::beat` | PAR light, right | 80 | 90 | 10 mm |

- Exactly these 14 names, no others under `LEDs`, no `primary` layer.
- The indicators sit on one straight vertical line (X = 0): the factory chain's
  152.4 mm pitch cannot reach the zig-zag the elevation sketches.
- Sphere diameter sets the glow size band (5 mm is `medium`, 10 mm is `large`).

## The rest of the model

Mast, mushroom stages with their silicone diffusers, the four 190 mm
responder discs, the crown and the maitake base, with PBR materials as the
artist guide describes (translucent parts get transmission; an opaque material
around a light bakes dark). The crown has no light source in this revision.

## Delivering

One `.glb`, exported with the artist guide's settings (layers on, Rhino Z to
glTF Y on, Draco off). Send it to Chris.

## Importing the file (maintainers)

1. Save it as `instruments/models/tower.glb`.
2. Add `model = "models/tower.glb"` to `instruments/tower.toml` (same commit).
3. Run `.venv/bin/python -m pytest tests -q`. The catalog refuses a wrong
   name, count or size, and the room check refuses a sphere whose layer does
   not match the `TOWER` room's zones, naming the sphere (for example
   `LED_012`).
4. Bake on Mycologist and export to mm-tuneshroom as `docs/MM_TERRARIUM.md`,
   *LED layout models*, describes.
```

- [ ] **Step 2: Clarify the marker number in `docs/instrument-model-guide.md`**

Replace this text in the first bullet of "## LED markers":

```
  number is the LED's position on the physical data chain**: `LED_000` is
  the first SK6812 after the controller. Indices run 0 to N-1 with no gaps
  and no duplicates, where N is the instrument's pixel count.
```

with:

```
  number is the LED's frame index**: for pixels on a data chain that is the
  position on the chain (`LED_000` is the first pixel after the controller);
  a light the firmware drives another way (the Tower's PARs, over DMX) still
  takes its frame index. Indices run 0 to N-1 with no gaps and no
  duplicates, where N is the instrument's pixel count. A Room fixture puts
  its origin at ground center, as its drawing does.
```

- [ ] **Step 3: Add the Tower paragraph to `docs/MM_TERRARIUM.md`**

At the end of the "#### LED layout models (`control/model_layout.py`)" subsection (after the paragraph beginning "**Bake host: Mycologist"), add:

```markdown
**Room fixtures and the Tower.** `_parse_room` refuses a fixture whose
instrument disagrees with it (`fixture_instrument_mismatch`,
`control/room_profile.py`): a declared `pixels` must equal the blocks' total,
and a model must have one marker per pixel with each marker's zone one of the
room zones covering it. `rooms/TOWER.toml` is the first fixture this matters
for: 14 px (`progress` 0-7, `responder` 8-11, `beat` 12-13, the two base PARs),
`GRB`, instrument `tower` with no model yet, so it runs on the no-layout
fallback (a 14-dot strip in the Console). The artist's brief and the import
recipe are `docs/tower-model-brief.md`; `tests/test_tower_import.py` dry-runs
the import. Spec: `docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md`.
```

- [ ] **Step 4: Mark the superseded sections**

Directly under each of these headings, insert the given line followed by a blank line:

In `docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md`:
- under `### 4.4 Tower: eight segments on one controller, bound as a Room fixture`:
  `> **Superseded 2026-10-02** by docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md: the Tower is 8 single 5 V pixels plus 4 responders and 2 PARs (14 px), not a 120 px RGBW strip.`
- immediately before the paragraph starting `**Tower order, Week 4`:
  `> **Superseded 2026-10-02:** the strip order below is replaced by the Tower drawing's parts (12 square 5 V addressable pixels, two PARs); see docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md.`

In `docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md`, under each of `## Task A7 [FW]: Tower build target`, `## Task B3 [HW]: Tower build` and `## Task C1 [SW]: The TOWER room and the Tower's binding`:
  `> **Superseded 2026-10-02** by docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md (14 px, not 120; the room and instrument are committed; firmware is filed separately).`

- [ ] **Step 5: Check and commit**

Run: `grep -c "—" docs/tower-model-brief.md docs/instrument-model-guide.md`
Expected: both counts 0 (no em dashes introduced; if the guide already had some, the count must be unchanged from `git show HEAD:docs/instrument-model-guide.md | grep -c "—"`).
Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass (some tests render diagrams or lint docs).

```bash
git add docs/tower-model-brief.md docs/instrument-model-guide.md docs/MM_TERRARIUM.md docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md
git commit -m "docs: Tower model brief, frame-index marker rule, deep-dive entry, superseded markers"
```

---

### Task 5: mm-devshroom Tower firmware issue draft

**Files:**
- Create: `docs/upstream/2026-10-02-devshroom-tower-issue.md`

**Interfaces:**
- Consumes: spec section 7.
- Produces: an issue body ready to file on `Musical-Mycology/mm-devshroom`. Filing it is NOT part of this task (it is outward-facing and needs Chris's go-ahead at closeout).

- [ ] **Step 1: Write the draft**

```markdown
# mm-devshroom: Tower firmware target (14 px, two data pins, DMX PARs)

Draft for an issue on Musical-Mycology/mm-devshroom, assignee Victor Lu.
Contract: mm-terrarium `docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md`
(sections 4 and 7). This replaces the `tower` env of the 2026-09-11 ESP32 plan
(Task A7: `PIXEL_COUNT=120`, 2.4 A), which assumed a 120 px RGBW strip.

## What the Tower is

Per the Tower drawing: 8 indicator stages, each one addressable 5 V pixel on a
factory-wired three-core chain (152.4 mm pitch, locking connector at the
Y = 1050 split), 4 detachable responder discs with one pixel each, and 2
off-the-shelf PAR lights in the base. The Room binds it as one fixture of 14
pixels; mm-terrarium's `rooms/TOWER.toml` is committed.

## Asks

1. A `tower` PlatformIO env: `PIXEL_COUNT=14`. A `/<dev>/leds` frame is
   42 bytes, `GRB`, frame index = the layout table below.
2. Output px 0-7 on data pin A (indicator chain, stage 1 at the base first)
   and px 8-11 on data pin B (responders R1 to R4, starting from the base).
   Separate pins so a responder unplugged for transport cannot cut the
   indicator chain.
3. Convert px 12-13 to DMX for the two PARs (left, right). The channel map
   depends on the PAR model; please note the model and map in the PR.
4. Power limiter at 1.0 A on a supply of at least 2 A at 5 V (12 chain pixels
   at 60 mA full white is 0.72 A). The PARs are mains powered and outside the
   limiter.
5. Bench check: a pure red frame shows red on every indicator and responder
   (confirms `GRB`).

## Layout (frame index)

| px | element |
|---|---|
| 0-7 | indicator stages 1/8 (base) to 8/8 |
| 8 | responder R1, lower left |
| 9 | responder R2, lower right |
| 10 | responder R3, upper left |
| 11 | responder R4, upper right |
| 12 | PAR left |
| 13 | PAR right |
```

- [ ] **Step 2: Commit**

```bash
git add docs/upstream/2026-10-02-devshroom-tower-issue.md
git commit -m "docs(upstream): mm-devshroom Tower firmware issue draft"
```
