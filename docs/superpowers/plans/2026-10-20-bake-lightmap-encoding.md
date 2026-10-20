# Bake light-map encoding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-LED `map_scale` plus a log10 texel curve, so every LED's light map survives 8-bit quantisation.

**Architecture:** All logic stays in the pure-Python `tools/model_bake_helpers.py` (pytest-tested); `tools/bake_model.py` only gains flags and a report line. The contract change lands in `validate_mm_bake`, which `inject_bake`, `export_models` and the fixture tests already share.

**Tech Stack:** Python 3 stdlib (+ numpy inside Blender only), pytest, Blender 4.5 on Mycologist.

**Spec:** `docs/superpowers/specs/2026-10-20-bake-lightmap-encoding-design.md`

## Global Constraints

- `map_scale`: list of exactly `pixels` finite numbers, each > 0.
- `map_encoding`: object with exactly `curve` (only `"log10"`) and `decades` (finite number > 0).
- Encode: `t > 0` gives `round(255 * (1 + log10(t) / decades))` clamped to [0, 255]; 0 or sub-floor gives 0.
- Decode: code 0 is 0.0, else `10 ** (decades * (code / 255 - 1))`.
- Default `decades` = 5 (`DEFAULT_DECADES` in `tools/model_bake_helpers.py`).
- No em dashes in docs or messages.

## Review Focus

- A raw map with NaN texels (Cycles glitch): must not poison a peak or a code; clamp NaN to 0 alongside negatives. Test in Task 1.
- `decades` passed as an int (`5`) in JSON: must validate (number, not bool). Test in Task 2.
- `map_encoding.decades = True` (bool is an int in Python): must be refused. Test in Task 2.
- A short final group (14 LEDs, group 3 holds 2): unused channels stay 0. Existing test covers it; keep it with the new signature.
- An old-format bake (number `map_scale`) reaching `export_models`: refused with a message naming `map_scale`. Covered by Task 2's validator test.

---

### Task 1: Per-LED normalisation and the log10 curve

**Files:**
- Modify: `tools/model_bake_helpers.py` (`normalise_maps`, `_to_byte` replaced, `quantise_group_rgba8`)
- Test: `tests/test_model_bake_helpers.py`

**Interfaces:**
- Produces: `DEFAULT_DECADES = 5`; `encode_log10(t: float, decades: float) -> int`; `decode_log10(code: int, decades: float) -> float`; `normalise_maps(raw: dict[int, list[float]]) -> tuple[dict[int, list[float]], list[float]]` (scales ordered by LED index); `quantise_group_rgba8(group, normalised, texel_count, decades) -> bytes`.

