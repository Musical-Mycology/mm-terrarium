# Modelling a Shroom for the 3D view (Rhino-first)

A one-page brief for the artist modelling a Shroom (or any carried
instrument) for mm-tuneshroom's 3D look-dev view. The why is in
mm-tuneshroom's `docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md`;
everything needed to build the file is here.

## Origin and units

Model in any Rhino unit; the exporter converts to metres. Put the centre
of the Shroom's base at the world origin, with the Shroom standing up the
Rhino Z axis. A Room fixture (the Tower, for example) puts its origin at
ground center instead, as its drawing does.

## LED markers

- One **sphere** per physical LED, centred where the LED's emitting face sits.
- Object name `LED_000`, `LED_001`, ... Three digits, zero-padded. **The
  number is the LED's frame index**: for pixels on a data chain that is the
  position on the chain (`LED_000` is the first pixel after the controller);
  a light the firmware drives another way (the Tower's PARs, over DMX) still
  takes its frame index. Indices run 0 to N-1 with no gaps and no
  duplicates, where N is the instrument's pixel count.
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
