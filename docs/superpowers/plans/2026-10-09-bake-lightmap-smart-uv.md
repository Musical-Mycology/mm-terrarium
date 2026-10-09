# Bake light-map unwrap (Smart UV Project) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tools/bake_model.py` bake real-size models by unwrapping the shared light-map atlas with Smart UV Project, and refuse a too-thin atlas before the first LED.

**Architecture:** Pure-Python helpers in `tools/model_bake_helpers.py` (UV area, texels, island margin, refusal) carry every testable decision; `tools/bake_model.py` and `tools/blender_probe.py` only drive bpy and call them. bpy code is verified on the bake host, not in pytest.

**Tech Stack:** Python 3 stdlib + pytest (`.venv`), Blender 4.5.14 LTS headless on Mycologist via the `portal` skill.

**Spec:** `docs/superpowers/specs/2026-10-09-bake-lightmap-smart-uv-design.md`

## Global Constraints

- Blender pin stays `PINNED_BLENDER = "4.5"`; bpy property names only as the bake host's RNA reports them: `smart_project(angle_limit, margin_method, island_margin, area_weight, correct_aspect, scale_to_bounds)`, `margin_method` item `"FRACTION"`.
- `MIN_LIGHTMAP_TEXELS = 16`; island margin `2 * BAKE_MARGIN_PX / resolution` with `BAKE_MARGIN_PX = 4`; `angle_limit = math.radians(66.0)`.
- Tests run with `.venv/bin/python -m pytest` (never bare `python3`).
- Bakes run as `claude-ops` on Mycologist through `portal ssh`, in the disposable `/Users/claude-ops/mm-bake/src`, with Chris's go-ahead (given 2026-10-09 for this work).
- The committed fixture bake must stay at `--resolution 256` (`tests/test_model_fixture.py` pins it).
- No em dashes in any doc or message.

---

### Task 1: Atlas helpers

**Files:**
- Modify: `tools/model_bake_helpers.py` (after `normalise_maps`, around line 396)
- Test: `tests/test_model_bake_helpers.py` (the import block at line 272 and new tests after `test_normalise_maps_refuses_an_all_black_bake`)

**Interfaces:**
- Produces: `MIN_LIGHTMAP_TEXELS: int = 16`; `uv_polygon_area(points: list) -> float`; `lightmap_texels(polygons: list, resolution: int) -> float`; `lightmap_island_margin(resolution: int, margin_px: int) -> float`; `refuse_thin_lightmap(coverage: dict, resolution: int, minimum: int = MIN_LIGHTMAP_TEXELS) -> None` (raises `BakeError`).

- [ ] **Step 1: Write the failing tests**

Extend the import at line 272 to add `MIN_LIGHTMAP_TEXELS, lightmap_island_margin, lightmap_texels, refuse_thin_lightmap, uv_polygon_area`, then add:

```python
def test_uv_polygon_area_of_a_unit_square_and_a_triangle():
    assert uv_polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)]) == 1.0
    assert uv_polygon_area([(0, 0), (0.5, 0), (0, 0.5)]) == 0.125


def test_uv_polygon_area_ignores_winding_and_degenerate_polygons():
    assert uv_polygon_area([(0, 1), (1, 1), (1, 0), (0, 0)]) == 1.0
    assert uv_polygon_area([(0, 0), (1, 1)]) == 0.0


def test_lightmap_texels_scales_summed_area_by_resolution_squared():
    quarter = [(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)]
    assert lightmap_texels([quarter, quarter], 4) == 8.0


def test_lightmap_island_margin_is_two_bake_margins_as_a_fraction():
    assert lightmap_island_margin(1024, 4) == 8 / 1024


def test_refuse_thin_lightmap_names_the_thinnest_mesh():
    with pytest.raises(BakeError, match=r"'Mesh_39' gets 3 light-map texels at 1024 px \(minimum 16\)"):
        refuse_thin_lightmap({"Body": 900.0, "Mesh_39": 3.2, "Mesh_7": 10.0}, 1024)


def test_refuse_thin_lightmap_passes_at_the_floor():
    refuse_thin_lightmap({"Body": float(MIN_LIGHTMAP_TEXELS)}, 256)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_model_bake_helpers.py -q`
Expected: collection error, `ImportError: cannot import name 'MIN_LIGHTMAP_TEXELS'`.

- [ ] **Step 3: Implement**

In `tools/model_bake_helpers.py`, after `normalise_maps`:

