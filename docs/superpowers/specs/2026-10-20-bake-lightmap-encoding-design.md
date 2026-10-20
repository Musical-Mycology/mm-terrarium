# Bake light-map encoding: per-LED scale and a log10 curve

Status: drafted 2026-10-20 (side session of the Tower GLB integration).
Amends mm-tuneshroom
`docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md`
section 4.1 (the baked-file contract), section 5.3 step 4 (normalise and
pack) and section 6.2 (the shader formula), and closes that spec's *Open
look-dev question (recorded 2026-09-29 from T2)*. Wording for the
mm-tuneshroom spec is in *Spec amendment* below. Tools:
`tools/bake_model.py`, `tools/model_bake_helpers.py`,
`tools/generate_model_fixture.py`.

## Problem

The Tower bake of Sophia's 2026-10-19 export (`instruments/models/tower.baked.glb`,
commit c557b56, sha256 `bd12bdb0`, 1024 px) is unusable: its maps, decoded
from the committed PNGs, are

| LEDs | zone | peak code (of 255) | texels with any light |
|------|------|--------------------|-----------------------|
| 0-7 | progress | 20 to 255 | 0.012% to 0.142% |
| 8-11 | responder | 3 to 10 | about 0.04% |
| 12-13 | beat (the two PARs) | 0 | 0% |

Two separate dynamic-range failures stack here.

1. **Between LEDs.** Section 5.3 step 4 normalises every map by the single
   brightest texel across all LEDs (`map_scale` = 8511 here). A progress LED
   5 mm behind its diffuser sets that peak, so a responder whose hottest
   texel is 1/30 of it peaks at code 9, and a PAR whose hottest texel is
   under 1/510 of it quantises to nothing at all.
2. **Within one LED.** Even the brightest progress LED (peak 255) lights
   only 0.13% of the atlas. Inverse-square falloff from a source 5 mm from
   its nearest surface puts a surface 100 mm away at 1/400 of the hotspot,
   below linear 8-bit's half-code threshold (1/510). The 2026-10-09 marker
   fixture shows the same thing on a small model: 1.8% of texels non-zero
   for its brightest LED, which is the T2 look-dev question.

Fixing only (1) gives every LED a code-255 hotspot and leaves each map a
pinprick. Fixing only (2) with a curve still loses the PARs if their peak
sits below the curve's floor relative to the global peak. Both have to
change.

## Options weighed

- **Per-LED scale only** (`map_scale` becomes a list). Fixes (1); (2) is
  untouched, so every LED is still a pinprick. Rejected alone.
- **Gamma curve only** (store `t^(1/g)`). g = 2.2 extends the floor to
  about 1e-6 of the global peak, which probably rescues the PARs, but the
  precision is uneven where it matters: between codes 3 and 4 the decoded
  value jumps 89%, between 10 and 11 23%, so the dim tail (exactly the
  far-surface glow this fixes) bands. WebGL's hardware sRGB decode cannot
  help either: it decodes RGB only, and the maps use all four channels for
  four LEDs.
- **(recommended) Per-LED scale plus a log10 curve.** Each LED is
  normalised to its own peak, and each texel is stored as its position on a
  `decades`-wide log scale below that peak. Inverse-square falloff is a
  power law, so it is a straight line in log space: every code step is the
  same ratio (`10^(decades/255)`, 4.6% at 5 decades), with no banding cliff
  in the tail and the full code range spent on the falloff the view needs.
  It costs one `pow` per LED per fragment in the shader, the same as gamma.
  What would change the call: a view that needs exact linear precision
  near each hotspot (log's 4.6% step there is coarser than linear's), which
  a preview of diffused light does not.

## The encoding

For LED i with raw (Cycles, linear) texels `r` after clamping bake noise
below 0 to 0:

- `map_scale[i] = max(r)`, the LED's own peak. A bake in which **any** LED
  has peak 0 is refused, naming every such LED: that LED's light reaches no
  baked surface, a modelling error (a marker buried in opaque geometry, or
  a missing transmissive material), never a result. This replaces the
  all-black refusal, which only caught every LED being dark at once.
- `t = r / map_scale[i]`, in [0, 1].
- `code = round(255 * (1 + log10(t) / decades))` for `t > 0`, clamped to
  [0, 255]; `t = 0`, or any `t` that rounds below code 1, stores 0.
- **Decode** (the view, and the tests): code 0 is black; otherwise
  `linear = map_scale[i] * 10^(decades * (code / 255 - 1))`.

So code 255 is the LED's peak, code 1 is `10^(-decades * 254/255)` of it,
and code 0 is true black. The floor is below the noise of a 128-sample
bake at any `decades` we would choose, so stray noise texels do become
non-zero; they decode to what Cycles actually computed, which is faint.

**`decades` defaults to 5**, a `--decades` flag on `tools/bake_model.py`.
Five covers inverse-square from 5 mm to 300 mm (3.6 decades) plus a
diffuser's transmission loss, at a 4.6% step. The re-bake reports each
LED's lit coverage so the default can be tuned against a real model; a
change of default is a re-bake, not a contract change.

Linear texture filtering now interpolates log codes, so between two texels
the view blends geometrically, and at a lit region's edge (a code next to
a 0) it fades through ever-smaller values rather than linearly. Both are
fine for a preview.

