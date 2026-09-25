# The VENUE Room: LED bars and fiber engines on two WLED controllers

Status: **approved for planning, hardware inputs pending (§2).** Every
design decision below is made. The inputs in §2 are hardware facts nobody
has yet, and each one names the section it fills. The plan
(`docs/superpowers/plans/2026-09-25-venue-room.md`) builds with N = 1 under
§2's rule and leaves every `max_amps` value as a named placeholder, so it
can run before §2 closes. Closing §2 later is a data edit, not a code
change.

Base: `main` at `fbc5c2f`. PR #140 (the Art-Net FixtureSink) is merged.

Closes the follow-up named in
[`2026-09-23-artnet-fixture-sink-design.md`](2026-09-23-artnet-fixture-sink-design.md)
§2 and §12: "The venue Room profile with bars and fiber fixtures on two
controllers."

## 1. Context

- **Hardware** (`mm-documents/MM_HARDWARE_DESIGN.md`, Terrarium bill of
  materials and §11.5):
  - LED bars: 12 V SK6812 RGBW, 144/m, ~6 m, which is 864 px. This is
    DEMO's `array` fixture today.
  - Fiber optics: "End-glow bundles ×3 + RGBW engines". One colour per
    bundle, and the bundles are independently controlled.
  - Two WLED ESP32 controllers, one driving the bars and one driving the
    fiber engines.
- **Rooms** are TOMLs under `rooms/`, keyed by filename. Bits opt in by
  name through `room_types`.
- **Art-Net outputs** are `[[artnet]]` entries in `terrarium.toml`,
  validated by `control/terrarium_config.py`. One output drives one fixture.
  Art-Net fixtures must be `color_order = "RGBW"`.
- **A Bit's ROOM light manifest** binds a `primary` target to every
  fixture. A target of `<fixture>` or `<fixture>.<zone>` binds only that
  fixture (`control/role_config.py` `slice_light_manifest`).
- **Bit capability requirements** are aggregated across fixtures
  (`control/engine.py` `_resolve_room_requirements`). So a fixture with a
  light-only instrument does not block a Bit whose room manifest also
  carries audio.

## 2. Inputs to complete (hand-off)

| # | Input | Who supplies it | What it fills |
|---|---|---|---|
| I1 | **N**: physical RGBW LEDs per fiber engine, and whether all three engines are equal | Hardware owner, from the engine part | §5 (`fiber` px, blocks, zones) and §7 (fiber amps) |
| I2 | **The fiber engine part**: supply voltage and full-white current per LED | Hardware owner, from the datasheet or a clamp-meter reading | §7 `amps_per_pixel_full` for the fiber output |
| I3 | **The Terrarium 12 V PSU rating.** The BOM says "12 V 20 A". The §11 parts table says Mean Well LRS-150-12 (12.5 A). | Hardware owner. Also fix whichever row of `MM_HARDWARE_DESIGN.md` is wrong. | §7 budget, §8 `[psus]` value |
| I4 | **Whether the fiber engines hang on that same 12 V PSU**, or on their own supply | Hardware owner | §7 split and §8 `psu` keys |

Rules for completing them:

- **If all three engines are not equal**, §5's equal-zone layout becomes
  per-bundle counts. Nothing else changes.
- **If N is unknown at implementation time**, implement with N = 1 and
  make each fiber zone's `count` a single edit. §5 then describes the room
  as it stands. Do not ship a guessed N > 1.
- **I3 must be resolved before any `max_amps` value is committed.** The
  commented examples in §7 use named placeholders until then.

## 3. Goals and non-goals

**Goals**

- G1. A new Room, `VENUE`, with two RGBW fixtures, `bars` and `fiber`.
  Each is driven by its own `[[artnet]]` output to its own WLED controller.
- G2. VENUE loads and runs with **no device bound**, driven entirely by
  Art-Net. It needs no simulator and no admin tap.
- G3. Bits address each fixture by name (`@fixture:bars`, `@fixture:fiber`,
  and manifest targets `bars.<zone>` and `fiber.b1`..`fiber.b3`). The
  three fiber bundles are independently addressable.
- G4. Outputs that share a PSU are checked at config load. Their
  `max_amps` values must sum to at most 80 % of the PSU's rating.
- G5. The whole room runs with no hardware against
  `harness/artnet_listen.py`.

**Non-goals**