```python
MIN_LIGHTMAP_TEXELS = 16


def uv_polygon_area(points: list) -> float:
    """Shoelace area of one UV polygon, (u, v) points in order; winding
    does not matter and fewer than three points is 0."""
    if len(points) < 3:
        return 0.0
    twice = 0.0
    for (u0, v0), (u1, v1) in zip(points, points[1:] + points[:1]):
        twice += u0 * v1 - u1 * v0
    return abs(twice) / 2.0


def lightmap_texels(polygons: list, resolution: int) -> float:
    """A mesh's light-map coverage in texels: its UV polygons' summed
    area on a resolution x resolution atlas."""
    return sum(uv_polygon_area(p) for p in polygons) * resolution * resolution


def lightmap_island_margin(resolution: int, margin_px: int) -> float:
    """Smart UV Project's island margin (margin_method FRACTION): two bake
    margins, so the bake's dilation never bleeds one island into another."""
    return 2 * margin_px / resolution


def refuse_thin_lightmap(coverage: dict, resolution: int,
                         minimum: int = MIN_LIGHTMAP_TEXELS) -> None:
    """Refuses before any LED is baked when a mesh's share of the shared
    atlas is under `minimum` texels (spec 2026-10-09): such a mesh bakes
    black. `coverage` maps mesh name to texels."""
    if not coverage:
        return
    name, texels = min(coverage.items(), key=lambda item: item[1])
    if texels < minimum:
        raise BakeError(
            f"mesh {name!r} gets {int(texels)} light-map texels at {resolution} px "
            f"(minimum {minimum}): simplify or drop small parts, or raise --resolution")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_model_bake_helpers.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tools/model_bake_helpers.py tests/test_model_bake_helpers.py
git commit -m "feat(bake): atlas helpers: UV area, texels, island margin, thin-atlas refusal"
```

### Task 2: Smart UV Project in the bake and the probe

**Files:**
- Modify: `tools/bake_model.py` (imports line 19-32; `_lightmap_pack` lines 92-102; `main` around line 222)
- Modify: `tools/blender_probe.py` (`_packed_cube` lines 66-82; `main` lines 179 and 185)

**Interfaces:**
- Consumes: Task 1's `lightmap_island_margin`, `lightmap_texels`, `refuse_thin_lightmap`.
- Produces: `_lightmap_unwrap(targets: list, resolution: int) -> None` and `_lightmap_coverage(targets: list, resolution: int) -> dict` in `tools/bake_model.py`.

No pytest can import bpy; this task's check is a syntax compile here and the bake-host runs in Task 3.

- [ ] **Step 1: Replace `_lightmap_pack` in `tools/bake_model.py`**

Add `import math` to the stdlib imports and `lightmap_island_margin, lightmap_texels, refuse_thin_lightmap` to the `tools.model_bake_helpers` import list. Replace the whole `_lightmap_pack` function with:

```python
def _lightmap_unwrap(targets: list, resolution: int) -> None:
    """All bake targets unwrapped together into one shared atlas with
    Smart UV Project (spec 2026-10-09; amends spec 5.3 step 2). Lightmap
    Pack made one island per face and left a 188k-face model with 0.008
    texels per face. Smart UV Project packs its own charts."""
    _select_only(targets)
    for obj in targets:
        obj.data.uv_layers.active = obj.data.uv_layers[LIGHTMAP_UV]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(
        angle_limit=math.radians(66.0), margin_method="FRACTION",
        island_margin=lightmap_island_margin(resolution, BAKE_MARGIN_PX),
        area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def _lightmap_coverage(targets: list, resolution: int) -> dict:
    """Each target's light-map area in texels, from its 'lightmap' UVs."""
    coverage = {}
    for obj in targets:
        uv = obj.data.uv_layers[LIGHTMAP_UV].data
        polygons = [[tuple(uv[i].uv) for i in poly.loop_indices]
                    for poly in obj.data.polygons]
        coverage[obj.name] = lightmap_texels(polygons, resolution)
    return coverage
```

In `main`, replace `_lightmap_pack(targets)` with:

```python
    _lightmap_unwrap(targets, args.resolution)
    refuse_thin_lightmap(_lightmap_coverage(targets, args.resolution), args.resolution)
```

- [ ] **Step 2: Update `tools/blender_probe.py`**

Add `import math`. In `_packed_cube`, replace the `bpy.ops.uv.lightmap_pack(...)` call with:

```python
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), margin_method="FRACTION",
                             island_margin=8 / 1024, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
```

and its docstring's "lightmap set packed" with "lightmap set unwrapped (Smart UV Project, as the bake does)". In `main`, replace `_section(report, "op_lightmap_pack", lambda: _op_props(bpy.ops.uv.lightmap_pack))` with `_section(report, "op_smart_project", lambda: _op_props(bpy.ops.uv.smart_project))`, and rename the `_lightmap_pack` function and its `"lightmap_pack"` section to `_lightmap_unwrap` / `"lightmap_unwrap"`.

- [ ] **Step 3: Check nothing still names Lightmap Pack, and that both files compile**

Run: `grep -n "lightmap_pack" tools/bake_model.py tools/blender_probe.py; .venv/bin/python -m py_compile tools/bake_model.py tools/blender_probe.py && echo compiled`
Expected: no grep output, then `compiled`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass (no test imports bpy).

- [ ] **Step 5: Commit**

```bash
git add tools/bake_model.py tools/blender_probe.py
git commit -m "feat(bake): unwrap the shared atlas with Smart UV Project; refuse a thin atlas before baking"
```

