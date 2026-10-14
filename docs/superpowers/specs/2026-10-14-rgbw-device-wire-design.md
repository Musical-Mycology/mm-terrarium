# RGBW device wire (contract v5)

**Date:** 2026-10-14
**Status:** Design approved in chat with Chris, 2026-10-14. Awaiting spec review.
**Repos:** mm-terrarium (the contract owner; this spec), luxaeterna,
mm-tuneshroom, and an issue for mm-devshroom.
**Purpose:**
- Every pixel frame on the wire becomes R, G, B, W.
- The Rev 1 Tuneshroom's white die can finally be lit.
- One frame layout replaces the current mix of GRB player frames and
  per-fixture channel orders.

## 1. Decisions (Chris, 2026-10-14)

1. **RGB order, not GRB.** GRB has no recorded rationale. It entered with
   luxaeterna's `shroom_capability()` (`luxaeterna/synth/capability.py:189-194`,
   commit b0e1308, 2026-07-21) and became the player wire by default.
2. **A white channel, fixed for every device.** Every pixel is 4 bytes,
   R, G, B, W. A device without a white die folds W into R, G and B itself.
   There is no per-instrument layout.
3. **One wire for every pixel sink.** Player devices, o2lite-bound Room
   fixtures (the TEST dev strips, the Tower) and Art-Net (already RGBW) all
   carry RGBW. `color_order` is retired from mm-terrarium's Room config.
4. **W is authored, never derived.** RGB content keeps W = 0. The light
   lexicon's white signals drive the W die. There is no automatic white
   extraction.

The white die was deliberately left dark until now. The 2026-08-19 decision,
"W always 0 ... no widening", is in
`docs/superpowers/plans/2026-09-11-student-hardware-track-esp32.md:60-62`,
and the open decision is in `docs/MM_TERRARIUM.md` *Not yet built*
("The Tuneshroom LED wire cannot reach the white die"). This spec closes
both.

## 2. Hardware this has to serve

| Device | LEDs | White die | Source |
|---|---|---|---|
| Rev 1 Tuneshroom / ESP32-P4 instrument | 12x SK6812 Mini RGBW | yes | mm-documents `MM_HARDWARE_DESIGN.md:123,234`; mm-tuneshroom `docs/shroom-hardware-spec.md:60-63` |
| mm-devshroom dev board | 6 NeoPixels, driven `NEO_GRB` | no | mm-devshroom `include/hardware_config.h:69-71`, `src/o2_test/hardware_control/leds.cpp:14` (origin/main 0225e78) |
| Tower fixture | 8 single 5 V pixels + 4 responders + 2 PARs, three-core | no | `docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md:22-23,87-88` |
| Venue array, fiber, bars (WLED, Art-Net) | SK6812 RGBW, physical GRBW in WLED | yes | `rooms/DEMO.toml:6-7`, `rooms/VENUE.toml:6-7` |

## 3. The wire (contract v5)

### 3.1 Frame layout

- A pixel frame is `pixel_count x 4` bytes in the order **R, G, B, W**, one
  byte each, 0 to 255.
- A 12 px player frame is 48 bytes. The Tower's 14 px frame is 56 bytes.
  A Room fixture's frame is its pixel count x 4.
- This applies to `/<dev>/leds` for player devices and for Room-bound devices
  alike, to every `FixtureSink`, and to Art-Net. Art-Net is already RGBW at
  128 px per universe (`devicelink/artnet_sink.py:34`).
- The physical strip order (GRB, GRBW, RGB) is a driver setting: a firmware
  constant, or WLED's LED settings. It is never on the wire.

### 3.2 The fold (devices with no white die)

A device whose LEDs have no white die shows each pixel as:

```
r' = min(255, r + w)
g' = min(255, g + w)
b' = min(255, b + w)
```

This is the rule the Console decoder already applies to RGBW fixtures
(`console/static/surface.js:719-741`). Every screen renderer uses the same
fold: the Console, the WebSim, and the mm-tuneshroom sim. A screen therefore
shows exactly what an RGB-only board shows.

### 3.3 Validation

Guide rule 8 keeps its meaning at the new width: a device drops a `/leds` blob
that is not exactly its pixel count x 4, and never truncates. A 36-byte
(v4) frame arriving at a v5 device is dropped.

### 3.4 Version

- `CONTRACT_VERSION = 5` (`tools/export_contract.py:70`). A device can
  observe this change, so it needs a bump under the rule in the comment at
  `:55-69`.
- **This is a hard cut. There is no negotiation**, consistent with the
  contract kit's ruling (`docs/superpowers/specs/2026-09-16-device-contract-kit-design.md:60`).
