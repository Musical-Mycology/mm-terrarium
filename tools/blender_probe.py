# tools/blender_probe.py
"""Reports what tools/bake_model.py relies on in the installed Blender,
so its bpy calls are fixed against facts, not guesses. Run on the bake
host (docs/MM_TERRARIUM.md, LED layout models):

    Blender -b --factory-startup --python-exit-code 1 -P tools/blender_probe.py

Prints one JSON object between MM_PROBE_BEGIN / MM_PROBE_END lines. Every
section catches its own exception and reports it, so one API change does
not hide the rest.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.model_layout import read_glb_json  # noqa: E402


def _op_props(op) -> dict:
    out = {}
    for prop in op.get_rna_type().properties:
        if prop.identifier == "rna_type":
            continue
        entry = {"type": prop.type}
        if prop.type == "ENUM":
            try:
                entry["items"] = [item.identifier for item in prop.enum_items]
                entry["flag"] = prop.is_enum_flag
            except Exception as exc:  # noqa: BLE001 -- record, keep going
                entry["items_error"] = str(exc)
        out[prop.identifier] = entry
    return out


def _section(report: dict, key: str, fn) -> None:
    try:
        report[key] = fn()
    except Exception:  # noqa: BLE001 -- a probe reports, never stops
        report[key] = {"error": traceback.format_exc(limit=3)}


def _principled_inputs() -> list:
    mat = bpy.data.materials.new("probe_mat")
    mat.use_nodes = True
    return [i.name for i in mat.node_tree.nodes["Principled BSDF"].inputs]


def _cube_with_two_uvs():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=0.1)
    obj = bpy.context.active_object
    uvs = obj.data.uv_layers
    if len(uvs) == 0:
        uvs.new(name="UVMap")
    uvs.new(name="lightmap")
    return obj


def _packed_cube() -> tuple:
    """Fresh two-UV cube with the lightmap set unwrapped (Smart UV Project,
    as the bake does); returns (obj, uv_range)."""
    obj = _cube_with_two_uvs()
    uvs = obj.data.uv_layers
    uvs.active = uvs["lightmap"]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), margin_method="FRACTION",
                             island_margin=8 / 1024, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    lightmap_uvs = np.empty(len(obj.data.loops) * 2, dtype=np.float32)
    uvs["lightmap"].data.foreach_get("uv", lightmap_uvs)
    uvs.active = uvs[0]
    uvs[0].active_render = True
    return obj, [float(lightmap_uvs.min()), float(lightmap_uvs.max())]


def _lightmap_unwrap() -> dict:
    _obj, uv_range = _packed_cube()
    return {"lightmap_uv_range": uv_range}


def _export_report(obj) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.glb"
        bpy.ops.export_scene.gltf(
            filepath=str(path), export_format="GLB", export_yup=True,
            export_texcoords=True, export_normals=True, export_extras=False,
            export_lights=False, export_cameras=False)
        gltf = read_glb_json(path.read_bytes(), path=str(path))
    return {
        "primitive_attributes": [sorted(p["attributes"]) for m in gltf["meshes"]
                                 for p in m["primitives"]],
        "materials_exported": len(gltf.get("materials", [])),
        "images_exported": len(gltf.get("images", [])),
    }


def _export_plain() -> dict:
    """Task 6's real export condition: the bake image nodes are removed."""
    obj, _ = _packed_cube()
    mat = bpy.data.materials.new("probe_mat")
    mat.use_nodes = True
    obj.data.materials.append(mat)
    return _export_report(obj)


def _export_uv_referenced() -> dict:
    """Two image nodes, each fed by a UV Map node naming one UV set."""
    obj, _ = _packed_cube()
    mat = bpy.data.materials.new("probe_mat_uv")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    for i, uv_name in enumerate(("UVMap", "lightmap")):
        img = bpy.data.images.new(f"probe_img_{i}", width=4, height=4)
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        uvn = nodes.new("ShaderNodeUVMap")
        uvn.uv_map = uv_name
        links.new(uvn.outputs["UV"], tex.inputs["Vector"])
        target = "Base Color" if i == 0 else "Emission Color"
        links.new(tex.outputs["Color"], bsdf.inputs[target])
    obj.data.materials.append(mat)
    return _export_report(obj)


def _mini_bake() -> dict:
    """A translucent cube lit by a point light at its centre: bakes
    DIFFUSE then TRANSMISSION (colour excluded) into one float image and
    reports each pass's peak, proving the bake call and pass_filter
    work headless and that light inside a translucent body reaches it."""
    obj = _cube_with_two_uvs()
    mat = bpy.data.materials.new("probe_translucent")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Transmission Weight"].default_value = 1.0
    obj.data.materials.append(mat)
    img = bpy.data.images.new("probe_bake", width=32, height=32, alpha=False,
                              float_buffer=True)
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = img
    node.select = True
    mat.node_tree.nodes.active = node
    light = bpy.data.lights.new("probe_led", type="POINT")
    light.energy = 1.0
    light.shadow_soft_size = 0.001
    led = bpy.data.objects.new("probe_led", light)
    bpy.context.scene.collection.objects.link(led)
    scene = bpy.context.scene
    world = bpy.data.worlds.new("probe_world")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    peaks = {}
    for kind in ("DIFFUSE", "TRANSMISSION"):
        bpy.ops.object.bake(type=kind, pass_filter={"DIRECT", "INDIRECT"},
                            use_clear=True, margin=2, target="IMAGE_TEXTURES")
        flat = np.empty(32 * 32 * 4, dtype=np.float32)
        img.pixels.foreach_get(flat)
        peaks[kind] = float(flat[0::4].max())
    return peaks


def main() -> None:
    report = {"version": list(bpy.app.version), "version_string": bpy.app.version_string,
              "python": sys.version.split()[0]}
    _section(report, "op_smart_project", lambda: _op_props(bpy.ops.uv.smart_project))
    _section(report, "op_bake", lambda: _op_props(bpy.ops.object.bake))
    _section(report, "op_export_gltf", lambda: _op_props(bpy.ops.export_scene.gltf))
    _section(report, "op_import_gltf", lambda: _op_props(bpy.ops.import_scene.gltf))
    _section(report, "op_make_single_user", lambda: _op_props(bpy.ops.object.make_single_user))
    _section(report, "principled_inputs", _principled_inputs)
    _section(report, "lightmap_unwrap", _lightmap_unwrap)
    _section(report, "export_plain", _export_plain)
    _section(report, "export_uv_referenced", _export_uv_referenced)
    _section(report, "mini_bake", _mini_bake)
    print("MM_PROBE_BEGIN")
    print(json.dumps(report, indent=1, sort_keys=True))
    print("MM_PROBE_END")


if __name__ == "__main__":
    main()