### Task 3: Bake-host verification and the fixture re-bake

**Files:**
- Modify: `tests/fixtures/models/marker_fixture.baked.glb` (re-baked)

**Interfaces:**
- Consumes: Task 2's bake and probe.

- [ ] **Step 1: Ship the branch to the bake host**

```bash
portal ssh mycologist 'rm -rf ~/mm-bake/src && mkdir -p ~/mm-bake/src'
git archive HEAD control tools tests/fixtures/models | portal ssh mycologist 'tar -x -C ~/mm-bake/src'
portal ssh mycologist "echo $(git rev-parse --short=12 HEAD) > ~/mm-bake/src/COMMIT"
```

- [ ] **Step 2: Run the probe**

```bash
portal ssh mycologist 'cd ~/mm-bake/src && /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 -P tools/blender_probe.py 2>&1 | sed -n "/MM_PROBE_BEGIN/,/MM_PROBE_END/p"' > probe.json
```

Expected: `op_smart_project` lists `angle_limit`, `margin_method` (items include `FRACTION`), `island_margin`, `area_weight`, `correct_aspect`, `scale_to_bounds`; `lightmap_unwrap.lightmap_uv_range` within [0, 1]; `export_plain.primitive_attributes` include `TEXCOORD_1`; `mini_bake` peaks above 0; no section has an `error` key. Delete `probe.json` afterwards (not committed).

- [ ] **Step 3: Re-bake the fixture at 256 px and bring it back**

```bash
portal ssh mycologist 'cd ~/mm-bake/src && nice -n 10 /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 -P tools/bake_model.py -- tests/fixtures/models/marker_fixture.glb --resolution 256 2>&1 | grep -a "bake_model:"'
portal ssh mycologist 'base64 < ~/mm-bake/src/tests/fixtures/models/marker_fixture.baked.glb' | base64 -d > tests/fixtures/models/marker_fixture.baked.glb
portal ssh mycologist 'shasum -a 256 ~/mm-bake/src/tests/fixtures/models/marker_fixture.baked.glb'; shasum -a 256 tests/fixtures/models/marker_fixture.baked.glb
```

Expected: `bake_model: wrote ... (map_scale ...)`, and the two sha256 values match.

- [ ] **Step 4: Run the fixture and full tests**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, including `tests/test_model_fixture.py::test_committed_real_bake_is_a_contract_valid_bake_of_the_source_fixture`.

- [ ] **Step 5: Bake the test Tower (not committed)**

From branch `claude/tower-test-model-bef12b` (holds `instruments/models/tower.glb`), with this branch's tools:

```bash
git archive claude/tower-test-model-bef12b instruments/models | portal ssh mycologist 'tar -x -C ~/mm-bake/src'
portal ssh mycologist 'cd ~/mm-bake/src && nohup nice -n 10 /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 -P tools/bake_model.py -- instruments/models/tower.glb > ~/mm-bake/bake.out 2>&1 < /dev/null &'
```

Poll `grep -a "bake_model:" ~/mm-bake/bake.out` until it prints `wrote` (about 30 minutes) or an error. Expected: no thin-atlas refusal, all 14 LEDs, `wrote .../tower.baked.glb`.

- [ ] **Step 6: Commit the fixture bake**

```bash
git add tests/fixtures/models/marker_fixture.baked.glb
git commit -m "test(bake): re-bake the marker fixture with Smart UV Project on Mycologist (Blender 4.5.14)"
```

### Task 4: Deep-dive

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (*LED layout models*, the **Bake and export** paragraph, around line 1285)

- [ ] **Step 1: Edit the paragraph**

The sentence below wraps across several lines in the file; match it across the line breaks. Replace "bakes per-LED light maps in headless Blender (Cycles Diffuse + Transmission per LED, summed, row-flipped, normalised by the brightest texel, packed 4 LEDs per RGBA PNG)" with:

"bakes per-LED light maps in headless Blender: every mesh unwrapped into one shared atlas with Smart UV Project (Lightmap Pack's one island per face left a 188k-face model with 0.008 texels per face, so every map baked black; spec `docs/superpowers/specs/2026-10-09-bake-lightmap-smart-uv-design.md`), a refusal before the first LED if any mesh gets under 16 atlas texels, then Cycles Diffuse + Transmission per LED, summed, row-flipped, normalised by the brightest texel, packed 4 LEDs per RGBA PNG"

and in the *Bake host* paragraph, after "The committed `tests/fixtures/models/marker_fixture.baked.glb` is the first real bake (256 px, the T2 gate)", add " and was re-baked 2026-10-09 with Smart UV Project".

- [ ] **Step 2: Check and commit**

Run: `grep -n "Smart UV Project" docs/MM_TERRARIUM.md` (expect 2 hits) and `.venv/bin/python -m pytest tests -q` (docs tests pass).

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): deep-dive bake paragraph names Smart UV Project and the thin-atlas refusal"
```