- A device still on v4 or earlier shows wrong colours until it updates.
  Today that is the dev board, and the Rev 1 firmware, which is still on v2
  and joining with jam roles. Section 7 covers the rollout.

## 4. Where W comes from

- **luxaeterna's canonical surface is already RGBW** (`_CANON` at
  `luxaeterna/synth/engine.py:10`). An RGB colour on an RGBW surface pads
  W = 0 (`luxaeterna/synth/ugens.py:207-211`). That stays: existing looks
  render with W = 0 and are byte-identical apart from the reorder.
- **Solid colours gain W.** The override colour Control paints with becomes
  an RGBW tuple:
  - `SolidCue` and the lobby's `rgb` overrides are affected
    (`devicelink/agent.py` `_apply_override` / `_solid_frame` at
    `:932-953`, and `control/lobby.py`).
  - A `SolidCue` colour of three values gets W = 0.
  - The new `_solid_frame` writes R, G, B, W with no order parameter.
- **The lexicon's whites use the die.**
  - `control/lobby.py:59` `WHITE = (255, 255, 255)` becomes
    `(0, 0, 0, 255)`, so the white invite flash, the white "invited" status
    pulse and any other reserved white signal light W only.
  - Green, red and the other signal colours are unchanged, with W = 0.
  - The device-drawn **Looking** pulse (guide rule 9, mm-tuneshroom
    `lib/render/looking.dart`) draws on W.
  - On RGB-only hardware the fold shows all of these white, as today.
- `docs/light-lexicon.md` gains a section 2 note: "white" in this lexicon
  means the W channel at the stated level, and an RGB-only device shows it
  through the fold.

## 5. Changes by repo

### 5.1 luxaeterna

- `shroom_capability()` (`luxaeterna/synth/capability.py:189-194`):
  `color_order="RGBW"`, and the docstring and README (`README.md:23`) say so.
- The library keeps `SurfaceCapability.color_order` and
  `to_dmx_bytes(frame, color_order)`. They are general mechanisms that its
  DMX fixture profiles (`luxaeterna/fixture.py:165-180`) and the WebSim
  rely on.
- `docs/deployment.md:136-160` drops "still open for the Tuneshroom".

### 5.2 mm-terrarium

| Area | Change |
|---|---|
| `devicelink/agent.py` | `_DEVICE_CHANNELS = 48` (12 px x 4). Remove the `"GRB"` defaults and the `color_order` parameters (`:932-953`, `:1179-1180`). Every capability the agent builds is RGBW. |
| `devicelink/protocol.py:177-190` | The `leds_event` docstring states RGBW, pixel count x 4. |
| `devicelink/o2_transport.py:82`, `harness/beat_tapper.py:35` | Comment widths. |
| `control/room_profile.py:80-83,136-144,210-219` | `RoomFixture` loses `color_order`. `channels` is always 4. A room TOML that still sets `color_order` fails to load with a located error naming the key and saying to delete it. Mixed orders no longer exist. |
| `control/terrarium_config.py:740-768` | The "Art-Net needs an RGBW fixture" refusal goes, because every fixture is RGBW. `_PIXELS_PER_RGBW_UNIVERSE` stays. |
| `control/room_view.py:60,138`, `console/static/rooms.js:110` | Stop passing and showing `color_order`. |
| `rooms/*.toml` | Delete every `color_order` line: TEST, TOWER, DEMO, VENUE. WLED's own GRBW setting is unchanged. |
| `terrarium.toml:19` | Drop the "must be RGBW" comment. |
| `control/lobby.py:59` and the lobby/override path | `WHITE` becomes RGBW `(0,0,0,255)`; green, red and the other signal colours keep three values (W = 0). `SolidCue.rgb` accepts (r, g, b) or (r, g, b, w); three values mean W = 0. |
| `harness/shroom_client.py:36,61-67,90,108-113` | `LED_CHANNELS = 48`. Decode R, G, B, W. Drop the "white die unreachable / Task B7" comment. |
| `harness/o2_shroom.py:455-470`, `harness/websim_leds.py:15-49`, `harness/design_session.py`, `harness/device_bridge.py`, `harness/room_surface.py` | Width is pixel count x 4 everywhere; no order. |
| `console/static/surface.js:719-741`, `console/static/design.js:182-195` | A single RGBW decoder with the fold (section 3.2). `design.js`'s hardcoded GRB goes. |
| `contract_kit/scenarios.py:116-120,343-349,445-447` | The authored frames become RGBW (`AUTHORED_NEWER_RGBW`, `AUTHORED_OLDER_RGBW`, 48 values). |
| `contract_kit/recorder.py:770,796` | It writes `expect_frame: {"rgbw": [...]}`. Re-record all 22 scenarios; 14 carry frames. |
| `tools/export_contract.py` | `CONTRACT_VERSION = 5`. Version history line: "v5: RGBW pixel frames, 4 per pixel". The `/leds` args and `expect_frame.rgbw` descriptions change (`:233-239`, `:333-338`, `:448-450`). |
| `docs/device-contract-guide.md` | The `/leds` row (`:212`), `expect_frame` (`:116`, `:158`), rule 8 at 4 bytes, rule 9 Looking on W, a new rule with the fold, and firmware checklist items. |
| `docs/light-lexicon.md` | The white = W note (section 4). |
| `docs/MM_TERRARIUM.md` | Remove the white-die open decision from *Not yet built*. Update the Room colour-order text (`:1158-1173`), the Art-Net RGBW-only note, the `design.js` GRB note (`:2077-2078`), the message table (`:1481`), and the contract status line. |
| `tests/` | Convert every GRB frame fixture and `color_order` fixture. Pin the 48-byte width, the RGBW reorder-free `_solid_frame`, the lobby white as W, the `color_order` load error, and the fold in the JS decoders. |

