import struct

from control.model_layout import parse_model_layout
from tests.glb_builder import BIN_CHUNK_TYPE, GlbBuilder, build_glb


def test_build_glb_round_trips_through_parse_model_layout():
    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.010, 0.020, 0.030), 0.001)
    nodes = [
        {"name": "LEDs", "children": [1]},
        {"name": "ring", "children": [2]},
        {"name": "LED_000", "mesh": mesh_idx},
    ]
    glb_bytes = builder.build(nodes)
    layout = parse_model_layout(glb_bytes, path="t.glb", pixel_count=1)
    px = layout.pixels[0]
    assert (px.x_mm, px.y_mm, px.z_mm) == (10, -30, 20)
    assert px.zone == "ring"


def test_build_glb_writes_a_real_four_byte_aligned_bin_chunk():
    builder = GlbBuilder()
    builder.add_box_mesh((0.0, 0.0, 0.0), 0.001)
    glb_bytes = builder.build([{"name": "Body", "mesh": 0}])
    json_len = struct.unpack_from("<I", glb_bytes, 12)[0]
    bin_chunk_offset = 12 + 8 + json_len
    bin_len, bin_type = struct.unpack_from("<II", glb_bytes, bin_chunk_offset)
    assert bin_type == BIN_CHUNK_TYPE
    assert bin_len % 4 == 0
    assert bin_len > 0
    assert bin_chunk_offset + 8 + bin_len == len(glb_bytes)


def test_build_glb_box_mesh_has_eight_vertices_and_thirty_six_indices():
    builder = GlbBuilder()
    builder.add_box_mesh((0.0, 0.0, 0.0), 0.05)
    prim = builder.meshes[0]["primitives"][0]
    pos_accessor = builder.accessors[prim["attributes"]["POSITION"]]
    idx_accessor = builder.accessors[prim["indices"]]
    assert pos_accessor["count"] == 8
    assert idx_accessor["count"] == 36
    assert idx_accessor["componentType"] == 5123  # UNSIGNED_SHORT


def test_build_glb_box_mesh_can_carry_texcoord0_and_texcoord1():
    builder = GlbBuilder()
    builder.add_box_mesh((0.0, 0.0, 0.0), 0.05, texcoord0=True, texcoord1=True)
    attrs = builder.meshes[0]["primitives"][0]["attributes"]
    assert "TEXCOORD_0" in attrs and "TEXCOORD_1" in attrs
    assert builder.accessors[attrs["TEXCOORD_0"]]["type"] == "VEC2"


def test_build_glb_with_no_binary_omits_the_bin_chunk():
    glb_bytes = build_glb({"asset": {"version": "2.0"}, "nodes": []})
    total_length = struct.unpack_from("<I", glb_bytes, 8)[0]
    assert total_length == len(glb_bytes)
    json_len = struct.unpack_from("<I", glb_bytes, 12)[0]
    assert 12 + 8 + json_len == len(glb_bytes)  # nothing after the JSON chunk


def test_accessor_only_box_has_no_backing_geometry():
    """Documents the deliberate limitation: this helper is for
    parser-math-only tests and produces an accessor with no bufferView."""
    from tests.glb_builder import accessor_only_box
    acc = accessor_only_box((0.0, 0.0, 0.0), 0.002)
    assert "bufferView" not in acc
    assert acc["min"] and acc["max"]


def test_build_glb_emits_a_default_scene_with_root_nodes():
    """GlbBuilder must emit a scenes array and scene index so three.js
    GLTFLoader and Blender can instantiate a root. Root nodes are those
    not listed in any node's children."""
    import json
    builder = GlbBuilder()
    builder.add_box_mesh((0.0, 0.0, 0.0), 0.001)
    nodes = [
        {"name": "LEDs", "children": [1, 2]},
        {"name": "ring", "children": [3]},
        {"name": "LED_000", "mesh": 0},
        {"name": "LED_001", "mesh": 0},
        {"name": "body", "mesh": 0},  # separate root
    ]
    glb_bytes = builder.build(nodes)
    # Extract and parse the JSON chunk
    json_len = struct.unpack_from("<I", glb_bytes, 12)[0]
    json_chunk_start = 20  # header (12) + chunk header (8)
    json_text = glb_bytes[json_chunk_start:json_chunk_start + json_len].decode("utf-8")
    gltf = json.loads(json_text)
    # Verify scene is present and defaults to 0
    assert gltf.get("scene") == 0
    assert "scenes" in gltf
    assert len(gltf["scenes"]) == 1
    # Verify roots are exactly LEDs (0) and body (4), in index order
    assert gltf["scenes"][0]["nodes"] == [0, 4]
