# tools/bake_model.py
"""Bakes an artist-authored .glb's per-LED light maps in headless
Blender (spec: mm-tuneshroom docs/superpowers/specs/
2026-09-28-3d-tuneshroom-model-and-view-design.md, section 5.3, as
amended in docs/superpowers/plans/2026-09-28-3d-model-t2-bake-export.md).

    Blender -b --factory-startup --python-exit-code 1 -P tools/bake_model.py -- \
        <model.glb> [--resolution 1024] [--samples 128] [--out PATH]

Writes <model dir>/<model stem>.baked.glb unless --out is given. Every
piece of logic that does not need bpy lives in tools/model_bake_helpers.py
and control/model_layout.py (pytest-tested); this file only drives bpy.
bpy calls verified against Blender 4.5 by tools/blender_probe.py on the
bake host (see docs/MM_TERRARIUM.md, LED layout models).
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.model_layout import ModelLayoutError, parse_source_layout  # noqa: E402
from tools.model_bake_helpers import (  # noqa: E402
    BakeContractError, BakeError, bake_output_path, build_mm_bake_extras,
    check_blender_version, encode_png_rgba8, flip_rows, group_leds_by_four,
    inject_bake, layout_to_blender_m, normalise_maps, quantise_group_rgba8,
)

PINNED_BLENDER = "4.5"   # confirmed on the bake host by tools/blender_probe.py
DEFAULT_RESOLUTION = 1024
DEFAULT_SAMPLES = 128
BAKE_MARGIN_PX = 4
EMITTER_RADIUS_M = 0.001
EMITTER_WATTS = 1.0      # arbitrary: every map is normalised afterwards
LIGHTMAP_UV = "lightmap"
DEFAULT_MATERIAL = "mm_bake_default_translucent"


def _parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="bake_model.py")
    parser.add_argument("model", type=Path)
    parser.add_argument("--resolution", type=int, default=DEFAULT_RESOLUTION)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def _select_only(objs: list) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def _remove_objects(objs: list) -> None:
    for obj in objs:
        bpy.data.objects.remove(obj, do_unlink=True)


def _markers_subtree() -> list:
    """The imported LEDs empty and everything under it. Only deleted and
    counted here; positions come from the source file's own parse (D13)."""
    leds = bpy.data.objects.get("LEDs")
    if leds is None:
        raise BakeError("the imported scene has no 'LEDs' object")
    return [leds, *leds.children_recursive]


def _prepare_meshes(targets: list) -> None:
    """Single-user mesh data (instanced meshes would overlap in the
    shared atlas), UV set 0 kept (a plain one added if absent), any other
    set dropped, and a fresh 'lightmap' set 1 (spec section 4.1)."""
    _select_only(targets)
    bpy.ops.object.make_single_user(type="SELECTED_OBJECTS", object=True, obdata=True)
    for obj in targets:
        uvs = obj.data.uv_layers
        if len(uvs) == 0:
            uvs.new(name="UVMap")
        while len(uvs) > 1:
            extra = uvs[len(uvs) - 1]
            print(f"bake_model: {obj.name}: dropping extra UV set {extra.name!r}")
            uvs.remove(extra)
        uvs.new(name=LIGHTMAP_UV)


