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

## Keep the geometry light

The file ships inside the Tuneshroom app and is drawn on a phone, the light
maps are baked on a CPU-only machine, and git refuses any file over 100 MB.
The model is a look-dev view of where light comes out, not a manufacturing
model, so its meshes only need to read well at arm's length on a screen.

**Budget** for the exported `.glb`:

| | Limit |
|---|---|
| Whole model | 300,000 triangles, 20 MB |
| Any one part | 20,000 triangles |
| A small part (under 50 mm), each copy | 2,000 triangles |

A first Tower export came in at 39 million triangles and 1.18 GB: 99% of it
was 42 copies of one 26 mm part at 925,000 triangles each. The steps below
exist to catch exactly that.

**Before exporting, in Rhino:**

1. **Coarsen the render mesh.** Surfaces export at the document's render
   mesh density, not at a setting in the export dialog. Open *File >
   Properties > Mesh* (on Mac, *Rhino > Settings > Mesh*), choose *Custom*,
   and start from: *Maximum angle* 20, *Maximum distance, edge to surface*
   0.5 mm, *Minimum edge length* 0.5 mm, *Refine mesh* off. Curved silicone
   still reads smooth at these values; flat parts drop to a handful of
   triangles.
2. **Clear per-object overrides.** An object can carry its own finer mesh
   settings that ignore step 1: select all, open *Properties > Render Mesh
   Settings*, and turn *Custom Mesh* off unless a part genuinely needs it.
3. **Turn off render mesh modifiers.** *Displacement*, *Edge Softening*,
   *Shut Lining*, *Thickening* and *Curve Piping* (Object Properties) each
   generate dense geometry at export. Displacement alone can turn a
   coin-sized part into a million triangles. Paint surface texture with the
   material instead.
4. **Reduce parts that are already meshes.** Imported STL/OBJ parts,
   scans and vendor models keep their own density whatever step 1 says.
   Run `ReduceMesh` on each, with *Reduce to* set to the budget above.
5. **Simplify or drop small repeated hardware.** Screws, nuts, standoffs,
   connectors and decorative studs are rarely visible in the view. Drop
   them, or model one simple copy (a cylinder is fine) and repeat that.
6. **Count it.** Select everything you will export and run
   `PolygonCount`: it reports the triangles and quads Rhino will write
   (count a quad as two triangles). Select any part that looks large on its
   own and check it against the per-part budget. Over budget: go back to
   step 1 to 5 for the parts responsible.

## Export (File > Export Selected or Save As, `.glb`)

- *Export Layers*: **on** (zones depend on it).
- *Map Rhino Z to glTF Y*: **on**.
- *Use Draco compression*: **off** (the catalog refuses Draco). Draco only
  shrinks the file; it would not fix a dense mesh anyway.
- *Export texture coordinates* and *Export vertex normals*: on.
- *SubD Meshing* (only if the model has SubD objects): keep *Subdivision
  level* at 1 or 2, or tick *Use control net* if the cage alone looks right.
  Each level up multiplies those parts' triangles by about four.

**Check the file before sending it:**

- **Size.** Over 20 MB means something is still too dense; go back to
  *Keep the geometry light*.
- **Layers.** Import the `.glb` into an empty Blender scene (*File >
  Import > glTF 2.0*; Blender is free) and look at the *Outliner*: the
  markers must sit under an object named `LEDs`, inside their zone
  sublayer. A flat list of objects means *Export Layers* was off.
- **Markers.** Every `LED_###` from 0 to N-1 is present, none missing.

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
