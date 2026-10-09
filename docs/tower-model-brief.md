# Tower model brief

What the Tower's 3D model needs, for the artist. The layout contract is
`docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md`; the general
modelling rules are `docs/instrument-model-guide.md`. This page is everything
specific to the Tower.

## Origin, units and axes

Model the whole 2100 mm fixture as drawn in "Fixture Tower - 2100 mm
Dimension Specification": ground center at the world origin, the Tower
standing up the Z axis, the drawing's front elevation facing the viewer (the
drawing's X is the model's X, viewer's right positive). Any unit is fine; the
exporter converts.

## LED markers: 14 spheres

One sphere per light, centred where that light emits, named and layered
exactly as below. The positions are the artist's own first model
(2026-10-05), which is now the reference for where the lights go; the two
PARs, which that model lacked, keep the drawing's positions. Keep each
sphere within about 25 mm of its X and Z. Depth (Y) is yours: put each
sphere on the face where the light actually comes out.

| Sphere | Layer | Light | X mm | Z mm | Sphere diameter |
|---|---|---|---|---|---|
| `LED_000` | `LEDs::progress` | indicator stage 1/8 | 44 | 482 | 5 mm |
| `LED_001` | `LEDs::progress` | indicator stage 2/8 | 34 | 632 | 5 mm |
| `LED_002` | `LEDs::progress` | indicator stage 3/8 | -20 | 791 | 5 mm |
| `LED_003` | `LEDs::progress` | indicator stage 4/8 | -29 | 937 | 5 mm |
| `LED_004` | `LEDs::progress` | indicator stage 5/8 | 50 | 1089 | 5 mm |
| `LED_005` | `LEDs::progress` | indicator stage 6/8 | 36 | 1242 | 5 mm |
| `LED_006` | `LEDs::progress` | indicator stage 7/8 | -25 | 1394 | 5 mm |
| `LED_007` | `LEDs::progress` | indicator stage 8/8 | 45 | 1547 | 5 mm |
| `LED_008` | `LEDs::responder` | responder R1, lower left | -244 | 1000 | 5 mm |
| `LED_009` | `LEDs::responder` | responder R2, lower right | 255 | 1250 | 5 mm |
| `LED_010` | `LEDs::responder` | responder R3, upper left | -251 | 1500 | 5 mm |
| `LED_011` | `LEDs::responder` | responder R4, upper right | 239 | 1750 | 5 mm |
| `LED_012` | `LEDs::beat` | PAR light, left | -80 | 90 | 10 mm |
| `LED_013` | `LEDs::beat` | PAR light, right | 80 | 90 | 10 mm |

- Exactly these 14 names, no others under `LEDs`, no `primary` layer.
- The indicators zig-zag up the vine, up to 50 mm either side of center, as
  in the first model.
- Sphere diameter sets the glow size band (5 mm is `medium`, 10 mm is `large`).

## The rest of the model

Mast, mushroom stages with their silicone diffusers, the four 190 mm
responder discs, the crown and the maitake base, with PBR materials as the
artist guide describes (translucent parts get transmission; an opaque material
around a light bakes dark). The crown has no light source in this revision.

## Delivering

One `.glb`, exported with the artist guide's settings (layers on, Rhino Z to
glTF Y on, Draco off). Before exporting, work through the guide's *Keep the
geometry light* steps: the whole Tower should come in under 300,000
triangles and 20 MB. Then run the guide's *Check the file before sending
it*. Send it to Chris.

What went wrong with the first export (2026-10-05), so the next one avoids it:

- **1.18 GB, 39 million triangles.** 42 copies of one small part (about
  26 x 26 x 5 mm, in 7 groups of 6 on the front face) carried 925,000
  triangles each, 99% of the file. Everything else together was about
  400,000. Steps 2 to 5 of *Keep the geometry light* cover the likely
  causes; `PolygonCount` on one copy shows which.
- **No layers.** The file was a flat list of objects with no `LEDs` node,
  so the catalog cannot read a single marker. *Export Layers* must be on.
- **`LED_012` and `LED_013` missing.** The two PAR markers on
  `LEDs::beat`.
- **20 mm spheres.** Every marker was 20 mm, which reads as the `large`
  glow band; use the diameters in the table above.

## Importing the file (maintainers only; artists can skip this)

1. Check its size first: over 20 MB, send it back with the guide's *Keep
   the geometry light* steps rather than committing it (git refuses files
   over 100 MB). Then save it as `instruments/models/tower.glb`.
2. Add `model = "models/tower.glb"` to `instruments/tower.toml` (same commit).
3. Run `.venv/bin/python -m pytest tests -q`. The catalog refuses a wrong
   sphere name or count, and the room check refuses a sphere whose layer
   does not match the `TOWER` room's zones, naming the sphere (for example
   `LED_012`). Sphere size is never refused; it only sets the glow band, so
   check it against the table above.
4. Bake on Mycologist and export to mm-tuneshroom as `docs/MM_TERRARIUM.md`,
   *LED layout models*, describes.
