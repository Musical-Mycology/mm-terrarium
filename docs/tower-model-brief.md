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
exactly as below. Positions are nominal: keep each indicator within 5 mm of
its Z, and each responder and PAR within 25 mm of its X and Z. Depth (Y) is
yours: put each sphere on the face where the light actually comes out.

| Sphere | Layer | Light | X mm | Z mm | Sphere diameter |
|---|---|---|---|---|---|
| `LED_000` | `LEDs::progress` | indicator stage 1/8 | 0 | 480 | 5 mm |
| `LED_001` | `LEDs::progress` | indicator stage 2/8 | 0 | 632 | 5 mm |
| `LED_002` | `LEDs::progress` | indicator stage 3/8 | 0 | 785 | 5 mm |
| `LED_003` | `LEDs::progress` | indicator stage 4/8 | 0 | 937 | 5 mm |
| `LED_004` | `LEDs::progress` | indicator stage 5/8 | 0 | 1089 | 5 mm |
| `LED_005` | `LEDs::progress` | indicator stage 6/8 | 0 | 1242 | 5 mm |
| `LED_006` | `LEDs::progress` | indicator stage 7/8 | 0 | 1394 | 5 mm |
| `LED_007` | `LEDs::progress` | indicator stage 8/8 | 0 | 1547 | 5 mm |
| `LED_008` | `LEDs::responder` | responder R1, lower left | -330 | 900 | 5 mm |
| `LED_009` | `LEDs::responder` | responder R2, lower right | 315 | 1045 | 5 mm |
| `LED_010` | `LEDs::responder` | responder R3, upper left | -330 | 1370 | 5 mm |
| `LED_011` | `LEDs::responder` | responder R4, upper right | 310 | 1510 | 5 mm |
| `LED_012` | `LEDs::beat` | PAR light, left | -80 | 90 | 10 mm |
| `LED_013` | `LEDs::beat` | PAR light, right | 80 | 90 | 10 mm |

- Exactly these 14 names, no others under `LEDs`, no `primary` layer.
- The indicators sit on one straight vertical line (X = 0): the factory chain's
  152.4 mm pitch cannot reach the zig-zag the elevation sketches.
- Sphere diameter sets the glow size band (5 mm is `medium`, 10 mm is `large`).

## The rest of the model

Mast, mushroom stages with their silicone diffusers, the four 190 mm
responder discs, the crown and the maitake base, with PBR materials as the
artist guide describes (translucent parts get transmission; an opaque material
around a light bakes dark). The crown has no light source in this revision.

## Delivering

One `.glb`, exported with the artist guide's settings (layers on, Rhino Z to
glTF Y on, Draco off). Send it to Chris.

## Importing the file (maintainers only; artists can skip this)

1. Save it as `instruments/models/tower.glb`.
2. Add `model = "models/tower.glb"` to `instruments/tower.toml` (same commit).
3. Run `.venv/bin/python -m pytest tests -q`. The catalog refuses a wrong
   sphere name or count, and the room check refuses a sphere whose layer
   does not match the `TOWER` room's zones, naming the sphere (for example
   `LED_012`). Sphere size is never refused; it only sets the glow band, so
   check it against the table above.
4. Bake on Mycologist and export to mm-tuneshroom as `docs/MM_TERRARIUM.md`,
   *LED layout models*, describes.
