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
