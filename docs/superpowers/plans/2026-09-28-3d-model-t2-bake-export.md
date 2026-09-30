# 3D Tuneshroom Model, PR T2 (bake, export, artist guide) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Supersedes** Tasks 13-16 of `docs/superpowers/plans/2026-09-28-3d-model-terrarium.md` (mm-tuneshroom repo). That plan was written before PR T1 (mm-terrarium PR #162) merged; every task here was re-checked against the merged code and against mm-tuneshroom PR #32's Dart reader (`lib/render/model_layout.dart`, `test/models_export_test.dart`).

**Goal:** Ship `tools/bake_model.py` (headless Blender light-map bake), `tools/export_models.py` (hands fresh bakes to mm-tuneshroom), and `docs/instrument-model-guide.md`, with the spec's T2 gate met: a real bake of `tests/fixtures/models/marker_fixture.glb` on **Mycologist** with the pinned Blender, committed and guarded by a test.

**Architecture:** Everything that does not need `bpy` is pure stdlib and pytest-tested: the one layout serializer (`control/model_layout.py`), the one §4.1 contract validator and writer (`tools/model_bake_helpers.py`), the bake numerics (grouping, row flip, normalisation, RGBA8 quantisation), and `tools/export_models.py`. Two thin `bpy` scripts run only inside Blender: `tools/blender_probe.py` (reports the installed Blender's real API surface, run before the bake script is finalised) and `tools/bake_model.py`. Claude runs both on Mycologist as the unprivileged `claude-ops` user, shipping the source in with `git archive` over `portal ssh` stdin and bringing the bake back as base64.

**Tech Stack:** Python 3 stdlib (`struct`, `json`, `zlib`, `hashlib`, `math`, `re`, `tomllib`) in `control/` and every tested `tools/` module; Blender 4.5 LTS (bundled Python 3.11 + numpy) for the two `bpy` scripts only. Tests: `.venv/bin/python -m pytest tests -q`.

**Spec:** `docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md` in mm-tuneshroom (branch `claude/web-3d-tuneshroom-simulator-13eecf`, PR #32; on this host in `/Users/chris/projects/mm-tuneshroom/.claude/worktrees/laughing-golick-4b85eb/`). Binding sections: 2 (D13), 3, 4, 4.1, 5.3, 5.4, 6.4, 9. The amendments this plan needs are listed below; Claude does not edit the spec (another repo), Chris applies them.

## Decisions taken with Chris (2026-09-28)

| # | Decision | Why |
|---|---|---|
| P1 | **Bake host: Mycologist**, Blender **4.5 LTS** (`blender-4.5.14-macos-x64.dmg`), installed natively in `/Applications`. | Chris's call. Probe (claude-ops, 2026-09-28): macOS 13.7.8, **Intel** i5-7360U 2C/4T, 16 GB, Iris Plus 640, 86 GB free, no Blender. Blender 5.x ships no Intel macOS build; 4.5 LTS is the last and is maintained to July 2027. Cycles has no GPU backend for Intel iGPUs, so bakes are CPU-only. |
| P2 | **Claude runs every bake as `claude-ops`** (Chris's go-ahead per run); Chris's only fleet step is the one-time Blender install. | Unprivileged, no git credentials on the host, fewest manual steps. |
| P3 | Material-less meshes get a **default translucent** Principled BSDF (`mm_bake_default_translucent`, Transmission Weight 1.0). | The fixture body has no material and its LEDs sit inside it; an opaque default would bake black. Lets the gate require light to actually reach the surface. |
| P4 | **No denoise in T2**; noise is controlled by `--samples`. | Cycles bakes are never denoised (render denoising does not apply). A compositor pass waits for the real model to show it is needed. |
| P5 | The real fixture bake is **committed** at `tests/fixtures/models/marker_fixture.baked.glb` (256 px) with a guarding test; it is **not** exported to mm-tuneshroom. | Durable gate evidence without changing PR A's fixture contract. |

## Global Constraints

- **Baked-file contract (spec §4.1), enforced by `validate_mm_bake`:** root `extras.mm_bake` has exactly the keys `source_sha256` (64 lowercase hex), `pixels` (int >= 1, not bool), `map_scale` (finite number > 0), `maps` (list of `(pixels + 3) // 4` non-negative ints; LED i -> `textures[maps[i // 4]]`, channel `i % 4`), `uv` (`"TEXCOORD_1"`), `resolution` (int >= 1), `blender` (non-empty str), `layout` (list of `pixels` objects `{index, x_mm, y_mm, z_mm, size, zone}`, `index == position`, x/y/z ints, `size` in `small|medium|large`, `zone` null or `^[a-z0-9_]+$` and not `primary`). Every non-marker mesh primitive carries `TEXCOORD_1`; no node named `^LED_\d{3}$` has a mesh; every `maps` entry names a texture whose image is `image/png`.
- **One serializer:** `control.model_layout.layout_to_json(pixels)` is the only code that turns `PixelLayout`s into the `layout` list, key order `index, x_mm, y_mm, z_mm, size, zone`. The fixture generator, the bake, `export_models`, and tests all call it.
- **One writer:** `tools.model_bake_helpers.inject_bake` is the only code that writes images/textures/root `extras.mm_bake`, and it refuses (InjectBakeError) any output that fails `validate_baked_glb`.
- **Committed fixtures stay byte-identical:** `tests/fixtures/models/{marker_fixture.glb, marker_fixture.mmbake.glb, expected_layout.json}` must not change (mm-tuneshroom PR A copies them and expects `export_models` to overwrite them byte-identically). `tests/test_model_fixture.py::test_committed_fixtures_match_regeneration` guards this; it must pass after every task. Amended during execution (ledger Ruling 7, commit ba580ab): the shared test box was wound inside-out, so the three fixtures were regenerated once with outward winding (LED positions unchanged); mm-tuneshroom's copies are refreshed by the next `export_models` run.
- **Bake output path:** `<source dir>/<source stem>.baked.glb` (what `control/terrarium_config.py::_warn_if_bake_stale` reads).
- **Blender pin:** `PINNED_BLENDER = "4.5"` in `tools/bake_model.py`; refuse any other major.minor; `mm_bake.blender` records the full `X.Y.Z` from `bpy.app.version` (never `bpy.app.version_string`, which carries a suffix like ` LTS`).
- **Layout source (D13):** the bake's layout comes only from `control.model_layout` run on the *source* bytes, never from Blender's imported scene. Blender scene metres = layout mm / 1000 on every axis (Blender's glTF importer maps glTF Y-up to Z-up exactly as the parser does).
- **Image row order:** Blender `Image.pixels` is bottom row first; PNG and glTF texture space are top row first. Every map is row-flipped before packing (`flip_rows`).
- **export_models target:** refuses an `out_repo` whose `pubspec.yaml` does not say `name: mm_shrooms_app`; writes `assets/models/<name>.<hash8>.baked.glb` + `assets/models/models.json` (`{"_provenance": {"tool": "export_models/1", "commit": <12-hex>}, "models": {<name>: {"file", "model_sha256", "baked_sha256"}}}`, `indent=2, sort_keys=True`); deletes only stale `*.baked.glb`; refuses (deleting nothing) if any other unlisted file sits in `assets/models/`; copies `marker_fixture.mmbake.glb` and `expected_layout.json` byte-identically into `test/fixtures/models/`; writes nothing at all if any instrument is refused.
- **Fleet actions:** only via the `portal` skill's standing research lane (`portal ssh mycologist '<cmd>'`), each run with Chris's go-ahead. Never `--operator`, never sudo. Work area on the host: `/Users/claude-ops/mm-bake/` (new, disposable, created by this plan). Bakes run under `nice -n 10` (mm-mycologist shares the box).
- **Host labels:** any command Chris runs himself is labelled **RUN ON: <HOST>** in bold caps; editor steps use `vim`.
- Use `.venv/bin/python`, never bare `python3` (see `docs/MM_TERRARIUM.md`, *Running it*). Offline `tools/` scripts may use `json.dumps` directly (the `wire_json` rule covers outbound wire JSON only).
- No em dashes in any prose written for Chris (docs, PR body, commit messages).

## Spec amendments to hand to Chris (mm-tuneshroom spec; not edited here)

1. **§1 / §9.1:** the bake host is Mycologist (Intel Mac) on Blender 4.5 LTS; Blender 5.x has no Intel macOS build. §9 item 1 is closed.
2. **§5.3 step 3:** replace "Denoise." with "Noise is controlled by `--samples` (default 128); Cycles bakes are not denoised, and a compositor denoise pass is deferred until a real model shows it is needed."
3. **§5.3 step 2:** add "A mesh with no material (or an empty slot) gets a default translucent Principled BSDF (Transmission Weight 1.0) so the bake has a surface to write; it is exported with the model." and "Meshes sharing mesh data are made single-user first, so the shared lightmap atlas has no overlapping islands. A source mesh with more than one UV set keeps only set 0; the lightmap is always set 1."
4. **§5.3 step 4:** add "Each map is row-flipped from Blender's bottom-up order to PNG's top-down order before packing; an all-black bake (every texel 0) is refused."
5. **§5.3 CLI:** `blender -b --factory-startup --python-exit-code 1 -P tools/bake_model.py -- <model.glb> [--resolution N] [--samples N] [--out PATH]`; the layout's pixel count is the source file's own marker count, and `pixels` is checked against the catalog at catalog load and by `export_models` (the bake is handed a model file, not an instrument; several instruments may share one).
6. **§5.4:** "`export_models` refuses a target that is not an mm-tuneshroom checkout (`pubspec.yaml` `name: mm_shrooms_app`), deletes only stale `*.baked.glb`, and refuses without deleting if any other unlisted file is in `assets/models/`; it writes nothing unless every declared model exports."

## Prerequisite outside this PR (carry-in 5)

mm-tuneshroom's startup currently awaits every bundled model before first paint (web = one HTTP fetch per bake). That must become lazy (load only the instrument in play) **before the first `export_models` run that carries a real model**. It is an mm-tuneshroom change, not part of T2; the first export from this PR carries `models: {}` (no instrument declares a model yet) and is unaffected.

## Operator prerequisite (ask Chris at the START of execution, so the install overlaps Tasks 1-4)

Chris installs Blender 4.5.14 on Mycologist once, from his own Mac over the operator lane (`portal ssh mycologist --operator`, lands as `mycologist`):

**RUN ON: MYCOLOGIST** (as `mycologist`)
```bash
cd /tmp && curl -fLO https://download.blender.org/release/Blender4.5/blender-4.5.14-macos-x64.dmg && curl -fLO https://download.blender.org/release/Blender4.5/blender-4.5.14.sha256 && grep 'macos-x64.dmg' blender-4.5.14.sha256 | shasum -a 256 -c - && hdiutil attach -nobrowse -readonly -mountpoint /tmp/blender-dmg blender-4.5.14-macos-x64.dmg && cp -R /tmp/blender-dmg/Blender.app /Applications/ && hdiutil detach /tmp/blender-dmg && rm blender-4.5.14-macos-x64.dmg blender-4.5.14.sha256
```

The download is about 340 MB. `shasum -c` must print `blender-4.5.14-macos-x64.dmg: OK` before anything is copied (the `&&` chain stops otherwise). Claude then verifies as `claude-ops` (Task 5 Step 3).

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `control/model_layout.py` | modify | + `layout_to_json`, + `parse_source_layout`, GLB container version must be 2 |
| `control/terrarium_config.py` | modify (docstring only) | "garbled" -> "missing or malformed" |
| `tools/generate_model_fixture.py` | modify | use `layout_to_json` (bytes unchanged) |
| `tools/model_bake_helpers.py` | modify | + `BakeContractError`, `validate_mm_bake`, `validate_baked_glb`; `inject_bake` enforces §4.1; + bake numerics (`BakeError`, `check_blender_version`, `group_leds_by_four`, `layout_to_blender_m`, `flip_rows`, `normalise_maps`, `quantise_group_rgba8`, `build_mm_bake_extras`, `bake_output_path`) |
| `tools/export_models.py` | create | export fresh bakes + fixture pair into an mm-tuneshroom checkout |
| `tools/blender_probe.py` | create | bpy-only: report the installed Blender's API surface and a two-UV export round trip |
| `tools/bake_model.py` | create | bpy-only thin bake script |
| `tests/test_model_layout.py`, `tests/test_model_bake_helpers.py`, `tests/test_model_fixture.py` | modify | new tests; T1 stubs updated to valid `mm_bake` |
| `tests/test_export_models.py` | create | export_models tests |
| `tests/fixtures/models/marker_fixture.baked.glb` | create | the gate's real bake (256 px) |
| `docs/instrument-model-guide.md` | create | artist guide (spec §3) |
| `docs/MM_TERRARIUM.md`, `docs/carried-instrument-schema.md` | modify | pipeline + Mycologist bake runbook |

Task order: 1 -> 2 -> 3 -> 4 are pure and independent of Blender; 5 needs the operator prerequisite; 6 needs 5's probe output; 7-8 close out.

---

### Task 1: One layout serializer, source-count parse, GLB version check

**Files:**
- Modify: `control/model_layout.py` (`read_glb_json` ~line 47; append two functions after `parse_model_layout`)
- Modify: `control/terrarium_config.py:442` (docstring word)
- Modify: `tools/generate_model_fixture.py` (`build_mmbake_fixture`, `build_fixture`; drop `import dataclasses`)
- Test: `tests/test_model_layout.py`

**Interfaces:**
- Produces: `layout_to_json(pixels: Iterable[PixelLayout]) -> list[dict]` (keys in order `index, x_mm, y_mm, z_mm, size, zone`); `parse_source_layout(data: bytes, *, path: str) -> ModelLayout` (pixel count = the file's own marker count; raises `ModelLayoutError`). Used by Tasks 3, 4, 6.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_model_layout.py`)

```python
from pathlib import Path

from control.model_layout import layout_to_json, parse_model_layout, parse_source_layout
from tests.glb_builder import GlbBuilder

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "models"


def test_read_glb_json_refuses_a_container_version_other_than_2():
    body = b'{"nodes": []}   '  # 13 bytes of JSON + 3 spaces = 16, 4-aligned
    data = (struct.pack("<III", GLB_MAGIC, 1, 12 + 8 + len(body))
            + struct.pack("<II", len(body), JSON_CHUNK_TYPE) + body)
    with pytest.raises(ModelLayoutError, match="version 1"):
        read_glb_json(data, path="t.glb")


def test_layout_to_json_is_the_index_bearing_shape_in_key_order():
    layout = parse_model_layout((_FIXTURES / "marker_fixture.glb").read_bytes(),
                                path="marker_fixture.glb", pixel_count=12)
    out = layout_to_json(layout.pixels)
    assert [list(entry) for entry in out] == [
        ["index", "x_mm", "y_mm", "z_mm", "size", "zone"]] * 12
    assert out[0] == {"index": 0, "x_mm": 40, "y_mm": 0, "z_mm": 100,
                      "size": "medium", "zone": "ring"}
    assert [e["index"] for e in out] == list(range(12))


def test_layout_to_json_matches_the_committed_expected_layout():
    layout = parse_model_layout((_FIXTURES / "marker_fixture.glb").read_bytes(),
                                path="marker_fixture.glb", pixel_count=12)
    expected = json.loads((_FIXTURES / "expected_layout.json").read_text())
    assert layout_to_json(layout.pixels) == expected["pixels"]


def test_parse_source_layout_counts_the_files_own_markers():
    data = (_FIXTURES / "marker_fixture.glb").read_bytes()
    assert parse_source_layout(data, path="m.glb") == parse_model_layout(
        data, path="m.glb", pixel_count=12)


def test_parse_source_layout_refuses_a_file_with_no_markers():
    data = GlbBuilder().build([{"name": "LEDs"}])
    with pytest.raises(ModelLayoutError, match="no LED markers"):
        parse_source_layout(data, path="empty.glb")


def test_parse_source_layout_still_refuses_one_based_numbering():
    builder = GlbBuilder()
    nodes = [{"name": "LEDs", "children": [1, 2]}]
    for i in (1, 2):
        nodes.append({"name": f"LED_{i:03d}",
                      "mesh": builder.add_box_mesh((0.0, 0.0, i / 100), 0.002)})
    with pytest.raises(ModelLayoutError, match="missing"):
        parse_source_layout(builder.build(nodes), path="one_based.glb")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_model_layout.py -q -k "version_other or layout_to_json or parse_source_layout"`
Expected: FAIL (ImportError: `layout_to_json`, `parse_source_layout`).

- [ ] **Step 3: Implement**

In `read_glb_json`, rename `_version` to `version` and add right after the magic check:

```python
    if version != 2:
        raise ModelLayoutError(
            path=path, message=f"unsupported GLB container version {version} (must be 2)")
```

Append after `_parse_model_layout_body`:

```python
def layout_to_json(pixels) -> list:
    """The one serializer for a layout: PixelLayouts (ordered by index)
    to the list of {index, x_mm, y_mm, z_mm, size, zone} dicts that
    extras.mm_bake.layout carries (spec section 4.1) and that
    mm-tuneshroom's reader requires, `index` included. Used by the bake,
    the fixture generator and export_models' layout-vs-catalog check, so
    the three can never disagree about the shape."""
    return [{"index": p.index, "x_mm": p.x_mm, "y_mm": p.y_mm,
             "z_mm": p.z_mm, "size": p.size, "zone": p.zone}
            for p in pixels]


def parse_source_layout(data: bytes, *, path: str) -> ModelLayout:
    """parse_model_layout with the pixel count taken from the file's own
    LED marker count. For tools/bake_model.py, which is handed a model
    file, not an instrument (several instruments may share one file).
    The catalog's `pixels` stays the authority: catalog load and
    tools/export_models.py both compare against it. Index rules still
    apply, so 1-based or gapped numbering is refused."""
    gltf = read_glb_json(data, path=path)
    if not isinstance(gltf, dict):
        raise ModelLayoutError(path=path, message="glTF root must be a JSON object")
    try:
        nodes = gltf.get("nodes", [])
        leds_idx = _find_leds_node(nodes, path)
        markers = _collect_markers(nodes, leds_idx, _build_parent_map(nodes), path)
    except ModelLayoutError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ModelLayoutError(path=path, message=f"malformed glTF: {exc}") from exc
    if not markers:
        raise ModelLayoutError(path=path, message="no LED markers found under 'LEDs'")
    return parse_model_layout(data, path=path, pixel_count=len(markers))
```

In `tools/generate_model_fixture.py`: `from control.model_layout import layout_to_json, parse_model_layout`; in `build_mmbake_fixture` replace `layout = [dataclasses.asdict(p) for p in layout_pixels]` with `layout = layout_to_json(layout_pixels)`; in `build_fixture` replace `"pixels": [dataclasses.asdict(p) for p in layout.pixels]` with `"pixels": layout_to_json(layout.pixels)`; delete `import dataclasses`.

In `control/terrarium_config.py` `_warn_if_bake_stale`'s docstring, change "a garbled or unreadable bake" to "a missing or malformed bake".

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_model_layout.py tests/test_model_fixture.py tests/test_catalog.py -q`
Expected: PASS, including `test_committed_fixtures_match_regeneration` (bytes unchanged: same keys, same order, same values).

- [ ] **Step 5: Commit**

```bash
git add control/model_layout.py control/terrarium_config.py tools/generate_model_fixture.py tests/test_model_layout.py
git commit -m "feat(model_layout): one layout serializer, source-count parse, GLB version check"
```

---

### Task 2: The §4.1 contract validator; `inject_bake` enforces it

**Files:**
- Modify: `tools/model_bake_helpers.py`
- Test: `tests/test_model_bake_helpers.py`

**Interfaces:**
- Consumes: `control.model_layout.read_glb_json`, `ModelLayoutError`.
- Produces: `class BakeContractError(Exception)`; `class InjectBakeError(BakeContractError)` (was a bare `Exception`; existing callers unaffected); `MM_BAKE_KEYS: tuple`; `LIGHTMAP_TEXCOORD = "TEXCOORD_1"`; `validate_mm_bake(mm_bake) -> None` (raises `BakeContractError`); `validate_baked_glb(data: bytes, *, path: str) -> dict` (returns the validated `mm_bake`; raises `BakeContractError` prefixed with `path`); `inject_bake` unchanged signature, now raises `InjectBakeError` for any contract failure, including on its own output. Used by Tasks 3, 4, 6.

- [ ] **Step 1: Update the T1 tests that used invalid `mm_bake` stubs, and add the new tests**

In `tests/test_model_bake_helpers.py`, add near the top (after `_flat_png`):

```python
def _mm_bake(pixels: int) -> dict:
    """A valid mm_bake for `pixels` LEDs (inject_bake rewrites "maps")."""
    return {"source_sha256": "ab" * 32, "pixels": pixels, "map_scale": 1.0,
            "maps": [], "uv": "TEXCOORD_1", "resolution": 2, "blender": "test",
            "layout": [{"index": i, "x_mm": 0, "y_mm": 0, "z_mm": i,
                        "size": "medium", "zone": None} for i in range(pixels)]}


def _pngs(pixels: int) -> list:
    return [_flat_png(2, 2, (i, i, i, 255)) for i in range((pixels + 3) // 4)]
```

Then change these existing tests (the others pass unchanged because their structural refusals fire before any `mm_bake` check):

- `test_inject_bake_adds_images_textures_sampler_and_root_extras`: unchanged (its stub is already a valid `mm_bake`; `inject_bake` supplies `maps`).
- **Delete** `test_inject_bake_adds_no_sampler_when_there_are_no_pngs` (unreachable now: `pixels >= 1` requires at least one PNG) and add in its place:

```python
def test_inject_bake_refuses_a_png_count_that_does_not_match_pixels():
    with pytest.raises(InjectBakeError, match="maps has 0 entries, expected 1"):
        inject_bake(_bakeable_glb_bytes(), [], _mm_bake(1))
```

- `test_inject_bake_orders_maps_by_png_order`: `out = inject_bake(_bakeable_glb_bytes(), pngs, _mm_bake(12))`.
- `test_inject_bake_output_is_itself_a_valid_glb_with_a_larger_buffer`: `out = inject_bake(before, [png], _mm_bake(4))`.
- `test_inject_bake_preserves_existing_keys_on_buffers_0`: `out = inject_bake(glb_with_custom, [png], _mm_bake(1))`.
- **Replace** `test_inject_bake_empty_pngs_no_buffers_does_not_create_buffers_key` (unreachable now) with:

```python
def test_inject_bake_creates_buffer_0_when_the_input_declares_none():
    from tools.model_bake_helpers import _read_glb_full, _write_glb
    gltf, binary = _read_glb_full(_bakeable_glb_bytes(), path="t.glb")
    del gltf["buffers"]
    out = inject_bake(_write_glb(gltf, binary), _pngs(1), _mm_bake(1))
    result = read_glb_json(out, path="out.glb")
    assert len(result["buffers"]) == 1
    assert result["buffers"][0]["byteLength"] > len(binary)
```

Append the new tests:

```python
import copy
from pathlib import Path

from tools.model_bake_helpers import (
    BakeContractError, _read_glb_full, _write_glb, validate_baked_glb, validate_mm_bake,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "models"


def _valid_bake() -> dict:
    return {**_mm_bake(5), "maps": [0, 1]}


def test_validate_mm_bake_accepts_a_valid_block():
    validate_mm_bake(_valid_bake())


@pytest.mark.parametrize("mutate, match", [
    (lambda b: b.pop("uv"), "missing required key"),
    (lambda b: b.update(source_sha256="AB" * 32), "source_sha256"),
    (lambda b: b.update(pixels=0), "pixels"),
    (lambda b: b.update(pixels=True), "pixels"),
    (lambda b: b.update(map_scale=float("nan")), "map_scale"),
    (lambda b: b.update(map_scale=0), "map_scale"),
    (lambda b: b.update(uv="TEXCOORD_0"), "uv"),
    (lambda b: b.update(resolution=0), "resolution"),
    (lambda b: b.update(blender=""), "blender"),
    (lambda b: b.update(maps=[0]), "maps has 1 entries, expected 2"),
    (lambda b: b.update(maps=[0, -1]), "non-negative"),
    (lambda b: b["layout"].pop(), "layout must be a list of 5"),
    (lambda b: b["layout"][2].update(index=3), r"layout\[2\]\.index"),
    (lambda b: b["layout"][0].update(x_mm=1.5), "x_mm"),
    (lambda b: b["layout"][0].update(size="huge"), "size"),
    (lambda b: b["layout"][0].update(zone="Ring"), "zone"),
    (lambda b: b["layout"][0].update(zone="primary"), "primary"),
    (lambda b: b["layout"][0].pop("zone"), "zone"),
])
def test_validate_mm_bake_refuses_each_contract_violation(mutate, match):
    bake = copy.deepcopy(_valid_bake())
    mutate(bake)
    with pytest.raises(BakeContractError, match=match):
        validate_mm_bake(bake)


def test_validate_baked_glb_accepts_the_committed_mmbake_fixture():
    data = (_FIXTURES / "marker_fixture.mmbake.glb").read_bytes()
    mm_bake = validate_baked_glb(data, path="marker_fixture.mmbake.glb")
    assert mm_bake["pixels"] == 12 and mm_bake["maps"] == [0, 1, 2]


def test_validate_baked_glb_refuses_the_source_fixture():
    data = (_FIXTURES / "marker_fixture.glb").read_bytes()
    with pytest.raises(BakeContractError, match="marker_fixture.glb"):
        validate_baked_glb(data, path="marker_fixture.glb")


def test_validate_baked_glb_refuses_a_map_naming_no_texture():
    gltf, binary = _read_glb_full(
        (_FIXTURES / "marker_fixture.mmbake.glb").read_bytes(), path="f.glb")
    gltf["extras"]["mm_bake"]["maps"] = [0, 1, 7]
    with pytest.raises(BakeContractError, match=r"maps\[2\] = 7 names no texture"):
        validate_baked_glb(_write_glb(gltf, binary), path="f.glb")


def test_validate_baked_glb_refuses_a_file_without_mm_bake():
    with pytest.raises(BakeContractError, match="no root extras.mm_bake"):
        validate_baked_glb(_bakeable_glb_bytes(), path="plain.glb")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_model_bake_helpers.py -q`
Expected: FAIL (ImportError: `BakeContractError`, `validate_mm_bake`, ...).

- [ ] **Step 3: Implement** (in `tools/model_bake_helpers.py`)

Add `import json` and `import math` at the top (and drop the local `import json` inside `_write_glb`); import `ModelLayoutError` alongside `read_glb_json`. Replace the exception class and add the validators:

```python
class BakeContractError(Exception):
    """A baked .glb (or an mm_bake block) breaks spec section 4.1."""


class InjectBakeError(BakeContractError):
    """inject_bake refused its input or its own output."""


LIGHTMAP_TEXCOORD = "TEXCOORD_1"
MM_BAKE_KEYS = ("source_sha256", "pixels", "map_scale", "maps", "uv",
                "resolution", "blender", "layout")
_LAYOUT_KEYS = ("index", "x_mm", "y_mm", "z_mm", "size", "zone")
_SIZES = ("small", "medium", "large")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ZONE_RE = re.compile(r"^[a-z0-9_]+$")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_mm_bake(mm_bake) -> None:
    """Spec section 4.1's root extras.mm_bake, checked field by field with
    the same rules mm-tuneshroom's lib/render/model_layout.dart applies
    (plus uv/resolution/blender, which the spec lists and this writer
    always sets). Raises BakeContractError naming the field and rule."""
    if not isinstance(mm_bake, dict):
        raise BakeContractError("mm_bake must be an object")
    missing = [k for k in MM_BAKE_KEYS if k not in mm_bake]
    if missing:
        raise BakeContractError(f"mm_bake is missing required key(s) {missing}")
    sha = mm_bake["source_sha256"]
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise BakeContractError("mm_bake.source_sha256 must be 64 lowercase hex characters")
    pixels = mm_bake["pixels"]
    if not _is_int(pixels) or pixels < 1:
        raise BakeContractError("mm_bake.pixels must be an integer >= 1")
    scale = mm_bake["map_scale"]
    if (not isinstance(scale, (int, float)) or isinstance(scale, bool)
            or not math.isfinite(scale) or scale <= 0):
        raise BakeContractError("mm_bake.map_scale must be a finite number > 0")
    if mm_bake["uv"] != LIGHTMAP_TEXCOORD:
        raise BakeContractError(f"mm_bake.uv must be {LIGHTMAP_TEXCOORD!r}")
    if not _is_int(mm_bake["resolution"]) or mm_bake["resolution"] < 1:
        raise BakeContractError("mm_bake.resolution must be an integer >= 1")
    if not isinstance(mm_bake["blender"], str) or not mm_bake["blender"]:
        raise BakeContractError("mm_bake.blender must be a non-empty string")
    maps = mm_bake["maps"]
    if not isinstance(maps, list) or not all(_is_int(m) and m >= 0 for m in maps):
        raise BakeContractError("mm_bake.maps must be a list of non-negative integers")
    expected_maps = (pixels + 3) // 4
    if len(maps) != expected_maps:
        raise BakeContractError(
            f"mm_bake.maps has {len(maps)} entries, expected {expected_maps} "
            f"(one per group of 4 pixels)")
    layout = mm_bake["layout"]
    if not isinstance(layout, list) or len(layout) != pixels:
        raise BakeContractError(
            f"mm_bake.layout must be a list of {pixels} entries (mm_bake.pixels)")
    for i, entry in enumerate(layout):
        if not isinstance(entry, dict):
            raise BakeContractError(f"mm_bake.layout[{i}] must be an object")
        absent = [k for k in _LAYOUT_KEYS if k not in entry]
        if absent:
            raise BakeContractError(f"mm_bake.layout[{i}] is missing key(s) {absent}")
        if not _is_int(entry["index"]) or entry["index"] != i:
            raise BakeContractError(
                f"mm_bake.layout[{i}].index must be {i} (indices run 0 to "
                f"{pixels - 1} in order), got {entry['index']!r}")
        if not all(_is_int(entry[k]) for k in ("x_mm", "y_mm", "z_mm")):
            raise BakeContractError(f"mm_bake.layout[{i}].x_mm/y_mm/z_mm must be integers")
        if entry["size"] not in _SIZES:
            raise BakeContractError(
                f"mm_bake.layout[{i}].size must be one of small, medium, large, "
                f"got {entry['size']!r}")
        zone = entry["zone"]
        if zone is not None:
            if not isinstance(zone, str) or not _ZONE_RE.match(zone):
                raise BakeContractError(
                    f"mm_bake.layout[{i}].zone {zone!r} must match [a-z0-9_]+")
            if zone == "primary":
                raise BakeContractError(f"mm_bake.layout[{i}].zone 'primary' is reserved")


def validate_baked_glb(data: bytes, *, path: str) -> dict:
    """The whole spec section 4.1 contract on a baked file's bytes: no
    LED marker meshes, TEXCOORD_1 on every primitive, a valid root
    extras.mm_bake, and every maps entry naming a PNG texture. Returns
    the mm_bake block. Used by inject_bake (on its own output),
    tools/export_models.py, and the committed-bake test."""
    try:
        gltf = read_glb_json(data, path=path)
    except ModelLayoutError as exc:
        raise BakeContractError(str(exc)) from exc
    try:
        if not isinstance(gltf, dict):
            raise BakeContractError("glTF root must be a JSON object")
        _refuse_remaining_marker_meshes(gltf)
        _refuse_missing_texcoord1(gltf)
        extras = gltf.get("extras")
        mm_bake = extras.get("mm_bake") if isinstance(extras, dict) else None
        if mm_bake is None:
            raise BakeContractError("no root extras.mm_bake; this is not a baked model")
        validate_mm_bake(mm_bake)
        textures = gltf.get("textures", [])
        images = gltf.get("images", [])
        for group, tex in enumerate(mm_bake["maps"]):
            if tex >= len(textures):
                raise BakeContractError(
                    f"mm_bake.maps[{group}] = {tex} names no texture "
                    f"(the file has {len(textures)})")
            src = textures[tex].get("source")
            if (not _is_int(src) or not 0 <= src < len(images)
                    or images[src].get("mimeType") != "image/png"):
                raise BakeContractError(
                    f"mm_bake.maps[{group}]: texture {tex} is not a PNG image")
    except BakeContractError as exc:
        raise BakeContractError(f"{path}: {exc}") from exc
    except (IndexError, KeyError, TypeError, AttributeError) as exc:
        raise BakeContractError(f"{path}: malformed glTF: {exc}") from exc
    return mm_bake
```

Change `_refuse_missing_texcoord1`, `_refuse_remaining_marker_meshes` and `_validate_buffers_structure` to raise `BakeContractError` (their messages stay the same; `_refuse_missing_texcoord1`'s message should name `LIGHTMAP_TEXCOORD`). In `_write_glb`, use `json.dumps(gltf, allow_nan=False)`.

Rewrite `inject_bake`'s body so every contract check is converted to `InjectBakeError` and its own output is re-validated:

```python
def inject_bake(glb_bytes: bytes, pngs: list, mm_bake: dict) -> bytes:
    """... (keep the existing docstring; append:) The final mm_bake
    (with "maps" rewritten) must pass validate_mm_bake, so len(pngs)
    must equal ceil(pixels / 4), and the written file must pass
    validate_baked_glb; any failure raises InjectBakeError."""
    try:
        gltf, binary = _read_glb_full(glb_bytes, path="<inject_bake input>")
        _refuse_remaining_marker_meshes(gltf)
        _refuse_missing_texcoord1(gltf)
        _validate_buffers_structure(gltf)
        out = _append_maps(gltf, bytearray(binary), pngs, mm_bake)
        validate_baked_glb(out, path="<inject_bake output>")
    except InjectBakeError:
        raise
    except (BakeContractError, ModelLayoutError) as exc:
        raise InjectBakeError(str(exc)) from exc
    return out
```

Move the existing append/sampler/buffers/extras code, unchanged, into `_append_maps(gltf, binary, pngs, mm_bake) -> bytes`, adding one line right before `root_extras = ...`: `validate_mm_bake(extras)`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_model_bake_helpers.py tests/test_model_fixture.py -q`
Expected: PASS (the regenerated mmbake fixture is byte-identical: `allow_nan=False` changes no output for finite values).

- [ ] **Step 5: Commit**

```bash
git add tools/model_bake_helpers.py tests/test_model_bake_helpers.py
git commit -m "feat(bake): validate the section 4.1 contract in inject_bake, the single writer"
```

---

### Task 3: Bake numerics, testable without bpy

**Files:**
- Modify: `tools/model_bake_helpers.py` (append)
- Test: `tests/test_model_bake_helpers.py` (append)

**Interfaces:**
- Consumes: `control.model_layout.layout_to_json`, `PixelLayout`; `validate_mm_bake` (Task 2).
- Produces (all used by Task 6's `tools/bake_model.py`):
  - `class BakeError(Exception)`
  - `check_blender_version(actual: tuple, pinned: str) -> str` returns `"X.Y.Z"`
  - `group_leds_by_four(pixel_count: int) -> list[list[int]]`
  - `layout_to_blender_m(pixel: PixelLayout) -> tuple[float, float, float]`
  - `flip_rows(values: list, width: int, height: int) -> list` (single channel)
  - `normalise_maps(raw_maps: dict[int, list[float]]) -> tuple[dict[int, list[float]], float]`
  - `quantise_group_rgba8(group: list[int], normalised: dict[int, list[float]], texel_count: int) -> bytes`
  - `build_mm_bake_extras(*, source_sha256: str, layout_pixels: tuple, map_scale: float, resolution: int, blender_version: str) -> dict`
  - `bake_output_path(model_path: Path) -> Path`

- [ ] **Step 1: Write the failing tests** (append)

```python
from control.model_layout import PixelLayout
from tools.model_bake_helpers import (
    BakeError, bake_output_path, build_mm_bake_extras, check_blender_version,
    flip_rows, group_leds_by_four, layout_to_blender_m, normalise_maps,
    quantise_group_rgba8,
)


def test_check_blender_version_accepts_the_pinned_major_minor_and_returns_xyz():
    assert check_blender_version((4, 5, 14), "4.5") == "4.5.14"


def test_check_blender_version_refuses_another_major_minor():
    with pytest.raises(BakeError, match=r"pinned to Blender 4\.5.*found 5\.2\.2"):
        check_blender_version((5, 2, 2), "4.5")


def test_group_leds_by_four():
    assert group_leds_by_four(12) == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]]
    assert group_leds_by_four(5) == [[0, 1, 2, 3], [4]]


def test_layout_to_blender_m_is_layout_mm_over_1000_on_every_axis():
    p = PixelLayout(index=0, x_mm=40, y_mm=-28, z_mm=100, size="medium", zone="ring")
    assert layout_to_blender_m(p) == (0.04, -0.028, 0.1)


def test_flip_rows_turns_blender_bottom_up_rows_into_top_down_rows():
    # 2 wide x 3 tall; Blender row 0 is the BOTTOM row.
    assert flip_rows([1, 2, 3, 4, 5, 6], 2, 3) == [5, 6, 3, 4, 1, 2]


def test_flip_rows_refuses_the_wrong_length():
    with pytest.raises(ValueError, match="width\\*height"):
        flip_rows([1, 2, 3], 2, 2)


def test_normalise_maps_scales_by_the_brightest_texel_across_all_leds():
    normalised, scale = normalise_maps({0: [0.5, 1.0], 1: [2.0, 0.0]})
    assert scale == 2.0
    assert normalised == {0: [0.25, 0.5], 1: [1.0, 0.0]}


def test_normalise_maps_clamps_negative_texels_to_zero():
    normalised, scale = normalise_maps({0: [-0.5, 1.0]})
    assert (normalised, scale) == ({0: [0.0, 1.0]}, 1.0)


def test_normalise_maps_refuses_an_all_black_bake():
    with pytest.raises(BakeError, match="black"):
        normalise_maps({0: [0.0, 0.0], 1: [0.0, 0.0]})


def test_quantise_group_rgba8_packs_leds_into_r_g_b_a_in_group_order():
    normalised = {4: [1.0, 0.0], 5: [0.5, 0.0], 6: [0.0, 1.0]}
    out = quantise_group_rgba8([4, 5, 6], normalised, texel_count=2)
    # texel 0: R=LED4 255, G=LED5 128, B=LED6 0, A=unused 0
    assert out == bytes([255, 128, 0, 0, 0, 0, 255, 0])


def test_quantise_group_rgba8_refuses_a_map_of_the_wrong_length():
    with pytest.raises(ValueError, match="LED 1"):
        quantise_group_rgba8([0, 1], {0: [0.0, 0.0], 1: [0.0]}, texel_count=2)


def test_build_mm_bake_extras_passes_the_contract_and_uses_the_one_serializer():
    layout = tuple(PixelLayout(index=i, x_mm=i, y_mm=0, z_mm=10, size="small",
                               zone=None) for i in range(5))
    extras = build_mm_bake_extras(source_sha256="cd" * 32, layout_pixels=layout,
                                  map_scale=0.87, resolution=256,
                                  blender_version="4.5.14")
    validate_mm_bake(extras)
    assert extras["pixels"] == 5 and extras["maps"] == [0, 1]
    assert extras["uv"] == "TEXCOORD_1" and extras["blender"] == "4.5.14"
    assert extras["layout"][4] == {"index": 4, "x_mm": 4, "y_mm": 0, "z_mm": 10,
                                   "size": "small", "zone": None}


def test_bake_output_path_sits_beside_the_source():
    assert bake_output_path(Path("instruments/models/cap.glb")) == Path(
        "instruments/models/cap.baked.glb")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_model_bake_helpers.py -q`
Expected: FAIL (ImportError: `BakeError`, ...).

- [ ] **Step 3: Implement** (append to `tools/model_bake_helpers.py`; add `from pathlib import Path` and `from control.model_layout import layout_to_json` to the imports)

```python
class BakeError(Exception):
    """tools/bake_model.py refused to run or to write a result."""


def check_blender_version(actual: tuple, pinned: str) -> str:
    """`actual` is bpy.app.version, a (major, minor, patch) tuple (never
    parse bpy.app.version_string: it carries a suffix like " LTS").
    Refuses any major.minor other than `pinned` ("X.Y"); returns the
    full "X.Y.Z" recorded as mm_bake.blender."""
    found = ".".join(str(n) for n in actual[:3])
    want = tuple(int(part) for part in pinned.split(".")[:2])
    if tuple(actual[:2]) != want:
        raise BakeError(
            f"tools/bake_model.py is pinned to Blender {pinned}.x, found {found}; "
            f"bake on the host documented in docs/MM_TERRARIUM.md (LED layout "
            f"models), or re-run tools/blender_probe.py and update PINNED_BLENDER")
    return found


def group_leds_by_four(pixel_count: int) -> list:
    """LED index groups, one per RGBA texture: LEDs 4i..4i+3 go to
    R, G, B, A of texture i (spec section 5.3 step 4)."""
    return [list(range(i, min(i + 4, pixel_count))) for i in range(0, pixel_count, 4)]


def layout_to_blender_m(pixel) -> tuple:
    """A layout pixel's Blender scene position in metres. The layout is
    Z-up millimetres (D12) and Blender's glTF importer converts the
    source's Y-up to the same Z-up frame, so it is mm / 1000 per axis."""
    return (pixel.x_mm / 1000.0, pixel.y_mm / 1000.0, pixel.z_mm / 1000.0)


def flip_rows(values: list, width: int, height: int) -> list:
    """Reverse the row order of a single-channel, row-major buffer.
    Blender's Image.pixels is bottom row first; PNG rows and glTF
    texture space (UV (0,0) = top-left) are top row first, and Blender's
    glTF exporter writes v' = 1 - v, so an unflipped map would be upside
    down on the model."""
    if len(values) != width * height:
        raise ValueError(f"values must be width*height ({width * height}), got {len(values)}")
    rows = [values[r * width:(r + 1) * width] for r in range(height)]
    return [v for row in reversed(rows) for v in row]


def normalise_maps(raw_maps: dict) -> tuple:
    """Divide every LED's texels by the brightest texel across all LEDs
    (spec section 5.3 step 4); map_scale is that peak, so
    texel * map_scale recovers the baked value. Negative texels (bake
    noise) clamp to 0. An all-black bake is refused: it means no LED's
    light reached any baked surface, which is a modelling or material
    error, not a result."""
    clamped = {led: [t if t > 0.0 else 0.0 for t in texels]
               for led, texels in raw_maps.items()}
    peak = max((max(texels) for texels in clamped.values() if texels), default=0.0)
    if peak <= 0.0:
        raise BakeError(
            "every light map is black: no LED lit any baked surface; check the "
            "markers sit where the surface can see them and the materials "
            "are not fully opaque around them")
    return {led: [t / peak for t in texels] for led, texels in clamped.items()}, peak


def _to_byte(value: float) -> int:
    if value <= 0.0:
        return 0
    if value >= 1.0:
        return 255
    return int(value * 255.0 + 0.5)


def quantise_group_rgba8(group: list, normalised: dict, texel_count: int) -> bytes:
    """One texture's raw RGBA8 bytes (encode_png_rgba8's input): the LEDs
    of `group` in R, G, B, A order, unused channels of a short final
    group 0."""
    out = bytearray(texel_count * 4)
    for channel, led in enumerate(group):
        texels = normalised[led]
        if len(texels) != texel_count:
            raise ValueError(f"LED {led} map has {len(texels)} texels, expected {texel_count}")
        out[channel::4] = bytes(_to_byte(t) for t in texels)
    return bytes(out)


def build_mm_bake_extras(*, source_sha256: str, layout_pixels: tuple, map_scale: float,
                         resolution: int, blender_version: str) -> dict:
    """extras.mm_bake for a real bake (spec section 4.1). "maps" is a
    placeholder in the right shape; inject_bake rewrites it with the
    actual texture indices. Validated before it is returned."""
    extras = {
        "source_sha256": source_sha256,
        "pixels": len(layout_pixels),
        "map_scale": map_scale,
        "maps": list(range(len(group_leds_by_four(len(layout_pixels))))),
        "uv": LIGHTMAP_TEXCOORD,
        "resolution": resolution,
        "blender": blender_version,
        "layout": layout_to_json(layout_pixels),
    }
    validate_mm_bake(extras)
    return extras


def bake_output_path(model_path: Path) -> Path:
    """<source dir>/<source stem>.baked.glb, the file the catalog's
    stale-bake check (control/terrarium_config.py) looks for."""
    return model_path.with_name(f"{model_path.stem}.baked.glb")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_model_bake_helpers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/model_bake_helpers.py tests/test_model_bake_helpers.py
git commit -m "feat(bake): row flip, normalisation, RGBA8 packing and version pin, testable without bpy"
```

---

### Task 4: `tools/export_models.py`

**Files:**
- Create: `tools/export_models.py`
- Test: `tests/test_export_models.py`

**Interfaces:**
- Consumes: `control.catalog.load_catalog`; `control.model_layout.layout_to_json`; `tools.model_bake_helpers.validate_baked_glb`, `BakeContractError`, `bake_output_path`.
- Produces: `ExportModelsError`, `export_models(instruments_root: Path, out_repo: Path, *, commit: str) -> dict`, `main(argv: list | None = None) -> None`. Run as `.venv/bin/python -m tools.export_models <mm-tuneshroom checkout>`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export_models.py
"""tools/export_models.py against tmp catalogs seeded with the committed
fixture pair: marker_fixture.glb is a real 12-LED source and
marker_fixture.mmbake.glb is a contract-valid bake of it."""
import hashlib
import json
from pathlib import Path

import pytest

from tools.export_models import ExportModelsError, export_models, main
from tools.model_bake_helpers import _read_glb_full, _write_glb

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "models"
SOURCE = (FIXTURES / "marker_fixture.glb").read_bytes()
BAKE = (FIXTURES / "marker_fixture.mmbake.glb").read_bytes()
TOML = '''
description = "a test instrument"
pixels = 12
capabilities = ["light.pixels"]
accepted_cues = ["midi"]
'''


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mm-tuneshroom"
    (repo / "assets" / "models").mkdir(parents=True)
    (repo / "pubspec.yaml").write_text("name: mm_shrooms_app\n")
    return repo


def _seed(root: Path, name: str = "glowcap", *, model: str = "glowcap",
          bake: bytes | None = BAKE) -> None:
    (root / "models").mkdir(parents=True, exist_ok=True)
    (root / "models" / f"{model}.glb").write_bytes(SOURCE)
    if bake is not None:
        (root / "models" / f"{model}.baked.glb").write_bytes(bake)
    (root / f"{name}.toml").write_text(TOML + f'\nmodel = "models/{model}.glb"\n')


def _edited_bake(edit) -> bytes:
    gltf, binary = _read_glb_full(BAKE, path="b.glb")
    edit(gltf["extras"]["mm_bake"])
    return _write_glb(gltf, binary)


def test_exports_the_bake_models_json_and_provenance(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root)
    payload = export_models(root, repo, commit="abc123def456")
    sha = hashlib.sha256(BAKE).hexdigest()
    assert payload == {
        "_provenance": {"tool": "export_models/1", "commit": "abc123def456"},
        "models": {"glowcap": {"file": f"glowcap.{sha[:8]}.baked.glb",
                               "model_sha256": hashlib.sha256(SOURCE).hexdigest(),
                               "baked_sha256": sha}}}
    models_dir = repo / "assets" / "models"
    assert (models_dir / f"glowcap.{sha[:8]}.baked.glb").read_bytes() == BAKE
    written = (models_dir / "models.json").read_text()
    assert written == json.dumps(payload, indent=2, sort_keys=True) + "\n"


def test_two_instruments_sharing_one_model_get_one_entry_each(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, "cap_a", model="shared")
    (root / "cap_b.toml").write_text(TOML + '\nmodel = "models/shared.glb"\n')
    payload = export_models(root, repo, commit="c")
    assert sorted(payload["models"]) == ["cap_a", "cap_b"]
    assert payload["models"]["cap_a"]["baked_sha256"] == payload["models"]["cap_b"]["baked_sha256"]


def test_refuses_a_missing_bake(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, bake=None)
    with pytest.raises(ExportModelsError, match="no bake"):
        export_models(root, repo, commit="c")


def test_refuses_a_stale_bake_and_writes_nothing(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, bake=_edited_bake(lambda b: b.update(source_sha256="cd" * 32)))
    before = sorted(p.name for p in (repo / "assets" / "models").iterdir())
    with pytest.raises(ExportModelsError, match="stale"):
        export_models(root, repo, commit="c")
    assert sorted(p.name for p in (repo / "assets" / "models").iterdir()) == before
    assert not (repo / "test").exists()


def test_refuses_a_layout_that_differs_from_the_catalog(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, bake=_edited_bake(lambda b: b["layout"][0].update(x_mm=41)))
    with pytest.raises(ExportModelsError, match="layout"):
        export_models(root, repo, commit="c")


def test_refuses_a_bake_that_breaks_the_contract(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root, bake=_edited_bake(lambda b: b.pop("uv")))
    with pytest.raises(ExportModelsError, match="missing required key"):
        export_models(root, repo, commit="c")


def test_instruments_without_a_model_export_an_empty_map(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    root.mkdir()
    (root / "plain.toml").write_text(TOML)
    assert export_models(root, repo, commit="c")["models"] == {}


def test_a_draft_that_declares_a_model_is_not_exported(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root)
    (root / "drafts").mkdir()
    (root / "glowcap.toml").replace(root / "drafts" / "glowcap.toml")
    assert export_models(root, repo, commit="c")["models"] == {}


def test_prunes_stale_exported_bakes(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root)
    stale = repo / "assets" / "models" / "old.deadbeef.baked.glb"
    stale.write_bytes(b"old")
    export_models(root, repo, commit="c")
    assert not stale.exists()


def test_refuses_an_unexpected_file_and_deletes_nothing(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    _seed(root)
    stray = repo / "assets" / "models" / "notes.txt"
    stray.write_text("hi")
    stale = repo / "assets" / "models" / "old.deadbeef.baked.glb"
    stale.write_bytes(b"old")
    with pytest.raises(ExportModelsError, match="notes.txt"):
        export_models(root, repo, commit="c")
    assert stray.exists() and stale.exists()


def test_refuses_a_target_that_is_not_mm_tuneshroom(tmp_path):
    root = tmp_path / "instruments"
    root.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "pubspec.yaml").write_text("name: something_else\n")
    with pytest.raises(ExportModelsError, match="not an mm-tuneshroom checkout"):
        export_models(root, other, commit="c")


def test_copies_the_mmbake_fixture_pair_byte_identically_and_not_the_source(tmp_path):
    root, repo = tmp_path / "instruments", _repo(tmp_path)
    root.mkdir()
    export_models(root, repo, commit="c")
    dest = repo / "test" / "fixtures" / "models"
    assert (dest / "marker_fixture.mmbake.glb").read_bytes() == BAKE
    assert (dest / "expected_layout.json").read_bytes() == (
        FIXTURES / "expected_layout.json").read_bytes()
    assert not (dest / "marker_fixture.glb").exists()


def test_main_without_a_target_prints_usage():
    with pytest.raises(SystemExit, match="usage"):
        main([])
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_export_models.py -q`
Expected: FAIL (ModuleNotFoundError: `tools.export_models`).

- [ ] **Step 3: Implement**

```python
# tools/export_models.py
"""Exports every published instrument's fresh baked model into an
mm-tuneshroom checkout, and copies the shared fixture pair beside its
tests (spec: mm-tuneshroom docs/superpowers/specs/
2026-09-28-3d-tuneshroom-model-and-view-design.md section 5.4). A
sibling of tools/export_solo.py and tools/export_contract.py.

    .venv/bin/python -m tools.export_models /path/to/mm-tuneshroom

Run from an mm-terrarium checkout at origin/main; commit the result in
mm-tuneshroom in its own commit naming the provenance commit. Nothing is
written unless every declared model exports.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from control.catalog import load_catalog
from control.model_layout import layout_to_json
from control.terrarium_config import TerrariumConfigError
from tools.model_bake_helpers import BakeContractError, bake_output_path, validate_baked_glb

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_VERSION = "export_models/1"
APP_PACKAGE = "mm_shrooms_app"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "models"
FIXTURE_FILES = ("marker_fixture.mmbake.glb", "expected_layout.json")


class ExportModelsError(Exception):
    pass


def _head_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _check_target(out_repo: Path) -> None:
    try:
        text = (out_repo / "pubspec.yaml").read_text(encoding="utf-8")
    except OSError:
        text = ""
    if not re.search(rf"^name:\s*{APP_PACKAGE}\s*$", text, re.MULTILINE):
        raise ExportModelsError(
            f"{out_repo} is not an mm-tuneshroom checkout (no pubspec.yaml "
            f"with name: {APP_PACKAGE})")


def _model_path(instruments_root: Path, name: str) -> Path:
    """The instrument's declared model, resolved as the catalog resolves a
    published entry's (against instruments/). The catalog load that
    produced `name` has already confined and parsed this path."""
    raw = tomllib.loads((instruments_root / f"{name}.toml").read_text(encoding="utf-8"))
    return instruments_root / raw["model"]


def _stage(instruments_root: Path) -> tuple:
    """(entries, files): every published model's checked bake, or an
    ExportModelsError naming the first instrument that cannot export."""
    entries, files = {}, {}
    for name, inst in sorted(load_catalog(instruments_root).published.items()):
        if inst.model_sha256 is None:
            continue
        baked_path = bake_output_path(_model_path(instruments_root, name))
        if not baked_path.is_file():
            raise ExportModelsError(
                f"instrument {name!r} declares a model but has no bake at "
                f"{baked_path}; run tools/bake_model.py first")
        baked = baked_path.read_bytes()
        try:
            mm_bake = validate_baked_glb(baked, path=str(baked_path))
        except BakeContractError as exc:
            raise ExportModelsError(f"instrument {name!r}: {exc}") from exc
        if mm_bake["source_sha256"] != inst.model_sha256:
            raise ExportModelsError(
                f"instrument {name!r}: bake {baked_path} is stale (bake "
                f"source_sha256 {mm_bake['source_sha256']}, current model "
                f"{inst.model_sha256}); re-run tools/bake_model.py")
        if mm_bake["layout"] != layout_to_json(inst.layout):
            raise ExportModelsError(
                f"instrument {name!r}: bake {baked_path} carries a layout that "
                f"differs from the catalog's; re-run tools/bake_model.py on the "
                f"current source model")
        sha = hashlib.sha256(baked).hexdigest()
        filename = f"{name}.{sha[:8]}.baked.glb"
        files[filename] = baked
        entries[name] = {"file": filename, "model_sha256": inst.model_sha256,
                         "baked_sha256": sha}
    return entries, files


def export_models(instruments_root: Path, out_repo: Path, *, commit: str) -> dict:
    """Stage and check everything first, then write: assets/models/ gets
    the bakes and models.json, stale *.baked.glb files are removed, and
    the shared fixture pair (never the source fixture, spec section 6.4)
    is copied byte-identically into test/fixtures/models/. Returns the
    models.json payload."""
    _check_target(out_repo)
    entries, files = _stage(instruments_root)

    models_dir = out_repo / "assets" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    strays = sorted(p.name for p in models_dir.iterdir()
                    if p.is_file() and p.name != "models.json"
                    and not p.name.endswith(".baked.glb"))
    if strays:
        raise ExportModelsError(
            f"{models_dir} holds unexpected file(s) {strays}; remove them by hand "
            f"(export_models deletes only stale *.baked.glb files)")

    for old in models_dir.glob("*.baked.glb"):
        if old.name not in files:
            old.unlink()
    for filename, data in files.items():
        (models_dir / filename).write_bytes(data)
    payload = {"_provenance": {"tool": TOOL_VERSION, "commit": commit},
               "models": entries}
    (models_dir / "models.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    fixture_dest = out_repo / "test" / "fixtures" / "models"
    fixture_dest.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_FILES:
        shutil.copyfile(FIXTURE_DIR / name, fixture_dest / name)
    return payload


def main(argv: list | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: python -m tools.export_models <mm-tuneshroom checkout>")
    out_repo = Path(argv[0])
    try:
        payload = export_models(REPO_ROOT / "instruments", out_repo, commit=_head_commit())
    except (ExportModelsError, TerrariumConfigError) as exc:
        sys.exit(f"export_models: {exc}")
    print(f"wrote {out_repo / 'assets' / 'models' / 'models.json'} "
          f"({len(payload['models'])} model(s)) and the shared fixture pair")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_export_models.py -q`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/export_models.py tests/test_export_models.py
git commit -m "feat(export): tools/export_models.py hands fresh bakes to mm-tuneshroom"
```

---

### Task 5: `tools/blender_probe.py`, and the probe run on Mycologist

**Files:**
- Create: `tools/blender_probe.py`

**Interfaces:**
- Consumes: `control.model_layout.read_glb_json` (stdlib; importable from Blender's Python 3.11).
- Produces: a JSON report between `MM_PROBE_BEGIN` / `MM_PROBE_END` lines that Task 6 reconciles against, and the confirmed `bpy.app.version`.

This script is bpy-only and cannot run under pytest. Each section is wrapped so one failing call is reported, not fatal: the point is to learn the real API surface of the installed Blender (carry-in 3) rather than guess it.

- [ ] **Step 1: Write the probe**

```python
# tools/blender_probe.py
"""Reports what tools/bake_model.py relies on in the installed Blender,
so its bpy calls are fixed against facts, not guesses. Run on the bake
host (docs/MM_TERRARIUM.md, LED layout models):

    Blender -b --factory-startup --python-exit-code 1 -P tools/blender_probe.py

Prints one JSON object between MM_PROBE_BEGIN / MM_PROBE_END lines. Every
section catches its own exception and reports it, so one API change does
not hide the rest.
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.model_layout import read_glb_json  # noqa: E402


def _op_props(op) -> dict:
    out = {}
    for prop in op.get_rna_type().properties:
        if prop.identifier == "rna_type":
            continue
        entry = {"type": prop.type}
        if prop.type == "ENUM":
            entry["items"] = [item.identifier for item in prop.enum_items]
            entry["flag"] = prop.is_enum_flag
        out[prop.identifier] = entry
    return out


def _section(report: dict, key: str, fn) -> None:
    try:
        report[key] = fn()
    except Exception:  # noqa: BLE001 -- a probe reports, never stops
        report[key] = {"error": traceback.format_exc(limit=3)}


def _principled_inputs() -> list:
    mat = bpy.data.materials.new("probe_mat")
    mat.use_nodes = True
    return [i.name for i in mat.node_tree.nodes["Principled BSDF"].inputs]


def _cube_with_two_uvs():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=0.1)
    obj = bpy.context.active_object
    uvs = obj.data.uv_layers
    if len(uvs) == 0:
        uvs.new(name="UVMap")
    uvs.new(name="lightmap")
    return obj


def _lightmap_pack_and_export() -> dict:
    obj = _cube_with_two_uvs()
    uvs = obj.data.uv_layers
    uvs.active = uvs["lightmap"]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.lightmap_pack(PREF_CONTEXT="ALL_FACES", PREF_PACK_IN_ONE=True,
                             PREF_NEW_UVLAYER=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    lightmap_uvs = np.empty(len(obj.data.loops) * 2, dtype=np.float32)
    uvs["lightmap"].data.foreach_get("uv", lightmap_uvs)
    uvs.active = uvs[0]
    uvs[0].active_render = True
    mat = bpy.data.materials.new("probe_mat")
    mat.use_nodes = True
    obj.data.materials.append(mat)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.glb"
        bpy.ops.export_scene.gltf(
            filepath=str(path), export_format="GLB", export_yup=True,
            export_texcoords=True, export_normals=True, export_extras=False,
            export_lights=False, export_cameras=False)
        gltf = read_glb_json(path.read_bytes(), path=str(path))
    return {
        "lightmap_uv_range": [float(lightmap_uvs.min()), float(lightmap_uvs.max())],
        "primitive_attributes": [sorted(p["attributes"]) for m in gltf["meshes"]
                                 for p in m["primitives"]],
        "materials_exported": len(gltf.get("materials", [])),
        "images_exported": len(gltf.get("images", [])),
    }


def _mini_bake() -> dict:
    """A translucent cube lit by a point light at its centre: bakes
    DIFFUSE then TRANSMISSION (colour excluded) into one float image and
    reports each pass's peak, proving the bake call and pass_filter
    work headless and that light inside a translucent body reaches it."""
    obj = _cube_with_two_uvs()
    mat = bpy.data.materials.new("probe_translucent")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Transmission Weight"].default_value = 1.0
    obj.data.materials.append(mat)
    img = bpy.data.images.new("probe_bake", width=32, height=32, alpha=False,
                              float_buffer=True)
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = img
    node.select = True
    mat.node_tree.nodes.active = node
    light = bpy.data.lights.new("probe_led", type="POINT")
    light.energy = 1.0
    light.shadow_soft_size = 0.001
    led = bpy.data.objects.new("probe_led", light)
    bpy.context.scene.collection.objects.link(led)
    scene = bpy.context.scene
    world = bpy.data.worlds.new("probe_world")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    peaks = {}
    for kind in ("DIFFUSE", "TRANSMISSION"):
        bpy.ops.object.bake(type=kind, pass_filter={"DIRECT", "INDIRECT"},
                            use_clear=True, margin=2, target="IMAGE_TEXTURES")
        flat = np.empty(32 * 32 * 4, dtype=np.float32)
        img.pixels.foreach_get(flat)
        peaks[kind] = float(flat[0::4].max())
    return peaks


def main() -> None:
    report = {"version": list(bpy.app.version), "version_string": bpy.app.version_string,
              "python": sys.version.split()[0]}
    _section(report, "op_lightmap_pack", lambda: _op_props(bpy.ops.uv.lightmap_pack))
    _section(report, "op_bake", lambda: _op_props(bpy.ops.object.bake))
    _section(report, "op_export_gltf", lambda: _op_props(bpy.ops.export_scene.gltf))
    _section(report, "op_import_gltf", lambda: _op_props(bpy.ops.import_scene.gltf))
    _section(report, "op_make_single_user", lambda: _op_props(bpy.ops.object.make_single_user))
    _section(report, "principled_inputs", _principled_inputs)
    _section(report, "export_round_trip", _lightmap_pack_and_export)
    _section(report, "mini_bake", _mini_bake)
    print("MM_PROBE_BEGIN")
    print(json.dumps(report, indent=1, sort_keys=True))
    print("MM_PROBE_END")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit the probe**

```bash
git add tools/blender_probe.py
git commit -m "feat(bake): tools/blender_probe.py reports the installed Blender's API surface"
```

- [ ] **Step 3: Confirm the operator prerequisite is done** (the install block at the top of this plan). Ask Chris for the go-ahead for this task's fleet actions (the Step 4 ship-in and the Step 5 probe run), then verify the install as `claude-ops` via the `portal` skill:

```bash
portal ssh mycologist '/Applications/Blender.app/Contents/MacOS/Blender --factory-startup -b --version | head -1'
```

Expected: `Blender 4.5.14 LTS` (anything other than 4.5.x: stop, BLOCKED, tell Chris).

- [ ] **Step 4: Ship this commit's sources to the host** (no push, no GitHub access needed; `portal ssh` passes stdin through, verified 2026-09-28)

```bash
git archive --format=tar HEAD control tools tests/fixtures/models | portal ssh mycologist 'rm -rf ~/mm-bake/src && mkdir -p ~/mm-bake/src && tar -x -C ~/mm-bake/src'
git rev-parse --short=12 HEAD | portal ssh mycologist 'cat > ~/mm-bake/src/COMMIT'
```

- [ ] **Step 5: Run the probe and save its output**

```bash
portal ssh mycologist 'cd ~/mm-bake/src && nice -n 10 /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 -P tools/blender_probe.py 2>&1 | sed -n "/MM_PROBE_BEGIN/,/MM_PROBE_END/p"' > "$SCRATCH/blender_probe_4.5.json.txt"
```

(`$SCRATCH` = this session's scratchpad directory.) Record the report in the SDD ledger. **Decision points, each a hard stop (BLOCKED, ask Chris) if it fails:**
- `version[:2] == [4, 5]`.
- `export_round_trip.primitive_attributes` contains `TEXCOORD_1` (and `TEXCOORD_0`), and `lightmap_uv_range` lies within [0, 1]. If `TEXCOORD_1` is absent, the exporter dropped the unused lightmap UV set: do not guess a workaround; report the `op_export_gltf` property list to Chris.
- `mini_bake.DIFFUSE` and `mini_bake.TRANSMISSION` are numbers (not `{"error": ...}`), and at least one is > 0.
- Every property Task 6 passes explicitly exists in the matching `op_*` section: `lightmap_pack`: `PREF_CONTEXT` (with item `ALL_FACES`), `PREF_PACK_IN_ONE`, `PREF_NEW_UVLAYER`; `bake`: `type` (items include `DIFFUSE`, `TRANSMISSION`), `pass_filter` (flag enum with `DIRECT`, `INDIRECT`), `use_clear`, `margin`, `target`; `export_scene.gltf`: `export_format`, `export_yup`, `export_texcoords`, `export_normals`, `export_extras`, `export_lights`, `export_cameras`, `use_selection`; `make_single_user`: `type`, `object`, `obdata`; Principled inputs include `Transmission Weight` and `Roughness`. If a name differs, Task 6 uses the probe's name (a rename is a fact, not a guess) and records the substitution in the script's docstring.

---

### Task 6: `tools/bake_model.py`, and the T2 gate bake on Mycologist

**Files:**
- Create: `tools/bake_model.py`
- Create: `tests/fixtures/models/marker_fixture.baked.glb` (produced by the gate run)
- Test: `tests/test_model_fixture.py` (append)

**Interfaces:**
- Consumes: `control.model_layout.parse_source_layout`, `ModelLayoutError`; from `tools.model_bake_helpers`: `BakeError`, `BakeContractError`, `bake_output_path`, `build_mm_bake_extras`, `check_blender_version`, `encode_png_rgba8`, `flip_rows`, `group_leds_by_four`, `inject_bake`, `layout_to_blender_m`, `normalise_maps`, `quantise_group_rgba8`, `validate_baked_glb`; Task 5's probe report.
- Produces: `<stem>.baked.glb` beside the source; the committed gate bake.

- [ ] **Step 1: Write the failing guard test** (append to `tests/test_model_fixture.py`)

```python
import hashlib

from tools.model_bake_helpers import validate_baked_glb


def test_committed_real_bake_is_a_contract_valid_bake_of_the_source_fixture():
    """The PR T2 gate's evidence: a real headless-Blender bake of
    marker_fixture.glb, made on the bake host with the pinned Blender
    (docs/MM_TERRARIUM.md, LED layout models)."""
    path = FIXTURE_DIR / "marker_fixture.baked.glb"
    mm_bake = validate_baked_glb(path.read_bytes(), path=str(path))
    source = (FIXTURE_DIR / "marker_fixture.glb").read_bytes()
    expected = json.loads((FIXTURE_DIR / "expected_layout.json").read_text())
    assert mm_bake["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert mm_bake["layout"] == expected["pixels"]
    assert mm_bake["blender"].startswith("4.5.")
    assert mm_bake["resolution"] == 256
```

Run: `.venv/bin/python -m pytest tests/test_model_fixture.py -q`
Expected: FAIL (FileNotFoundError: `marker_fixture.baked.glb`). It stays failing until Step 5; do not commit it before then.

- [ ] **Step 2: Write the script** (substitute any renamed property the Task 5 probe reported, and note it in the docstring)

```python
# tools/bake_model.py
"""Bakes an artist-authored .glb's per-LED light maps in headless
Blender (spec: mm-tuneshroom docs/superpowers/specs/
2026-09-28-3d-tuneshroom-model-and-view-design.md, section 5.3, as
amended in docs/superpowers/plans/2026-09-28-3d-model-t2-bake-export.md).

    Blender -b --factory-startup --python-exit-code 1 -P tools/bake_model.py -- \
        <model.glb> [--resolution 1024] [--samples 128] [--out PATH]

Writes <model dir>/<model stem>.baked.glb unless --out is given. Every
piece of logic that does not need bpy lives in tools/model_bake_helpers.py
and control/model_layout.py (pytest-tested); this file only drives bpy.
bpy calls verified against Blender 4.5 by tools/blender_probe.py on the
bake host (see docs/MM_TERRARIUM.md, LED layout models).
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.model_layout import ModelLayoutError, parse_source_layout  # noqa: E402
from tools.model_bake_helpers import (  # noqa: E402
    BakeContractError, BakeError, bake_output_path, build_mm_bake_extras,
    check_blender_version, encode_png_rgba8, flip_rows, group_leds_by_four,
    inject_bake, layout_to_blender_m, normalise_maps, quantise_group_rgba8,
)

PINNED_BLENDER = "4.5"   # confirmed on the bake host by tools/blender_probe.py
DEFAULT_RESOLUTION = 1024
DEFAULT_SAMPLES = 128
BAKE_MARGIN_PX = 4
EMITTER_RADIUS_M = 0.001
EMITTER_WATTS = 1.0      # arbitrary: every map is normalised afterwards
LIGHTMAP_UV = "lightmap"
DEFAULT_MATERIAL = "mm_bake_default_translucent"


def _parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="bake_model.py")
    parser.add_argument("model", type=Path)
    parser.add_argument("--resolution", type=int, default=DEFAULT_RESOLUTION)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def _select_only(objs: list) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def _remove_objects(objs: list) -> None:
    for obj in objs:
        bpy.data.objects.remove(obj, do_unlink=True)


def _markers_subtree() -> list:
    """The imported LEDs empty and everything under it. Only deleted and
    counted here; positions come from the source file's own parse (D13)."""
    leds = bpy.data.objects.get("LEDs")
    if leds is None:
        raise BakeError("the imported scene has no 'LEDs' object")
    return [leds, *leds.children_recursive]


def _prepare_meshes(targets: list) -> None:
    """Single-user mesh data (instanced meshes would overlap in the
    shared atlas), UV set 0 kept (a plain one added if absent), any other
    set dropped, and a fresh 'lightmap' set 1 (spec section 4.1)."""
    _select_only(targets)
    bpy.ops.object.make_single_user(type="SELECTED_OBJECTS", object=True, obdata=True)
    for obj in targets:
        uvs = obj.data.uv_layers
        if len(uvs) == 0:
            uvs.new(name="UVMap")
        while len(uvs) > 1:
            extra = uvs[len(uvs) - 1]
            print(f"bake_model: {obj.name}: dropping extra UV set {extra.name!r}")
            uvs.remove(extra)
        uvs.new(name=LIGHTMAP_UV)


def _lightmap_pack(targets: list) -> None:
    """All bake targets packed together into one shared atlas (spec 5.3 step 2)."""
    _select_only(targets)
    for obj in targets:
        obj.data.uv_layers.active = obj.data.uv_layers[LIGHTMAP_UV]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.lightmap_pack(PREF_CONTEXT="ALL_FACES", PREF_PACK_IN_ONE=True,
                             PREF_NEW_UVLAYER=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def _default_material():
    mat = bpy.data.materials.get(DEFAULT_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(DEFAULT_MATERIAL)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Transmission Weight"].default_value = 1.0
        bsdf.inputs["Roughness"].default_value = 0.5
    return mat


def _ensure_materials(targets: list) -> list:
    """Every face needs a node material to bake into: material-less
    meshes and empty slots get the default translucent stand-in (plan
    decision P3). Returns the unique materials in use."""
    for obj in targets:
        if len(obj.material_slots) == 0:
            obj.data.materials.append(_default_material())
        for slot in obj.material_slots:
            if slot.material is None:
                slot.material = _default_material()
            if not slot.material.use_nodes:
                slot.material.use_nodes = True
    return list({slot.material.name: slot.material
                 for obj in targets for slot in obj.material_slots}.values())


def _configure_scene(samples: int) -> None:
    """World black with no other lights (spec 5.3 step 3), Cycles on CPU
    (the bake host has no supported GPU)."""
    scene = bpy.context.scene
    world = bpy.data.worlds.new("mm_bake_world")
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    background.inputs["Strength"].default_value = 0.0
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples


def _add_bake_nodes(materials: list, resolution: int) -> tuple:
    """One float image, one active Image Texture node per material
    pointing at it (the node a Cycles bake writes into)."""
    image = bpy.data.images.new("mm_bake_target", width=resolution, height=resolution,
                                alpha=False, float_buffer=True)
    nodes = []
    for mat in materials:
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.select = True
        mat.node_tree.nodes.active = node
        nodes.append((mat, node))
    return image, nodes


def _bake_pass(kind: str, image, resolution: int) -> np.ndarray:
    bpy.ops.object.bake(type=kind, pass_filter={"DIRECT", "INDIRECT"},
                        use_clear=True, margin=BAKE_MARGIN_PX, target="IMAGE_TEXTURES")
    flat = np.empty(resolution * resolution * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    return flat[0::4].astype(np.float64)   # colour excluded, so R == G == B


def _bake_led(center_m: tuple, image, resolution: int) -> list:
    """Spec 5.3 step 3: a small unit-white spherical emitter (a point
    light with a radius) at the LED; Diffuse and Transmission baked into
    the image in turn (a bake overwrites what it covers), summed, and
    row-flipped to top-down order."""
    light = bpy.data.lights.new("mm_bake_led", type="POINT")
    light.energy = EMITTER_WATTS
    light.shadow_soft_size = EMITTER_RADIUS_M
    emitter = bpy.data.objects.new("mm_bake_led", light)
    bpy.context.scene.collection.objects.link(emitter)
    emitter.location = center_m
    try:
        total = _bake_pass("DIFFUSE", image, resolution) + _bake_pass(
            "TRANSMISSION", image, resolution)
    finally:
        bpy.data.objects.remove(emitter, do_unlink=True)
        bpy.data.lights.remove(light)
    return flip_rows(total.tolist(), resolution, resolution)


def _export(targets: list, image, nodes: list, path: Path) -> bytes:
    """Bake nodes and image removed first (so nothing but the model is
    exported), UV set 0 active and render-active so the artist's UVs stay
    TEXCOORD_0 and 'lightmap' becomes TEXCOORD_1 (confirmed by the probe)."""
    for mat, node in nodes:
        mat.node_tree.nodes.remove(node)
    bpy.data.images.remove(image)
    for obj in targets:
        uvs = obj.data.uv_layers
        uvs.active = uvs[0]
        uvs[0].active_render = True
    bpy.ops.export_scene.gltf(
        filepath=str(path), export_format="GLB", use_selection=False,
        export_yup=True, export_texcoords=True, export_normals=True,
        export_extras=False, export_lights=False, export_cameras=False)
    return path.read_bytes()


def main() -> None:
    args = _parse_args()
    blender = check_blender_version(tuple(bpy.app.version), PINNED_BLENDER)
    model_path = args.model.resolve()
    layout = parse_source_layout(model_path.read_bytes(), path=str(model_path))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(model_path))
    markers = _markers_subtree()
    marker_meshes = [o for o in markers if o.type == "MESH"]
    if len(marker_meshes) != len(layout.pixels):
        raise BakeError(f"Blender imported {len(marker_meshes)} marker meshes, "
                        f"the source parse found {len(layout.pixels)}")
    _remove_objects(markers)
    _remove_objects([o for o in bpy.data.objects if o.type in {"LIGHT", "CAMERA"}])
    targets = [o for o in bpy.data.objects if o.type == "MESH"]
    if not targets:
        raise BakeError("no non-marker mesh to bake onto")

    _prepare_meshes(targets)
    _lightmap_pack(targets)
    materials = _ensure_materials(targets)
    _configure_scene(args.samples)
    image, nodes = _add_bake_nodes(materials, args.resolution)
    _select_only(targets)

    raw = {}
    for pixel in layout.pixels:
        raw[pixel.index] = _bake_led(layout_to_blender_m(pixel), image, args.resolution)
        print(f"bake_model: LED {pixel.index + 1}/{len(layout.pixels)} baked", flush=True)

    normalised, map_scale = normalise_maps(raw)
    texels = args.resolution * args.resolution
    pngs = [encode_png_rgba8(args.resolution, args.resolution,
                             quantise_group_rgba8(group, normalised, texels))
            for group in group_leds_by_four(len(layout.pixels))]

    with tempfile.TemporaryDirectory() as tmp:
        exported = _export(targets, image, nodes, Path(tmp) / "export.glb")
    mm_bake = build_mm_bake_extras(
        source_sha256=layout.model_sha256, layout_pixels=layout.pixels,
        map_scale=map_scale, resolution=args.resolution, blender_version=blender)
    out = args.out or bake_output_path(model_path)
    out.write_bytes(inject_bake(exported, pngs, mm_bake))
    print(f"bake_model: wrote {out} (map_scale {map_scale:.6g})")


if __name__ == "__main__":
    try:
        main()
    except (BakeError, BakeContractError, ModelLayoutError) as exc:
        sys.exit(f"bake_model: {exc}")
```

- [ ] **Step 3: Commit the script** (before shipping it, so `COMMIT` on the host names it)

```bash
git add tools/bake_model.py
git commit -m "feat(bake): tools/bake_model.py, the headless-Blender LED light-map bake"
```

- [ ] **Step 4: Run the gate bake on Mycologist** (Chris's go-ahead first; `portal` skill, research lane)

```bash
git archive --format=tar HEAD control tools tests/fixtures/models | portal ssh mycologist 'rm -rf ~/mm-bake/src && mkdir -p ~/mm-bake/src && tar -x -C ~/mm-bake/src'
git rev-parse --short=12 HEAD | portal ssh mycologist 'cat > ~/mm-bake/src/COMMIT'
portal ssh mycologist 'cd ~/mm-bake/src && time nice -n 10 /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 -P tools/bake_model.py -- tests/fixtures/models/marker_fixture.glb --resolution 256 --samples 64 2>&1 | grep -E "bake_model|Error|Traceback" ; shasum -a 256 tests/fixtures/models/marker_fixture.baked.glb'
```

Expected: 12 `LED n/12 baked` lines, `wrote .../marker_fixture.baked.glb (map_scale ...)`, exit 0, and a sha256. If a bpy call fails, fix it against the probe report (not by guessing), amend the commit, and re-run from Step 4. Two fix rounds without progress: BLOCKED, report to Chris.

- [ ] **Step 5: Bring the bake back and verify it byte-for-byte**

```bash
portal ssh mycologist 'base64 < ~/mm-bake/src/tests/fixtures/models/marker_fixture.baked.glb' | base64 -D > tests/fixtures/models/marker_fixture.baked.glb
shasum -a 256 tests/fixtures/models/marker_fixture.baked.glb
```

Expected: the same sha256 as the host printed in Step 4.

- [ ] **Step 6: Run the gate test and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_model_fixture.py -q && .venv/bin/python -m pytest tests -q`
Expected: PASS. This is the T2 gate: a Mycologist bake of `marker_fixture.glb` whose output passes `inject_bake`'s validation (it was written through it and re-checked here) and the mm-tuneshroom reader rules (`validate_mm_bake` mirrors `lib/render/model_layout.dart`), with non-black maps (`normalise_maps` refuses an all-black bake).

- [ ] **Step 7: Commit the evidence**

```bash
git add tests/fixtures/models/marker_fixture.baked.glb tests/test_model_fixture.py
git commit -m "test(bake): commit the Mycologist Blender 4.5 bake of the marker fixture (T2 gate)"
```

---

### Task 7: Artist guide and pipeline docs

**Files:**
- Create: `docs/instrument-model-guide.md`
- Modify: `docs/MM_TERRARIUM.md` (section `#### LED layout models (control/model_layout.py)`, ~line 891)
- Modify: `docs/carried-instrument-schema.md` (after the `model_sha256` row, ~line 128)

- [ ] **Step 1: Write `docs/instrument-model-guide.md`** (spec section 3, verbatim in substance, for the artist)

```markdown
# Modelling a Shroom for the 3D view (Rhino-first)

A one-page brief for the artist modelling a Shroom (or any carried
instrument) for mm-tuneshroom's 3D look-dev view. The why is in
mm-tuneshroom's `docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md`;
everything needed to build the file is here.

## Origin and units

Model in any Rhino unit; the exporter converts to metres. Put the centre
of the Shroom's base at the world origin, with the Shroom standing up the
Rhino Z axis.

## LED markers

- One **sphere** per physical LED, centred where the LED's emitting face sits.
- Object name `LED_000`, `LED_001`, ... Three digits, zero-padded. **The
  number is the LED's position on the physical data chain**: `LED_000` is
  the first SK6812 after the controller. Indices run 0 to N-1 with no gaps
  and no duplicates, where N is the instrument's pixel count.
- Every marker sits on a sublayer of a top-level layer named `LEDs`. The
  sublayer's name is the marker's **zone**: `LEDs::ring`, `LEDs::stem`.
  Zone names are lowercase `[a-z0-9_]+`. A marker directly on `LEDs`
  belongs to no named zone. Every pixel is always in `primary`; do not
  make a `primary` layer.
- **Size** comes from the sphere's diameter: under 4 mm is `small`, 4 mm
  to under 8 mm is `medium`, 8 mm and over is `large`.

## Materials

Give the cap and stem the physical silicone look with a Rhino PBR
material: base colour, roughness, and transmission/IOR for the translucent
parts. The light maps are baked through these materials, so an opaque
material around an LED bakes dark. Marker spheres need no material; the
bake removes them. A mesh with no material is baked as generic
translucent silicone.

## Export (File > Export Selected or Save As, `.glb`)

- *Export Layers*: **on** (zones depend on it).
- *Map Rhino Z to glTF Y*: **on**.
- *Use Draco compression*: **off** (the catalog refuses Draco).
- *Export texture coordinates* and *Export vertex normals*: on.

## Using another tool

The convention also works from Blender or any tool that writes named
mesh nodes under named parent nodes. It is written for Rhino because that
is the source.

## Where the file goes

Hand the `.glb` to the maintainers. It is saved in mm-terrarium at
`instruments/models/<name>.glb`, next to the catalog entry
(`instruments/<name>.toml`) that declares `model = "models/<name>.glb"`;
several instruments may share one file. The catalog checks every rule
above when it loads, and names the marker and the rule if one is broken.
The maintainers then bake the light maps and export the result to
mm-tuneshroom (`docs/MM_TERRARIUM.md`, *LED layout models*).
```

- [ ] **Step 2: Update `docs/MM_TERRARIUM.md`** In the *LED layout models* section, change "(`docs/instrument-model-guide.md` and `tools/bake_model.py` / `tools/export_models.py` arrive in PR T2)" to "(`docs/instrument-model-guide.md` is the artist's brief)", then append:

```markdown
**Bake and export.** `tools/bake_model.py` bakes per-LED light maps in
headless Blender (Cycles Diffuse + Transmission per LED, summed, row-flipped,
normalised by the brightest texel, packed 4 LEDs per RGBA PNG) and writes
`<stem>.baked.glb` beside the source; `tools/model_bake_helpers.inject_bake`
is the one writer of the baked-file contract (`extras.mm_bake`, spec
section 4.1) and refuses anything `validate_baked_glb` rejects.
`control.model_layout.layout_to_json` is the one layout serializer (bake,
fixture generator, export). `.venv/bin/python -m tools.export_models
<mm-tuneshroom checkout>` (stdlib, no Blender) copies every published
instrument's fresh bake to `assets/models/<name>.<hash8>.baked.glb` with
`models.json`, refuses a stale bake or one whose layout differs from the
catalog's, and copies the shared fixture pair into `test/fixtures/models/`;
it writes nothing unless every model exports. Before the first export that
carries a real model, mm-tuneshroom's startup must stop awaiting every
bundled model.

**Bake host: Mycologist, Blender 4.5 LTS** (`PINNED_BLENDER = "4.5"`;
Intel Mac, and Blender 5.x ships no Intel macOS build; CPU-only Cycles).
Installed by the operator at `/Applications/Blender.app`. Claude bakes as
`claude-ops` through the `portal` skill, with the operator's go-ahead per
run: `git archive HEAD control tools <model dir> | portal ssh mycologist
'... tar -x -C ~/mm-bake/src'`, then `nice -n 10
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup
--python-exit-code 1 -P tools/bake_model.py -- <model.glb>` in
`/Users/claude-ops/mm-bake/src` (disposable), then copy the bake back as
base64 and compare sha256 on both ends. After a Blender upgrade, re-run
`tools/blender_probe.py` there before moving the pin. The committed
`tests/fixtures/models/marker_fixture.baked.glb` is the first real bake
(256 px, the T2 gate).
```

- [ ] **Step 3: Add the cross-reference to `docs/carried-instrument-schema.md`** directly under the `model_sha256` table row's table:

```markdown
The baked model a device bundles is produced from that source by
`tools/bake_model.py` and handed over by `tools/export_models.py`; see
`docs/instrument-model-guide.md` for the convention the source follows.
Neither tool changes this wire blob.
```

- [ ] **Step 4: Run the suite** (docs-only task; also confirms `tools/render_diagrams.py` markers were not disturbed)

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/instrument-model-guide.md docs/MM_TERRARIUM.md docs/carried-instrument-schema.md
git commit -m "docs: artist model guide, bake and export pipeline, Mycologist bake host"
```

---

### Task 8: PR T2

- [ ] **Step 1: Verify main has not moved under this work** (memory: *verify main before PR*): `git fetch origin && git log --oneline HEAD..origin/main -- control/model_layout.py tools/ tests/fixtures/models docs/MM_TERRARIUM.md`. If anything is listed, rebase onto `origin/main`, re-run `.venv/bin/python -m pytest tests -q`, and re-run `test_committed_fixtures_match_regeneration` specifically.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin HEAD
gh pr create --repo Musical-Mycology/mm-terrarium --title "3D model T2: headless-Blender bake, export_models, artist guide" --body-file "$SCRATCH/pr-t2-body.md"
```

The body (written to the scratchpad first) lists: what shipped per task; the gate result (host, `Blender 4.5.14`, resolution 256, samples 64, `map_scale`, bake sha256, wall time); the probe's key facts; the spec amendments section of this plan, verbatim, for Chris to apply to mm-tuneshroom; the carry-in 5 prerequisite; test plan (`.venv/bin/python -m pytest tests -q`).

- [ ] **Step 3: Bind the PR and arm auto-fix** (Chris asked for auto-fix): `mcp__ccd_pr__get_status`, `bind_pr` if needed, then `set_monitor` with auto-fix on. This repo has no GitHub Actions workflows, so auto-fix acts on review comments only. Never enable auto-merge.

- [ ] **Step 4: Closeout** via `superpowers:finishing-a-development-branch`, and `mm-deepdive-sync` for `docs/MM_TERRARIUM.md` (already updated in Task 7) plus a follow-up for mm-documents `services/MM_MYCOLOGIST.md` (new: Blender 4.5 at `/Applications/Blender.app`, bake work area `/Users/claude-ops/mm-bake/`).

---

## Self-Review

**Spec coverage.** §3: Task 7 guide. §4 (one parser, D13): `parse_source_layout` wraps it (Task 1); the bake never reads Blender's scene for positions (Task 6). §4.1: `validate_mm_bake`/`validate_baked_glb` (Task 2) mirror the Dart reader field by field; `inject_bake` is the single writer and self-checks. §5.3 steps 1-5: Task 6 (parse, delete markers, UV 0 + shared lightmap atlas, black world, per-LED point emitter, Diffuse + Transmission into one image in turn, summed, normalised, packed, exported, injected); resolution flag, version pin (Tasks 3, 6). §5.4: Task 4 (exact `models.json` shape, stale and layout refusals, pruning, fixture pair, provenance). §6.4 fixtures: unchanged bytes guarded after every task; the real bake is additive (P5). §7 gate: Task 6 Steps 4-7. §9.1: closed by P1.

**Carry-ins.** 1: `layout_to_json`, used by generator, bake, export (Tasks 1, 3, 4). 2: Task 2. 3: Task 5 probe with explicit stop conditions; Task 6 fixes calls against it. 4: probe Step 3/5 confirm 4.5.x. 5: noted as prerequisite. 6: "garbled" docstring and GLB version (Task 1).

**Fixes to the superseded Tasks 13-16.** `pixel_count=len(markers)` from Blender's import (now the source JSON's count); `version_string` parsing ("4.5.14 LTS"); no row flip (maps would be upside down); no material on the fixture body (bake fails); per-LED image-node leak; mesh emitters; instancing overlap in the atlas; extra source UV sets shifting the lightmap to TEXCOORD_2; export_models pruning `*.baked.glb` only while the Dart guard fails on any unlisted file; partial writes on refusal.

**Known risk handed to PR B (not T2):** a 4th LED stored in PNG alpha is only safe if the browser decodes without premultiplying; three.js's GLTFLoader decodes glTF textures with `premultiplyAlpha: 'none'`, which PR B should confirm in its in-browser check.
