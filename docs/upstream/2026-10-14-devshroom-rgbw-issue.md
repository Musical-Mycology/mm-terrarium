# mm-devshroom: decode the contract v5 RGBW pixel wire

mm-terrarium contract v5 (on branch `claude/rgbw-device-wire`, not yet merged) changes every `/<dev>/leds` frame
to R, G, B, W, 4 bytes per pixel: 48 bytes for a 12 px device. A Room-bound
device's frame is its fixture's pixel count x 4. Spec:
mm-terrarium `docs/superpowers/specs/2026-10-14-rgbw-device-wire-design.md`.
Guide: `docs/device-contract-guide.md` (the `/leds` row, rules 8, 9 and 11).

Firmware today (origin/main 0225e78):
- `src/o2_test/hardware_control/leds.cpp:39-54` `show_rgb` reads 3-byte
  triplets and draws any length. On a v5 Terrarium every colour is wrong.

Do this:
1. Decode `/leds` as R, G, B, W, 4 per pixel. Drop a blob whose size is not
   exactly the device's declared pixel count x 4 (rule 8); never truncate.
   `testshroom` declares 12 px, so expect exactly 48 bytes. The dev board has
   6 LEDs (`LED_COUNT = 6`, `include/hardware_config.h`), so draw the first 6
   pixels and ignore the rest; this is not truncation, the frame is still
   validated at 48.
2. The dev board's NeoPixels have no white die: show each pixel as
   `min(255, r + w)`, `min(255, g + w)`, `min(255, b + w)` (rule 11).
3. Keep `NEO_GRB` as the driver order. The wire order is now independent of
   the strip's physical order.
4. The Tower's pixels are three-core too, so they need the same fold.
5. A Rev 1 RGBW board drives the die directly (`NEO_GRBW` or the P4 driver's
   equivalent), with no fold.
6. Replay contract export v5.

The lexicon's whites (the invite flash, the Looking pulse) now arrive as
W only, `0,0,0,255`, so without step 2 they show black.