- White-aware instruments and an RGBW SolidCue. Both are still the
  2026-09-23 follow-up, and W stays 0 on both fixtures in this slice.
- Console targeting and mute display for unbound fixtures. That landed
  separately in PR #144
  ([`2026-09-25-console-fixture-targets-design.md`](2026-09-25-console-fixture-targets-design.md)).
  VENUE depends on it for operator use, but not for this slice's tests.
- The 33 fps vs 44 Hz gap. PR #142 addressed it separately
  ([`2026-09-25-tick-pacing-design.md`](2026-09-25-tick-pacing-design.md)):
  the 44 Hz tick is now paced to deadlines. The VENUE loopback figures
  (bars 32.6 fps, fiber 32.5 fps) were measured before PR #142 and have
  not been re-measured.
- Retiring or reshaping DEMO.
- Multiple controllers per fixture.

## 4. Decisions

| # | Decision | Why | What would change it |
|---|---|---|---|
| D1 | **(recommended) A new `rooms/VENUE.toml`. DEMO stays as it is, the single-fixture 864 px dev room.** | MetronomeBit, TestBit, Rev1Bit, run_stack defaults and the DEMO live-verify history all name DEMO and its `array` fixture. A new room adds without breaking any of them. | If DEMO stops being used for dev runs, fold it into VENUE later and delete it. |
| D2 | **Fixture names `bars` and `fiber`, declared in that order.** | They name the hardware. Declaration order is the Console strip order, and the bars are the primary display. | None. |
| D3 | **`bars` reuses DEMO's layout exactly:** six 144 px blocks `m1`..`m6` and three 288 px zones `left`/`center`/`right`, instrument `venue_array`. | Same strip, same controller build-out. It also keeps the verified seam-sweep numbers comparable. | A different physical run length at the venue. |
| D4 | **`fiber` is 3 × N px.** It has one block per engine (`e1`..`e3`, N px each) and one zone per bundle (`b1`..`b3`, aligned 1:1 with the blocks). | One colour per bundle and independent control is exactly a zone per bundle. A block per engine lets `--identify-blocks` (`harness/o2_shroom.py`) paint each engine's range a distinct colour on the WebSim canvas, which checks the profile. N is I1, and the user chose to model each engine's physical LEDs rather than use WLED grouping. | I1. |
| D5 | **A new light-only instrument, `instruments/venue_fiber.toml`.** It carries capabilities `["light.surface"]`, no `audio.flsyn`, and no white. | A second fixture with `venue_array` would start a second FluidSynth drone. Light-only keeps one room voice. W = 0 follows the 2026-09-23 non-goal. | A white-aware instrument slice. That is the named follow-up, and it would upgrade both instruments together. |
| D6 | **(recommended) Both outputs use `start_universe = 0` and port 6454 on hardware. On loopback, the fiber output sets `port = 6455`.** | Each controller is its own host, so universes never overlap on hardware, and both WLEDs keep their default start universe. `harness/artnet_listen.py` binds one port with no address reuse, and it decodes one contiguous strip. So a no-hardware run needs one listener per output, on its own port. The overlap validator keys on host:port, so the loopback config passes. | A listener that serves several fixtures on one port. |
| D7 | **VENUE depends on PR #143's covered-fixture binding; this spec does not change binding.** PR #143 ([`2026-09-25-artnet-fixture-no-simulator-design.md`](2026-09-25-artnet-fixture-no-simulator-design.md)) landed separately: a fixture with `[[artnet]]` coverage gets no simulator, is never waited for, and never fails a load for lack of a device (§9). | Without it, G2 is impossible: an all-Art-Net room would time out in `wait_for_room_binding`. PR #143 also closed PR #140's "double-bind" bring-up prerequisite. | None. |
| D8 | **(recommended) An optional `psu` key on `[[artnet]]` and a `[psus.<name>]` table with `amps`, so the validator enforces the 80 % sum.** | VENUE is the first room where two outputs share a supply. If the budget is wrong, it surfaces only as a brownout at the venue. The check is a few lines in a validator that already exists. | If PSU topology is to stay a bring-up checklist item only, drop §8. The config comment would then carry the rule, as today. |
| D9 | **(recommended) TestBit and MetronomeBit gain `"VENUE"` in `room_types`. Rev1Bit, ChaseBit and MinigameBit do not.** | TestBit is the reference scored-and-jam Bit, and every shipped room has one. MetronomeBit is the production game, and VENUE is where it will run. Both address the room only through `primary`, so no fixture-specific code changes. Rev1Bit is a board bench check. ChaseBit and MinigameBit are TEST-only today. | If MetronomeBit's feel on the fiber (its `primary` also lights the bundles) is wrong, give it fixture-scoped targets (`bars`) in a follow-up. |
| D10 | **No shipped Bit gains a `@fixture:` function in this slice.** A test-only Bit exercises `@fixture:bars` and `@fixture:fiber`. | `_bit_fixture_names` refuses to load a Bit that names a fixture the room lacks. A `@fixture:fiber` step in TestBit would break TestBit on TEST and DEMO. | Per-room function tables. They don't exist today. |