### 5.3 mm-tuneshroom

- `lib/render/frame.dart`: `GrbFrame` becomes `RgbwFrame`. `blackFrame` and
  `decodeWireFrame` work at `pixelCount * 4`.
- The renderers write R, G, B, W at `i * 4`: `ambient.dart`,
  `cue_player.dart` and `looking.dart` (Looking on W).
- `lib/host/device_session.dart` frame fields follow.
- `lib/sim/sim_controller.dart` `_colorsFromGrb` becomes `_colorsFromRgbw`
  with the fold.
- Contract copy: `test/contract/contract.json` and the scenarios are
  re-exported at v5. The version pins at `test/contract_replay_test.dart:154-156`
  and `test/contract_export_test.dart:39` become 5, and `expect_frame.rgbw`
  is compared.
- The legacy M1a stack (`lib/core/`, `lib/ui/`) is untouched.
- The devshroom sim profile work (branch `claude/devshroom-sim-profile`)
  rebases onto this. Its spec's D2 (red/green swap) is superseded.

### 5.4 mm-devshroom (an issue for Victor, not code from us)

- Decode `/leds` as R, G, B, W at pixel count x 4, and drop any other size.
- Fold W into R, G, B for the dev board's NeoPixels (and the Tower's
  pixels).
- Keep `NEO_GRB` as the driver order.
- Replay contract export v5.
- This replaces the red/green swap item in the devshroom defect list. A
  Rev 1 RGBW board drives the die directly (`NEO_GRBW`, or the P4 driver's
  equivalent).

## 6. Testing

- **mm-terrarium:** `.venv/bin/python -m pytest tests -v` and
  `node --test tests/js/*.test.js` green. New tests:
  - `_solid_frame` emits R,G,B,W.
  - Lobby white reaches a device as `[0,0,0,255]` per pixel.
  - Player frames are 48 bytes.
  - A room TOML with `color_order` fails with a located error.
  - The Console decoder folds W.
  - The Testshroom drops a 36-byte frame.
  - The contract export reports 5 and every `expect_frame` has 48 `rgbw`
    values.
- **luxaeterna:** its suite green, and `shroom_capability().color_order == "RGBW"`.
- **mm-tuneshroom:** `flutter test` green, including the v5 replay.
- **Live check:** `./terrarium.sh --room TEST` with one Testshroom, plus the
  mm-tuneshroom browser sim against it.
  - The white invite flash arrives as W-only frames and shows white on both.
  - A Bit's coloured frame shows the right hue, which proves R and G are no
    longer swapped.
  - The Console strip matches.

## 7. Rollout and order

1. luxaeterna first: `shroom_capability` is the source of the player layout.
   mm-terrarium pins luxaeterna as a sibling dev dependency, so both land
   together.
2. Then mm-terrarium: the contract, Control, the harness, the Console and
   docs, with the export at v5.
3. Then mm-tuneshroom: re-export and replay v5.
4. File the mm-devshroom issue when mm-terrarium merges. Until Victor lands
   it, the dev board shows wrong colours on a v5 Terrarium. That is accepted
   under the hard cut (section 3.4).

## 8. Out of scope

- Automatic white extraction (decision 4).
- Per-instrument channel layouts (decision 2).
- Any change to WLED configuration. The physical GRBW stays in WLED.
- Colour calibration of the white die against mixed RGB white.
