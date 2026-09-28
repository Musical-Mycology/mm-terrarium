import struct

import pytest

from control.model_layout import read_glb_json
from tests.glb_builder import GlbBuilder
from tools.model_bake_helpers import InjectBakeError, encode_png_rgba8, inject_bake


def _flat_png(width: int, height: int, rgba: tuple) -> bytes:
    return encode_png_rgba8(width, height, bytes(rgba) * (width * height))


def test_encode_png_rgba8_starts_with_the_png_signature_and_declares_rgba():
    png = _flat_png(2, 2, (10, 20, 30, 255))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", png[16:26])
    assert (width, height, bit_depth, color_type) == (2, 2, 8, 6)  # 6 = RGBA


def test_encode_png_rgba8_refuses_the_wrong_pixel_length():
    with pytest.raises(ValueError, match=r"width\*height\*4"):
        encode_png_rgba8(2, 2, b"\x00" * 3)


def _bakeable_glb_bytes() -> bytes:
    """One non-marker mesh with both TEXCOORD_0 and TEXCOORD_1 -- what a
    real Blender export produces after the bake (spec section 4.1)."""
    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06,
                                     texcoord0=True, texcoord1=True)
    return builder.build([{"name": "Body", "mesh": mesh_idx}])


def test_inject_bake_adds_images_textures_sampler_and_root_extras():
    png = _flat_png(2, 2, (1, 2, 3, 4))
    mm_bake = {"source_sha256": "ab" * 32, "pixels": 1, "map_scale": 1.0,
               "resolution": 2, "blender": "4.2", "uv": "TEXCOORD_1",
               "layout": [{"index": 0, "x_mm": 0, "y_mm": 0, "z_mm": 0,
                           "size": "medium", "zone": None}]}
    out = inject_bake(_bakeable_glb_bytes(), [png], mm_bake)
    gltf = read_glb_json(out, path="out.glb")
    assert len(gltf["images"]) == 1
    assert gltf["images"][0]["mimeType"] == "image/png"
    assert len(gltf["textures"]) == 1
    sampler = gltf["samplers"][gltf["textures"][0]["sampler"]]
    assert sampler == {"magFilter": 9729, "minFilter": 9729,
                        "wrapS": 33071, "wrapT": 33071}
    root_extras = gltf["extras"]["mm_bake"]
    assert root_extras["maps"] == [0]
    assert root_extras["layout"] == mm_bake["layout"]
    assert root_extras["source_sha256"] == "ab" * 32
    assert "mm_bake" not in (gltf.get("scenes", [{}])[0] or {}).get("extras", {})


def test_inject_bake_orders_maps_by_png_order():
    pngs = [_flat_png(2, 2, (i, i, i, 255)) for i in range(3)]
    out = inject_bake(_bakeable_glb_bytes(), pngs, {"pixels": 12, "layout": []})
    gltf = read_glb_json(out, path="out.glb")
    assert gltf["extras"]["mm_bake"]["maps"] == [0, 1, 2]
    assert len(gltf["textures"]) == 3


def test_inject_bake_refuses_missing_texcoord1():
    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.0, 0.0, 0.0), 0.06, texcoord0=True)
    glb_bytes = builder.build([{"name": "Body", "mesh": mesh_idx}])
    with pytest.raises(InjectBakeError, match="TEXCOORD_1"):
        inject_bake(glb_bytes, [], {"pixels": 0, "layout": []})


def test_inject_bake_refuses_a_remaining_marker_mesh():
    builder = GlbBuilder()
    body_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06,
                                     texcoord0=True, texcoord1=True)
    marker_idx = builder.add_box_mesh((0.0, 0.0, 0.0), 0.002)
    nodes = [{"name": "Body", "mesh": body_idx},
             {"name": "LED_000", "mesh": marker_idx}]
    glb_bytes = builder.build(nodes)
    with pytest.raises(InjectBakeError, match="LED_000"):
        inject_bake(glb_bytes, [], {"pixels": 1, "layout": []})


def test_inject_bake_output_is_itself_a_valid_glb_with_a_larger_buffer():
    before = _bakeable_glb_bytes()
    before_gltf = read_glb_json(before, path="before.glb")
    png = _flat_png(2, 2, (9, 9, 9, 9))
    out = inject_bake(before, [png], {"pixels": 4, "layout": []})
    after_gltf = read_glb_json(out, path="out.glb")
    assert after_gltf["buffers"][0]["byteLength"] > before_gltf["buffers"][0]["byteLength"]