def _lightmap_pack(targets: list) -> None:
    """All bake targets packed together into one shared atlas (spec 5.3 step 2)."""
    _select_only(targets)
    for obj in targets:
        obj.data.uv_layers.active = obj.data.uv_layers[LIGHTMAP_UV]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.lightmap_pack(PREF_CONTEXT="ALL_FACES", PREF_PACK_IN_ONE=True,
                             PREF_NEW_UVLAYER=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def _default_material():
    mat = bpy.data.materials.get(DEFAULT_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(DEFAULT_MATERIAL)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Transmission Weight"].default_value = 1.0
        bsdf.inputs["Roughness"].default_value = 0.5
    return mat


def _ensure_materials(targets: list) -> list:
    """Every face needs a node material to bake into: material-less
    meshes and empty slots get the default translucent stand-in (plan
    decision P3). Returns the unique materials in use."""
    for obj in targets:
        if len(obj.material_slots) == 0:
            obj.data.materials.append(_default_material())
        for slot in obj.material_slots:
            if slot.material is None:
                slot.material = _default_material()
            if not slot.material.use_nodes:
                slot.material.use_nodes = True
    return list({slot.material.name: slot.material
                 for obj in targets for slot in obj.material_slots}.values())


def _configure_scene(samples: int) -> None:
    """World black with no other lights (spec 5.3 step 3), Cycles on CPU
    (the bake host has no supported GPU)."""
    scene = bpy.context.scene
    world = bpy.data.worlds.new("mm_bake_world")
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    background.inputs["Strength"].default_value = 0.0
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples


def _add_bake_nodes(materials: list, resolution: int) -> tuple:
    """One float image, one active Image Texture node per material
    pointing at it (the node a Cycles bake writes into)."""
    image = bpy.data.images.new("mm_bake_target", width=resolution, height=resolution,
                                alpha=False, float_buffer=True)
    nodes = []
    for mat in materials:
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.select = True
        mat.node_tree.nodes.active = node
        nodes.append((mat, node))
    return image, nodes


def _bake_pass(kind: str, image, resolution: int) -> np.ndarray:
    bpy.ops.object.bake(type=kind, pass_filter={"DIRECT", "INDIRECT"},
                        use_clear=True, margin=BAKE_MARGIN_PX, target="IMAGE_TEXTURES")
    flat = np.empty(resolution * resolution * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    return flat[0::4].astype(np.float64)   # colour excluded, so R == G == B


def _bake_led(center_m: tuple, image, resolution: int) -> list:
    """Spec 5.3 step 3: a small unit-white spherical emitter (a point
    light with a radius) at the LED; Diffuse and Transmission baked into
    the image in turn (a bake overwrites what it covers), summed, and
    row-flipped to top-down order."""
    light = bpy.data.lights.new("mm_bake_led", type="POINT")
    light.energy = EMITTER_WATTS
    light.shadow_soft_size = EMITTER_RADIUS_M
    emitter = bpy.data.objects.new("mm_bake_led", light)
    bpy.context.scene.collection.objects.link(emitter)
    emitter.location = center_m
    try:
        total = _bake_pass("DIFFUSE", image, resolution) + _bake_pass(
            "TRANSMISSION", image, resolution)
    finally:
        bpy.data.objects.remove(emitter, do_unlink=True)
        bpy.data.lights.remove(light)
    return flip_rows(total.tolist(), resolution, resolution)


def _export(targets: list, image, nodes: list, path: Path) -> bytes:
    """Bake nodes and image removed first (so nothing but the model is
    exported), UV set 0 active and render-active so the artist's UVs stay
    TEXCOORD_0 and 'lightmap' becomes TEXCOORD_1 (confirmed by the probe)."""
    for mat, node in nodes:
        mat.node_tree.nodes.remove(node)
    bpy.data.images.remove(image)
    for obj in targets:
        uvs = obj.data.uv_layers
        uvs.active = uvs[0]
        uvs[0].active_render = True
    bpy.ops.export_scene.gltf(
        filepath=str(path), export_format="GLB", use_selection=False,
        export_yup=True, export_texcoords=True, export_normals=True,
        export_extras=False, export_lights=False, export_cameras=False)
    return path.read_bytes()


def main() -> None:
    args = _parse_args()
    blender = check_blender_version(tuple(bpy.app.version), PINNED_BLENDER)
    model_path = args.model.resolve()
    layout = parse_source_layout(model_path.read_bytes(), path=str(model_path))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(model_path))
    markers = _markers_subtree()
    marker_meshes = [o for o in markers if o.type == "MESH"]
    if len(marker_meshes) != len(layout.pixels):
        raise BakeError(f"Blender imported {len(marker_meshes)} marker meshes, "
                        f"the source parse found {len(layout.pixels)}")
    _remove_objects(markers)
    _remove_objects([o for o in bpy.data.objects if o.type in {"LIGHT", "CAMERA"}])
    targets = [o for o in bpy.data.objects if o.type == "MESH"]
    if not targets:
        raise BakeError("no non-marker mesh to bake onto")

    _prepare_meshes(targets)
    _lightmap_pack(targets)
    materials = _ensure_materials(targets)
    _configure_scene(args.samples)
    image, nodes = _add_bake_nodes(materials, args.resolution)
    _select_only(targets)

    raw = {}
    for pixel in layout.pixels:
        raw[pixel.index] = _bake_led(layout_to_blender_m(pixel), image, args.resolution)
        print(f"bake_model: LED {pixel.index + 1}/{len(layout.pixels)} baked", flush=True)

    normalised, map_scale = normalise_maps(raw)
    texels = args.resolution * args.resolution
    pngs = [encode_png_rgba8(args.resolution, args.resolution,
                             quantise_group_rgba8(group, normalised, texels))
            for group in group_leds_by_four(len(layout.pixels))]

    with tempfile.TemporaryDirectory() as tmp:
        exported = _export(targets, image, nodes, Path(tmp) / "export.glb")
    mm_bake = build_mm_bake_extras(
        source_sha256=layout.model_sha256, layout_pixels=layout.pixels,
        map_scale=map_scale, resolution=args.resolution, blender_version=blender)
    out = args.out or bake_output_path(model_path)
    out.write_bytes(inject_bake(exported, pngs, mm_bake))
    print(f"bake_model: wrote {out} (map_scale {map_scale:.6g})")


if __name__ == "__main__":
    try:
        main()
    except (BakeError, BakeContractError, ModelLayoutError) as exc:
        sys.exit(f"bake_model: {exc}")
