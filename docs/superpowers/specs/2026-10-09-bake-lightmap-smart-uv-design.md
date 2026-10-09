# Bake light-map unwrap: Smart UV Project, and a thin-atlas guard

Status: approved by Chris 2026-10-09. Amends mm-tuneshroom
`docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md`
section 5.3 step 2 (wording in *Spec amendment* below; Chris applies it in
mm-tuneshroom). Tool: `tools/bake_model.py`, `tools/model_bake_helpers.py`,
`tools/blender_probe.py`.

## Problem

The first bake of a real model (a test Tower: Sophia's 2026-10-05 export
minus its decorative fungus growths, 35 meshes, 188,328 faces) finished all
14 LEDs and then refused with "every light map is black". Every texel of
every map was exactly 0.

Root cause, measured on the bake host (Mycologist, Blender 4.5.14):

- `_lightmap_pack` runs `bpy.ops.uv.lightmap_pack` over all targets. It
  makes one island per face. At 188,328 faces the median face of a
  responder disc gets a UV area of 7.4e-9, which is 0.008 texels at the
  default 1024 px. No texel centre falls inside any face, so Cycles writes
  nothing.
- With the full scene present for shadows, the same disc unwrapped on its
  own bakes normally (Diffuse peak 6.6 on 3,138 texels). Light positions,
  materials, normals and occlusion were each ruled out separately.
- The committed fixture (`tests/fixtures/models/marker_fixture.glb`, a few
  hundred faces) bakes fine, which is why T2 never met this.

The artist guide's budget allows 300,000 triangles, so a correct re-export
would fail the same way. The unwrap has to change, not the model.

## Change 1: Smart UV Project for the shared atlas

`_lightmap_pack(targets)` becomes `_lightmap_unwrap(targets, resolution)`:
the same selection and the same `lightmap` UV set (set 1, all targets in
one edit-mode session, so one shared atlas), but

```python
bpy.ops.uv.smart_project(
    angle_limit=math.radians(66.0),       # Blender's own default
    margin_method="FRACTION",
    island_margin=lightmap_island_margin(resolution, BAKE_MARGIN_PX),
    area_weight=0.0,
    correct_aspect=True,
    scale_to_bounds=False)
```

Smart UV Project groups neighbouring faces into charts by angle and packs
the charts itself, so the atlas is spent on surface, not on per-face
margins. `lightmap_island_margin(resolution, BAKE_MARGIN_PX)` (helper)
is `2 * BAKE_MARGIN_PX / resolution`: islands sit at least two bake margins
apart, so the 4 px dilation never bleeds one island into another.

Measured on the test Tower with Smart UV Project: unwrap 0.4 s, 492k of the
1,048k texels used, the thinnest mesh 69 texels, the responder disc's
Diffuse peak 8.3 on 5,596 texels. No separate `pack_islands` call: Smart UV
Project already packs, and a second pack measured as a no-op.

Every property name above was read from the bake host's own
`bpy.ops.uv.smart_project` RNA, not assumed.

## Change 2: refuse a thin atlas before baking

A bake takes minutes per LED, and this failure only showed after all of
them. After the unwrap, the script measures each target's light-map area in
texels and refuses before the first LED when any target falls below
`MIN_LIGHTMAP_TEXELS = 16`:

> bake_model: mesh 'Mesh_39' gets 3 light-map texels at 1024 px (minimum
> 16): simplify or drop small parts, or raise --resolution

Helpers in `tools/model_bake_helpers.py` (pure Python, pytest-tested):

- `uv_polygon_area(points) -> float`: shoelace area of one UV polygon.
- `lightmap_texels(polygons, resolution) -> float`: a mesh's summed UV
  polygon area times `resolution ** 2`.
- `refuse_thin_lightmap(coverage, resolution, minimum)`: raises `BakeError`
  naming the thinnest mesh and its texel count; `coverage` maps mesh name
  to texels.
- `lightmap_island_margin(resolution, margin_px) -> float`.

The bpy side only gathers each target's UV polygons and calls them. 16
texels is a floor for "this part gets any light map at all", not a quality
bar: the test Tower's thinnest part has 69.

## Change 3: the probe follows the bake

`tools/blender_probe.py` records `op_smart_project` (RNA properties) in
place of `op_lightmap_pack`, and its packed cube uses Smart UV Project, so
the export round-trip it reports is the one the bake does. Run it on the
bake host before the first bake with the new code.

## Spec amendment (mm-tuneshroom, section 5.3 step 2)

Replace "Lightmap-pack **all non-marker meshes together into one shared
atlas**" with: "Unwrap **all non-marker meshes together into one shared
atlas** with Smart UV Project (angle limit 66 degrees, islands two bake
margins apart), and refuse the bake before the first LED if any mesh gets
fewer than 16 atlas texels. (Amended 2026-10-09: Lightmap Pack's
one-island-per-face layout left a 188k-face model with 0.008 texels per
face.)"

## Verification

1. pytest for the helpers (area of known polygons, texel scaling, the
   margin, refusal naming the thinnest mesh, passing at the floor).
2. Bake host: re-run the probe; re-bake `marker_fixture.glb` at `--resolution 256`
   (what `tests/test_model_fixture.py` pins) and commit the new
   `marker_fixture.baked.glb` (it must stay valid and non-black, and it
   records what the current tool produces); bake the test Tower and check
   every LED's map is non-black.
3. `docs/MM_TERRARIUM.md`, *LED layout models*: the bake paragraph names
   Smart UV Project and the thin-atlas refusal.

## Out of scope

Bake resolution and sample defaults, the 8-bit encoding question (linear
maps crush far texels), and exporting the test Tower (it follows once a
bake succeeds, on its own local branch).