- [ ] **Step 1: Failing tests**
  - `test_normalise_maps_scales_each_led_by_its_own_peak`: `{0: [0.5, 1.0], 1: [2.0, 0.0]}` gives `{0: [0.5, 1.0], 1: [1.0, 0.0]}`, scales `[1.0, 2.0]`.
  - `test_normalise_maps_clamps_negative_and_nan_texels_to_zero`.
  - `test_normalise_maps_refuses_naming_every_dark_led`: `{0: [1.0], 1: [0.0], 2: [-1.0]}` raises `BakeError` matching `LED 1` and `LED 2`.
  - `test_encode_log10_endpoints`: `encode_log10(1.0, 5) == 255`, `encode_log10(0.0, 5) == 0`, `encode_log10(1e-6, 5) == 0`, `encode_log10(10 ** (-5 * 254 / 255), 5) == 1`.
  - `test_log10_round_trip_within_half_a_step`: for `t` in a log sweep 1e-4.9..1, `abs(decode/t - 1) <= 10 ** (5 / 510) - 1 + 1e-9`.
  - `test_decode_log10_zero_is_black`.
  - Update the existing `quantise_group_rgba8` packing test to pass `decades=5` with inputs 1.0 and 0.0 so expected bytes stay 255/0.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_model_bake_helpers.py -q`; expect the new tests to FAIL.
- [ ] **Step 3:** Implement. `math.isnan` guard in the clamp; the refusal lists every dark LED index.
- [ ] **Step 4:** Same command, PASS.

### Task 2: Contract (`validate_mm_bake`, `build_mm_bake_extras`, fixture generator)

**Files:**
- Modify: `tools/model_bake_helpers.py` (`MM_BAKE_KEYS`, `validate_mm_bake`, `build_mm_bake_extras`), `tools/generate_model_fixture.py`, `tests/fixtures/models/marker_fixture.mmbake.glb` (regenerated)
- Test: `tests/test_model_bake_helpers.py`

**Interfaces:**
- Consumes: `DEFAULT_DECADES`.
- Produces: `build_mm_bake_extras(*, source_sha256, layout_pixels, map_scale: list, decades: float, resolution, blender_version) -> dict`; `map_encoding_block(decades) -> dict` returning `{"curve": "log10", "decades": decades}`.

- [ ] **Step 1: Failing tests.** `_valid_bake(pixels)` builds `map_scale=[1.0] * pixels`, `map_encoding={"curve": "log10", "decades": 5}`. Add refusal cases to the parametrised test: number `map_scale` (match `map_scale`), wrong length, a 0 entry, a NaN entry, a bool entry, missing `map_encoding`, `curve="gamma"`, `decades=0`, `decades=True`, an extra key in `map_encoding`. Add `test_validate_mm_bake_accepts_int_decades`. Update `test_build_mm_bake_extras_...` to pass `map_scale=[0.87, 0.5]`, `decades=5` and assert both keys.
- [ ] **Step 2:** Run; FAIL.
- [ ] **Step 3:** Implement; generator writes `[1.0] * pixels` and `map_encoding_block(DEFAULT_DECADES)`; regenerate with `.venv/bin/python -m tools.generate_model_fixture`.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests -q`; everything passes except `test_committed_real_bake_is_a_contract_valid_bake_of_the_source_fixture` (old-format real bake; fixed in Task 4).
- [ ] **Step 5:** Commit tasks 1-2 together with the spec and this plan.

### Task 3: `tools/bake_model.py` flags and report

**Files:** Modify `tools/bake_model.py`.

- [ ] `--decades` (float, default `DEFAULT_DECADES`), `--raw PATH` (`numpy.savez_compressed(PATH, **{f"led_{i:03d}": array})` of the raw float32 maps before normalising).
- [ ] After quantising, print per LED: `bake_model: LED i zone peak <scale:.6g> lit <pct>%` where lit is the share of texels whose code > 0 (`coverage_report(normalised, decades) -> list[float]` in helpers, tested: `{0: [1.0, 0.0], 1: [1e-6, 0.5]}` at 5 decades gives `[0.5, 0.5]`).
- [ ] Pass `map_scale=scales, decades=args.decades` into `build_mm_bake_extras`; final line prints `min/max map_scale`.
- [ ] `python -m py_compile tools/bake_model.py`; commit.

### Task 4: Re-bake on Mycologist

- [ ] With the operator's go-ahead, ship `git archive HEAD control tools instruments/models tests/fixtures/models` to `~/mm-bake/src` as `claude-ops`, bake `tests/fixtures/models/marker_fixture.glb --resolution 256` and `instruments/models/tower.glb --raw ~/mm-bake/tower.raw.npz` (background, ~35 min), copy both back, compare sha256.
- [ ] Decode the Tower's maps (scratch script) and confirm all 14 LEDs have code > 0 texels; record the table for the PR.
- [ ] `.venv/bin/python -m pytest tests -q` all pass; update `docs/MM_TERRARIUM.md` *Bake and export* (per-LED scale, log10 curve, new refusal, `--decades`/`--raw`); commit.

### Task 5: mm-tuneshroom reader draft

- [ ] In an mm-tuneshroom worktree branch: `BakedModel.mapScale` becomes `List<double>`, add `MapEncoding mapEncoding {String curve; double decades}`, validation mirroring Task 2 messages; update `test/support/glb_builder.dart`, `test/render_model_layout_test.dart`, re-copy `test/fixtures/models/marker_fixture.mmbake.glb`; apply the spec amendment text to its 3D spec. `flutter test` passes. Leave as a draft PR or a hand-over note.