## Contract change (section 4.1)

Root `extras.mm_bake`:

```jsonc
{
  "source_sha256": "<sha256 of the source .glb>",
  "pixels": 12,
  "map_scale": [8511.2, 301.7, /* ... */],   // one per LED, each > 0:
                                             // LED i's raw peak
  "map_encoding": {"curve": "log10", "decades": 5},
  "maps": [0, 1, 2],
  "uv": "TEXCOORD_1",
  "resolution": 1024,
  "blender": "X.Y.Z",
  "layout": [ /* unchanged */ ]
}
```

- `map_scale` is a list of exactly `pixels` finite numbers, each > 0.
- `map_encoding` is an object with exactly `curve` and `decades`; `curve`
  is `"log10"` (the only curve), `decades` a finite number > 0.
- Everything else is unchanged.

**No version field.** The change is self-detecting and fails closed both
ways: today's Dart reader refuses a list `map_scale` ("must be a number"),
and the new validators refuse a number `map_scale` and a missing
`map_encoding`. `tools/export_models.py` already validates every bake, so an
old-format bake cannot be exported after this lands; it must be re-baked.
`map_scale` values stay in the bake's own linear units (a 1 W point light),
so ratios between LEDs keep their physical meaning.

## Bake changes (section 5.3 step 4)

Step 4 becomes: normalise each LED's map by its own brightest texel and
record that peak as `map_scale[i]`; refuse if any LED's peak is 0; encode
each texel with the log10 curve above at `decades` (default 5); pack LEDs
4i to 4i+3 into R, G, B, A of texture i as before.

In code:

- `normalise_maps(raw) -> (normalised, scales)`: per-LED peak; `scales` a
  list ordered by LED index; the refusal names every dark LED.
- `encode_log10(t, decades) -> int` and `decode_log10(code, decades) ->
  float`, pure.
- `quantise_group_rgba8(group, normalised, texel_count, decades)`.
- `build_mm_bake_extras(..., map_scale: list, decades: float)` and
  `validate_mm_bake` per the contract above.
- `tools/bake_model.py`: `--decades` (default 5); after encoding it prints
  one line per LED with its peak and the share of atlas texels at code > 0,
  so a dark or pinprick LED is visible in the bake log. `--raw PATH`
  optionally saves the raw float maps (`numpy.savez_compressed`) so a
  future curve or `decades` change can be re-encoded without a 35-minute
  re-bake.
- `tools/generate_model_fixture.py`: the synthetic fixture writes
  `map_scale` as `[1.0] * pixels` and the default `map_encoding`; its
  committed bytes change.
- `tests/fixtures/models/marker_fixture.baked.glb` and
  `instruments/models/tower.baked.glb` are re-baked on Mycologist.

## Acceptance

- `pytest` passes, including new tests for the curve's round trip
  (relative error at most half a code step across the encodable range),
  code 0 for 0 and sub-floor values, per-LED normalisation, the dark-LED
  refusal naming each LED, and the new contract rules.
- The Tower re-bake passes `validate_baked_glb`, and **every one of its 14
  LEDs has a non-zero lit area** in the decoded maps (code > 0), with the
  per-LED coverage table recorded in the PR.
- mm-tuneshroom's reader change is drafted (below).

## mm-tuneshroom (reader and view)

- `lib/render/model_layout.dart`: `BakedModel.mapScale` (double) becomes
  `mapScale` (`List<double>`, length `pixels`, each > 0), plus
  `mapEncoding` (`curve`, `decades`), validated with the same rules and
  messages as `validate_mm_bake`; tests and `test/support/glb_builder.dart`
  follow; `test/fixtures/models/marker_fixture.mmbake.glb` is re-copied by
  `export_models`.
- The 3D view (section 6.2, not built yet) decodes per the formula below.
  The view also needs an exposure: `map_scale` is a hotspot's raw value,
  so an unexposed sum saturates next to each LED. Choosing it is the
  view's look-dev, not this contract.

## Spec amendment (for the mm-tuneshroom spec)

Section 4.1, the `map_scale` line of the example and a new line:

> `"map_scale": [8511.2, 301.7],  // one per LED: its raw peak (each > 0)`
> `"map_encoding": {"curve": "log10", "decades": 5},  // texel code c > 0 decodes to map_scale[i] * 10^(decades * (c/255 - 1)); c = 0 is black`

Section 5.3 step 4, replace the first sentence with:

> Normalise each LED's map by its own brightest texel and record that peak
> as `map_scale[i]`; refuse the bake if any LED's peak is 0. Store each
> texel on a log10 curve `decades` wide (default 5): code
> `round(255 * (1 + log10(t) / decades))`, 0 for black or below the floor.
> (Amended 2026-10-20: mm-terrarium
> `docs/superpowers/specs/2026-10-20-bake-lightmap-encoding-design.md`.)

Delete the *Open look-dev question (recorded 2026-09-29 from T2)*
paragraph; this answers it.

Section 6.2, the formula:

```
colour = albedo * (ambient + exposure * sum_i( rgb_i * map_scale[i] * dec(map_i(uv1)) ))
dec(c) = c > 0 ? 10^(decades * (c - 1)) : 0      // c in [0, 1] as sampled
```