## 5. `rooms/VENUE.toml`

`<N>` is I1. Offsets are multiples of N. The file must be written with
literal integers, and §2's N = 1 rule applies until I1 lands.

```toml
description = "Terrarium venue: 6 m LED bars + 3 fiber-optic engines, two WLED controllers"
backends = ["devicelink", "array"]

[[fixtures]]
name = "bars"
# Art-Net wire order. WLED's own LED settings carry the strip's physical GRBW.
color_order = "RGBW"
instrument = "venue_array"
  # blocks m1..m6: identical to rooms/DEMO.toml's array (start 0,144,...,720; count 144)
  # zones left/center/right: identical to rooms/DEMO.toml (start 0,288,576; count 288)

[[fixtures]]
name = "fiber"
color_order = "RGBW"
instrument = "venue_fiber"
  [[fixtures.blocks]]
  name = "e1"
  start = 0
  count = <N>
  [[fixtures.blocks]]
  name = "e2"
  start = <N>
  count = <N>
  [[fixtures.blocks]]
  name = "e3"
  start = <2N>
  count = <N>
  [[fixtures.zones]]
  name = "b1"
  start = 0
  count = <N>
  [[fixtures.zones]]
  name = "b2"
  start = <N>
  count = <N>
  [[fixtures.zones]]
  name = "b3"
  start = <2N>
  count = <N>
```

Constraints the file must satisfy, all enforced by existing validation:

- Both fixtures are `RGBW`. A profile may not mix color orders
  (`control/room_profile.py`).
- Each block is at most 170 px, so N ≤ 170. Keeping the fiber fixture to
  one universe (3N ≤ 128) is not required. The sink spans universes as it
  does for bars.

## 6. `instruments/venue_fiber.toml`

```toml
description = "Fiber-optic end-glow engines, one colour per bundle"
capabilities = ["light.surface"]
accepted_cues = ["midi", "play", "solid", "mute"]
  [ambient]
  [ambient.light]
  instruments = [ { instrument = "aurora", target = "primary" } ]
```

- **The ambient is `aurora` on `primary`, a slow breathing hue, the same
  across all three bundles.** Per-bundle colour comes from a Bit's
  manifest targeting `fiber.b1`..`fiber.b3`. A per-bundle ambient (one
  `aurora` per zone with distinct `hue`) is a follow-up. It needs
  confirming that an instrument's own ambient may target its fixture's
  local zone names, and that is not needed for this slice.
- **No `[[functions]]`.** The built-ins `flash` and `stop` come free from
  `light.surface` (`control/builtins.py`). `play` is accepted because
  `tests/test_engine_functions.py`'s `test_shipped_instruments_accept_every_cue_kind`
  requires every shipped instrument to accept every cue kind; the fixture
  has no sample voice, so a `play` addressed to it does nothing.
- The "spore-like starfield" look in the hardware doc is creative work
  and not in scope. Any registered luxaeterna preset (`bloom`, `glow`,
  `aurora`, `rainbow`) can replace `aurora` later.

## 7. Art-Net wiring and power

Two commented entries replace the DEMO example in `terrarium.toml`. The
DEMO example stays as a separate block. The host is never a real IP.

```toml
# [[artnet]]
# room = "VENUE"
# fixture = "bars"
# host = "<WLED controller IP>"     # bars controller; 127.0.0.1 for artnet_listen
# start_universe = 0                # universes 0-6
# max_amps = <BARS_MAX_AMPS>        # spec 2026-09-25 section 7
# psu = "led12v"
#
# [[artnet]]
# room = "VENUE"
# fixture = "fiber"
# host = "<WLED controller IP>"     # fiber controller; 127.0.0.1 for artnet_listen
# start_universe = 0
# port = 6455                       # loopback only (second artnet_listen); omit on hardware
# max_amps = <FIBER_MAX_AMPS>
# amps_per_pixel_full = <FIBER_AMPS_PER_PIXEL>   # I2
# psu = "led12v"                    # only if I4 says the engines share it
#
# [psus.led12v]
# amps = <PSU_RATED_AMPS>           # I3
```

