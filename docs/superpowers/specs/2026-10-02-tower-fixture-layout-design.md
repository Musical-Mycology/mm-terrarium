# Tower fixture LED layout

**Date:** 2026-10-02
**Status:** approved design, ready for a plan
**Source of truth for the hardware:** "Fixture Tower - 2100 mm Dimension
Specification" (Google Drive file id `1PpfBMbNlIt-sUmyWG4jPh7aZovVvYkVs`),
front elevation in millimetres with ground center = (0,0).

## 1. Purpose

Pin the Tower's light layout as one contract that the Room profile, the
instrument catalog, the firmware and the 3D model all share, and make
mm-terrarium ready to import an artist's Tower model the moment it arrives.

This spec does **not** produce a 3D model. Chris sends section 4's table and
`docs/tower-model-brief.md` to Sophia; her `.glb` is imported later with the
recipe in section 6. Until then the Tower runs on the no-model fallback (no
`Instrument.layout`; the Console shows a 14-dot strip).

## 2. What it supersedes

The 2026-09-11 ESP32 track assumed the Tower was 8 segments of 12 V SK6812
RGBW strip, 15 px each, 120 px. The Tower definition drawing says otherwise:
"Chainable LED x 8" is eight **single** addressable pixels on a factory-wired
5 V, three-core chain. This spec replaces:

- `docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md`
  section 4.4 and the Tower line of section 9 (procurement).
- `docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md` tasks
  A7 (`tower` env, `PIXEL_COUNT=120`, 2.4 A), B3 (segment labelling and
  pixel-0 placement) and C1 (draft `rooms/TOWER.toml` and
  `instruments/tower.toml`).

Each of those gets a one-line "superseded by" marker pointing here; their
text is otherwise left as history.

## 3. The physical Tower (from the drawing)

| Element | Where (Y mm) | Light |
|---|---|---|
| Maitake base | 0 to 350 | two concealed off-the-shelf PAR lights; "game clock + celebration; not tap-responsive" |
| Light path + indicators | 450 to 1640 | 8 mushroom stages, 1 addressable LED each, silicone diffuser per stage; "correct taps bloom indicators upward, lit mushrooms remain on as progress" |
| Responders | sides, about 1000 to 1750 (artist's model) | 4 identical detachable discs, diameter 190 mm, single color, "real-time tap feedback only" |
| Crown | 1550 to 2100 | fiber optics and props; no light source in this revision |

Indicator chain (drawing panel A): square PCB with one central addressable
LED, factory center pitch 152.4 mm, stages 1/8 to 8/8 at Y = 480, 632, 785,
937, 1089, 1242, 1394, 1547. A 3-pin locking connector sits at the Y = 1050
structural split, between stages 4 and 5. Pinout: red +5 V, middle data,
black GND.

## 4. The layout contract: one fixture, 14 pixels

The frame index is the contract. Every consumer (room zones, firmware, model
markers, Bits) uses these indices.

| px | Zone | Block | Element | Nominal (x, z) mm | Marker size |
|---|---|---|---|---|---|
| 0 | `progress` | `lower` | stage 1/8 | (+44, 482) | medium |
| 1 | `progress` | `lower` | stage 2/8 | (+34, 632) | medium |
| 2 | `progress` | `lower` | stage 3/8 | (-20, 791) | medium |
| 3 | `progress` | `lower` | stage 4/8 | (-29, 937) | medium |
| 4 | `progress` | `upper` | stage 5/8 | (+50, 1089) | medium |
| 5 | `progress` | `upper` | stage 6/8 | (+36, 1242) | medium |
| 6 | `progress` | `upper` | stage 7/8 | (-25, 1394) | medium |
| 7 | `progress` | `upper` | stage 8/8 | (+45, 1547) | medium |
| 8 | `responder` | `responders` | R1 lower-left | (-244, 1000) | medium |
| 9 | `responder` | `responders` | R2 lower-right | (+255, 1250) | medium |
| 10 | `responder` | `responders` | R3 upper-left | (-251, 1500) | medium |
| 11 | `responder` | `responders` | R4 upper-right | (+239, 1750) | medium |
| 12 | `beat` | `par` | PAR left | (-80, 90) | large |
| 13 | `beat` | `par` | PAR right | (+80, 90) | large |

Rules:

- **Coordinates.** x is the drawing's X (viewer's right positive), z is the
  drawing's Y (up). y (depth) is left to the artist: each marker sits where
  that light actually emits. Pixels 0 to 11 are read off the artist's first
  model (`Tower.glb`, 2026-10-05), which Chris made the reference for light
  count and placement on 2026-10-09, zig-zag included; the PARs (12, 13)
  are read off the elevation. All are nominal to about 25 mm.
- **Progress order.** px 0 is the base stage, so "bloom upward" is ascending
  index, and the controller in the base feeds stage 1 first.
- **Responder order.** Bottom to top, left before right.
- **Append-only.** New light elements (a crown or canopy source) take px 14
  onward. Existing indices never move.
- **Color order.** `GRB`, 3 channels (42-byte frame). The RGBW assumption is
  gone: the drawing's part is three-core 5 V.
- **Size bands** follow the artist guide (medium is a 4 to under 8 mm marker
  sphere, large is 8 mm and over).

## 5. mm-terrarium changes

### 5.1 `rooms/TOWER.toml`

One fixture, same format as `rooms/TEST.toml`:

- `name = "tower"`, `color_order = "GRB"`, `instrument = "tower"`,
  `backends = ["devicelink"]`.
- Blocks (hardware composition): `lower` 0-3, `upper` 4-7, `responders`
  8-11, `par` 12-13.
- Zones (gameplay targeting): `progress` 0-7, `responder` 8-11, `beat`
  12-13. No `primary` (synthesized).

### 5.2 `instruments/tower.toml`

- `pixels = 14`, `capabilities = ["light.surface"]`, a description.
- **No `model` line** until Sophia's file lands (a published instrument
  whose model file is missing fails to load, so the line and the file
  arrive in one commit).
- **No functions.** Mushica's own work adds them (`beat_pulse`,
  `progress_fill`, a responder flash).

### 5.3 Two load-time checks in `_parse_room` (`control/terrarium_config.py`)

Run per fixture after it is built, raising `TerrariumConfigError` like the
existing fixture checks (so a published room fails to load and a draft
records the error on its `CatalogEntry`):

1. **Pixel count.** If the fixture's instrument declares `pixels` (non-zero),
   it must equal the fixture's `pixel_count` (sum of blocks). Today no room
   fixture's instrument declares `pixels`, so existing rooms are unaffected.
2. **Model zones.** If the instrument has a `layout`, then for every marker,
   either its zone is `None` or it is the name of a room zone on that fixture
   whose range covers the marker's index. Also the layout length must equal
   the fixture's `pixel_count` (implied by check 1 plus the model parser's own
   marker-count check, but stated so the error names the room).

Error messages name the room, the fixture, the pixel index and both zone
names, so the import's first test run tells Sophia exactly which marker is
wrong.

### 5.4 Artist guide (`docs/instrument-model-guide.md`)

One clarification: the marker number is the **frame index**. For pixels on a
data chain that equals the chain position; elements the firmware drives
another way (the Tower's PARs, over DMX) still take their frame index. Room
fixtures put the origin at ground center, as their drawings do.

### 5.5 Sophia's brief (`docs/tower-model-brief.md`)

A standalone, code-free brief: section 4's table, origin and axes, marker
naming (`LED_000` to `LED_013`), the three `LEDs::<zone>` layers, size bands,
materials and export settings (by reference to the artist guide), the
nominal-position tolerance, what to deliver (one `.glb`, no Draco), and the
import recipe in section 6 for whoever imports it.

### 5.6 Deep-dive

A short *Tower* paragraph under *LED layout models* in `docs/MM_TERRARIUM.md`:
the room, the 14-pixel contract, the fallback, the new checks, and the import
recipe.

## 6. Import recipe (when Sophia's file arrives)

1. Save it as `instruments/models/tower.glb`.
2. Add `model = "models/tower.glb"` to `instruments/tower.toml`.
3. Run the test suite. The model parser checks names and count; section
   5.3's checks confirm zones against `rooms/TOWER.toml`.
4. Bake on Mycologist per *LED layout models* (`tools/bake_model.py`).
5. `.venv/bin/python -m tools.export_models <mm-tuneshroom checkout>`.

No new tooling: this is the existing pipeline.

## 7. Firmware contract (mm-devshroom, filed as an issue for Victor)

- `tower` env: `PIXEL_COUNT=14`; a `/<dev>/leds` frame is 42 bytes, GRB.
- px 0-7 on data pin A (the indicator chain, stage 1 first), px 8-11 on
  data pin B (responders R1 to R4, starting from the base). Separate pins so
  a responder unplugged for transport cannot cut the progress chain.
- px 12-13 converted to DMX for the two PARs (channel map per the PAR model,
  a confirm item).
- Power limiter at 1.0 A on a supply of at least 2 A at 5 V (12 chain pixels
  at 60 mA full white is 0.72 A, so the limiter never dims normal play).
  PARs are mains powered and outside the limiter.

## 8. Confirm items (hardware, before or at Gate 1, Oct 9)

1. **Color order** of the purchased pixels: check `GRB` at the bench by
   sending a pure red frame and confirming the indicators show red.
2. **Indicator zig-zag vs chain pitch.** Superseded 2026-10-09: the
   indicators zig-zag as in the artist's model (section 4). The hops there
   run about 145 to 170 mm against the chain's 152.4 mm factory pitch, so
   check at the bench that the chain reaches every stage, or order longer
   leads.
3. **Responders detachable for transport only;** all four attached during a
   show.
4. **PAR DMX channel map.**
5. **Base depth**, which the drawing itself marks provisional.

## 9. Testing

- `rooms/TOWER.toml` loads: one fixture, 14 px, the block and zone ranges in
  5.1, instrument `tower` with `pixels = 14` and no layout.
- Check 1 fails on a fixture whose instrument declares a mismatched
  `pixels`.
- Check 2 passes for a synthetic `.glb` whose markers match the room's zones
  and fails, naming the index, for one whose marker sits in the wrong zone.
  Synthetic files are built in the test suite with `tests/glb_builder`; they
  are a few spheres, not a Tower model.
- The existing room and catalog tests still pass unchanged.

## 10. Out of scope

- The Tower 3D model itself (Sophia).
- mm-tuneshroom's 3D renderer (PR B) and viewing a Room fixture there.
- A 3D view in the Terrarium Console.
- Mushica's Tower functions and cues.
- Crown or canopy lighting (would take px 14 onward).
- Firmware implementation (section 7 is filed, not built here).