**Budget rule.** B = 0.8 × PSU rated amps (I3).

- **Fiber gets its full draw, so it never dims.**
  `FIBER_MAX_AMPS = 3 × N × FIBER_AMPS_PER_PIXEL`, from I1 and I2. At 5 W
  total for three engines (the BOM power line), this is about 0.4 A at 12 V.
- **Bars get the rest.** `BARS_MAX_AMPS = B − FIBER_MAX_AMPS` if the fiber
  engines share the PSU (I4), else `BARS_MAX_AMPS = B`. `bars` keeps the
  default `amps_per_pixel_full = 0.025`, so full white is
  864 × 0.025 = 21.6 A. The limiter always binds on bars at full white,
  under either candidate PSU.

Illustrative only, not to be committed. With N = 8, 0.025 A/px and a
shared PSU, fiber is 0.6 A. At 12.5 A the bars get 9.4 A; at 20 A they get
15.4 A.

## 8. PSU budget validation (`control/terrarium_config.py`)

- **`[psus.<name>]` holds `amps`**, a required positive number. The table
  is optional, and an unknown key is a located error, matching the
  existing `[[artnet]]` key check.
- **`[[artnet]]` gains an optional `psu`**, which must name a declared
  `[psus]` entry (located error otherwise). An output with no `psu` is
  not checked, so existing configs are unchanged.
- **Check.** For each PSU, the sum of `max_amps` over the outputs that name
  it must be ≤ 0.8 × `amps`. Otherwise it is a located error naming the
  PSU, the sum, the limit and each contributing `artnet[i]`.
- **Scope of the sum.** Outputs for different rooms may name the same PSU,
  and the sum spans all of them. Only one room loads at a time, but one
  box has one physical supply, and a stricter check costs nothing.
- `ArtNetOutput` gains `psu: str | None = None`, and `TerrariumConfig`
  gains `psus: dict[str, float]`. Neither is read at runtime; they exist
  for validation only.

## 9. Binding an Art-Net-covered fixture (dependency on PR #143)

This spec makes no binding change. Covered-fixture binding landed
separately in PR #143
([`2026-09-25-artnet-fixture-no-simulator-design.md`](2026-09-25-artnet-fixture-no-simulator-design.md)):
`Terrarium.load_room` passes the Room's `[[artnet]]`-covered fixtures
(`artnet_fixtures(config, room)` in `control/terrarium_config.py`) as
`skip`, so a covered fixture gets no simulator, is not reconnected to a
recorded binding, and is never armed or waited for. VENUE depends on that
behaviour.

The one VENUE-specific fact: both VENUE fixtures, `bars` and `fiber`, are
covered, so VENUE loads with nothing bound and no wait (G2).

Arming a covered fixture: this spec defers to the deep-dive's record in
`docs/MM_TERRARIUM.md` (the 2026-09-23 `[[artnet]]` entry). The Console's
`ArmRoomCommand` (`console/agent.py`) still arms any fixture name without
checking `[[artnet]]` coverage, so an operator who arms `bars` or `fiber`
and taps would give that fixture a `DeviceLinkSink` alongside its Art-Net
output, contrary to the parent spec's D2. That is a recorded gap: the
Console follow-up should refuse arming a fixture in
`artnet_fixtures(config, room)`. This slice does not implement the refusal.

## 10. Bits

- `bits/test/test_bit.py`: `room_types = {"TEST", "DEMO", "VENUE"}`. Its
  `primary` aurora binds on both `bars` and `fiber`. Its `flsyn` ugen
  manifest is satisfied by `bars` (aggregated requirements, §1).
- `bits/metronome/metronome_bit.py`: `room_types = {"DEMO", "VENUE"}`.
  The plan must first confirm it addresses the room only through
  `primary` and names no `array` fixture. If it does name `array`, D9's
  MetronomeBit half is dropped from this slice.
- **Addressing convention**, for Bit authors, recorded in the deep-dive:
  - Script steps, generator lanes and stream outputs use `@fixture:bars`
    or `@fixture:fiber`.
  - ROOM light-manifest targets are `bars`, `bars.left|center|right`,
    `fiber`, and `fiber.b1|b2|b3`.
  - A Bit that names either fixture can only list rooms that declare it.

## 11. Testing

All offline, per the existing suite rules
(`.venv/bin/python -m pytest tests -q`).

1. **Profile.** `VENUE` loads through the rooms catalog. It has two RGBW
   fixtures in order `bars`, `fiber`. `bars` is 864 px / 3456 ch with DEMO's
   blocks and zones. `fiber` is 3N px with aligned `e*` blocks and `b*`
   zones.
2. **Instrument.** `venue_fiber` loads. Its capabilities are
   `{"light.surface"}`. Its built-ins are `flash` and `stop` and not `ping`.
3. **Config.** The two §7 entries, uncommented with `127.0.0.1`, validate.
   Overlapping universes on one host:port are still refused. The `psu` sum
   is accepted at exactly 80 % and refused just above it. An unknown `psu`
   and an unknown `[psus]` key give located errors. An output with no
   `psu` is unchecked.
4. **Binding.** Covered-fixture binding is PR #143's, and its tests live
   with it in `tests/test_terrarium.py` (spawn, wait, mixed-room and
   all-Art-Net cases). This slice adds only a VENUE-shaped guard: with no
   `[[artnet]]` output and no simulator factory, a VENUE load still fails
   with "no device joined", so coverage never masks a Room nothing drives.
5. **Bits.** TestBit and MetronomeBit load on VENUE. A test-only Bit with
   `@fixture:fiber` and `fiber.b2` targets loads on VENUE and is refused
   on DEMO. `role_config.slice_light_manifest` binds a `fiber.b2`
   target to zone `b2` on `fiber` and drops it from `bars`. That slicing
   is what confines the cue to the middle bundle.
6. **End to end, no hardware.** Two `harness/artnet_listen.py` receivers
   run on loopback: `--pixels 864` on port 6454, and `--pixels <3N>
   --port 6455`. Both see frames from their own output, with 0 bad packets
   and 0 sequence gaps.

## 12. Bring-up additions

Append to the 2026-09-23 spec's §9 step 8 when VENUE is on hardware:

- Fiber WLED: SK6812 RGBW, Art-Net, DMX mode *Multi RGBW*, start
  universe 0, ABL on, boot preset off. Remove the loopback `port` line.
- Smoke without Control: `python -m harness.array_smoke --host <fiber
  controller IP> --pixels <3N>` shows no wrong colour order.
- Fire a SolidCue at `fiber.b1`, then `b2`, then `b3`. Each lights exactly
  one bundle, in order. This is the per-engine mapping check.
  (`--identify-blocks` in `harness/o2_shroom.py` only paints the WebSim
  canvas, so it confirms the profile, not the wiring.)
- Drive all-white on both fixtures at once and read the shared PSU with a
  clamp meter. It must stay ≤ B.

## 13. Dependencies and follow-ups

- **Depends on**, for operator use but not for this slice's tests: the
  Console fixture-targets work, landed in PR #144
  ([`2026-09-25-console-fixture-targets-design.md`](2026-09-25-console-fixture-targets-design.md)),
  which lets an operator target and see the mute state of unbound `bars`
  and `fiber` by name.
- **Independent:** `claude/tick-pacing`, since landed as PR #142.
- **Follow-ups:** white-aware `venue_array` and `venue_fiber` together, and
  an RGBW SolidCue; a starfield preset for the fiber ambient; fixing the
  PSU row in `MM_HARDWARE_DESIGN.md` once I3 is answered.

## 14. Implementation order (for writing-plans)

1. §9, binding of covered fixtures. Landed separately in PR #143
   ([`2026-09-25-artnet-fixture-no-simulator-design.md`](2026-09-25-artnet-fixture-no-simulator-design.md)),
   which also fixed PR #140's prerequisite for DEMO. VENUE depends on it;
   nothing to build here.
2. §8, PSU validation.
3. §6, `venue_fiber`, including the zone-target check.
4. §5, `rooms/VENUE.toml`, with N from I1 or N = 1 per §2.
5. §10, Bit `room_types` and the test-only fixture Bit.
6. §7, `terrarium.toml` comments, and the §11.6 end-to-end run.
7. Deep-dive sync: a new VENUE entry and the addressing convention. PR #143
   already closed the double-bind prerequisite in the 2026-09-23 entry.
