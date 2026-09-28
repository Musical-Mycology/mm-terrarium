import hashlib
import json
import struct

import pytest

from control.model_layout import ModelLayoutError, read_glb_json
from tests.glb_builder import GLB_MAGIC, JSON_CHUNK_TYPE, build_glb as _build_glb


def test_read_glb_json_round_trips_a_minimal_document():
    gltf = {"asset": {"version": "2.0"}, "nodes": []}
    data = _build_glb(gltf)
    assert read_glb_json(data, path="t.glb") == gltf


def test_read_glb_json_refuses_bad_magic():
    with pytest.raises(ModelLayoutError, match="magic"):
        read_glb_json(b"not-a-glb-at-all-------", path="t.glb")


def test_read_glb_json_refuses_short_file():
    with pytest.raises(ModelLayoutError, match="too small"):
        read_glb_json(b"\x00\x01", path="t.glb")


def test_read_glb_json_refuses_non_json_first_chunk():
    header = struct.pack("<III", GLB_MAGIC, 2, 12 + 8 + 4)
    chunk_header = struct.pack("<II", 4, 0x004E4942)  # BIN
    data = header + chunk_header + b"\x00\x00\x00\x00"
    with pytest.raises(ModelLayoutError, match="not JSON"):
        read_glb_json(data, path="t.glb")


def test_read_glb_json_error_names_the_path():
    with pytest.raises(ModelLayoutError, match="t.glb"):
        read_glb_json(b"short", path="t.glb")


from control.model_layout import (
    _build_parent_map, _node_local_matrix, _transform_point, _world_matrix,
)

IDENTITY = (1.0, 0.0, 0.0, 0.0,
           0.0, 1.0, 0.0, 0.0,
           0.0, 0.0, 1.0, 0.0,
           0.0, 0.0, 0.0, 1.0)


def test_node_local_matrix_defaults_to_identity():
    assert _node_local_matrix({}) == IDENTITY


def test_node_local_matrix_uses_matrix_key_verbatim():
    m = tuple(float(i) for i in range(16))
    assert _node_local_matrix({"matrix": list(m)}) == m


def test_node_local_matrix_from_translation_only():
    m = _node_local_matrix({"translation": [1.0, 2.0, 3.0]})
    assert m[12:15] == (1.0, 2.0, 3.0)
    assert m[0:3] == (1.0, 0.0, 0.0)


def test_node_local_matrix_from_scale_only():
    m = _node_local_matrix({"scale": [2.0, 3.0, 4.0]})
    assert m[0] == 2.0 and m[5] == 3.0 and m[10] == 4.0


def test_transform_point_identity_is_a_no_op():
    assert _transform_point(IDENTITY, (1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)


def test_transform_point_applies_translation():
    m = _node_local_matrix({"translation": [10.0, 0.0, 0.0]})
    assert _transform_point(m, (0.0, 0.0, 0.0)) == (10.0, 0.0, 0.0)


def test_world_matrix_composes_parent_translation_with_child():
    nodes = [
        {"name": "root", "translation": [10.0, 0.0, 0.0], "children": [1]},
        {"name": "child", "translation": [0.0, 5.0, 0.0]},
    ]
    parent_map = _build_parent_map(nodes)
    world = _world_matrix(1, nodes, parent_map)
    assert _transform_point(world, (0.0, 0.0, 0.0)) == (10.0, 5.0, 0.0)


def test_world_matrix_of_a_root_node_is_its_own_local_matrix():
    nodes = [{"name": "root", "translation": [1.0, 2.0, 3.0]}]
    parent_map = _build_parent_map(nodes)
    assert _world_matrix(0, nodes, parent_map) == _node_local_matrix(nodes[0])


from control.model_layout import _collect_markers, _find_leds_node, _zone_for


def _nodes_with_leds(marker_specs):
    """marker_specs: list of (name, zone_or_None). Builds a node list:
    index 0 is 'LEDs', zone nodes as its children, marker nodes (each
    given a 'mesh': 0 so _collect_markers treats it as a marker) as
    children of their zone node or of LEDs directly."""
    nodes = [{"name": "LEDs", "children": []}]
    zone_idx = {}
    for name, zone in marker_specs:
        parent_idx = 0
        if zone is not None:
            if zone not in zone_idx:
                nodes.append({"name": zone, "children": []})
                zone_idx[zone] = len(nodes) - 1
                nodes[0]["children"].append(zone_idx[zone])
            parent_idx = zone_idx[zone]
        nodes.append({"name": name, "mesh": 0})
        nodes[parent_idx]["children"].append(len(nodes) - 1)
    return nodes


def test_find_leds_node_locates_the_one_node():
    nodes = [{"name": "Body"}, {"name": "LEDs"}]
    assert _find_leds_node(nodes, "t.glb") == 1


def test_find_leds_node_refuses_when_missing():
    with pytest.raises(ModelLayoutError, match="no node named"):
        _find_leds_node([{"name": "Body"}], "t.glb")


def test_find_leds_node_refuses_when_duplicated():
    with pytest.raises(ModelLayoutError, match="more than one"):
        _find_leds_node([{"name": "LEDs"}, {"name": "LEDs"}], "t.glb")


def test_collect_markers_finds_markers_by_index():
    nodes = _nodes_with_leds([("LED_000", "ring"), ("LED_001", "ring")])
    parent_map = _build_parent_map(nodes)
    markers = _collect_markers(nodes, 0, parent_map, "t.glb")
    assert set(markers) == {0, 1}


def test_collect_markers_refuses_marker_outside_leds():
    nodes = [{"name": "LEDs", "children": []}, {"name": "LED_000", "mesh": 0}]
    parent_map = _build_parent_map(nodes)
    with pytest.raises(ModelLayoutError, match="not a descendant"):
        _collect_markers(nodes, 0, parent_map, "t.glb")


def test_collect_markers_refuses_duplicate_index():
    nodes = _nodes_with_leds([("LED_000", "ring"), ("LED_000", "stem")])
    parent_map = _build_parent_map(nodes)
    with pytest.raises(ModelLayoutError, match="duplicate"):
        _collect_markers(nodes, 0, parent_map, "t.glb")


def test_collect_markers_ignores_a_led_named_node_without_a_mesh():
    nodes = [{"name": "LEDs", "children": [1]}, {"name": "LED_000"}]
    parent_map = _build_parent_map(nodes)
    assert _collect_markers(nodes, 0, parent_map, "t.glb") == {}


def test_zone_for_marker_directly_on_leds_is_none():
    nodes = [{"name": "LEDs", "children": [1]}, {"name": "LED_000", "mesh": 0}]
    parent_map = _build_parent_map(nodes)
    assert _zone_for(1, 0, parent_map, nodes) is None


def test_zone_for_marker_under_a_sublayer_is_that_layer_name():
    nodes = _nodes_with_leds([("LED_000", "ring")])
    parent_map = _build_parent_map(nodes)
    marker_idx = next(i for i, n in enumerate(nodes) if n.get("name") == "LED_000")
    assert _zone_for(marker_idx, 0, parent_map, nodes) == "ring"


from control.model_layout import ModelLayout, PixelLayout, parse_model_layout


def _sphere_node(name: str, mesh_idx: int) -> dict:
    return {"name": name, "mesh": mesh_idx}


from tests.glb_builder import accessor_only_box, mesh_with_position


def _sphere_mesh_and_accessor(center_m, radius_m, accessors: list) -> dict:
    accessors.append(accessor_only_box(center_m, radius_m))
    return mesh_with_position(len(accessors) - 1)


def _one_marker_document(center_m, radius_m, zone="ring"):
    accessors: list = []
    mesh = _sphere_mesh_and_accessor(center_m, radius_m, accessors)
    nodes = [
        {"name": "LEDs", "children": [1]},
        {"name": zone, "children": [2]},
        {"name": "LED_000", "mesh": 0},
    ]
    return {"nodes": nodes, "meshes": [mesh], "accessors": accessors}


def test_parse_model_layout_converts_axes_and_rounds_to_mm():
    gltf = _one_marker_document((0.010, 0.020, 0.030), 0.001)
    data = _build_glb(gltf)
    layout = parse_model_layout(data, path="t.glb", pixel_count=1)
    px = layout.pixels[0]
    # gltf (X, Y, Z) metres -> layout (X, -Z, Y) * 1000 mm
    assert (px.x_mm, px.y_mm, px.z_mm) == (10, -30, 20)
    assert px.zone == "ring"
    assert px.index == 0


def test_parse_model_layout_bands_diameter():
    small = parse_model_layout(
        _build_glb(_one_marker_document((0.0, 0.0, 0.0), 0.0015)),  # 3mm dia
        path="t.glb", pixel_count=1)
    medium = parse_model_layout(
        _build_glb(_one_marker_document((0.0, 0.0, 0.0), 0.0025)),  # 5mm dia
        path="t.glb", pixel_count=1)
    large = parse_model_layout(
        _build_glb(_one_marker_document((0.0, 0.0, 0.0), 0.005)),  # 10mm dia
        path="t.glb", pixel_count=1)
    assert small.pixels[0].size == "small"
    assert medium.pixels[0].size == "medium"
    assert large.pixels[0].size == "large"


def test_parse_model_layout_marker_directly_on_leds_has_no_zone():
    gltf = _one_marker_document((0.0, 0.0, 0.0), 0.001)
    gltf["nodes"] = [
        {"name": "LEDs", "children": [1]},
        {"name": "LED_000", "mesh": 0},
    ]
    layout = parse_model_layout(_build_glb(gltf), path="t.glb", pixel_count=1)
    assert layout.pixels[0].zone is None


def test_parse_model_layout_returns_model_sha256_of_the_whole_file():
    data = _build_glb(_one_marker_document((0.0, 0.0, 0.0), 0.001))
    layout = parse_model_layout(data, path="t.glb", pixel_count=1)
    assert layout.model_sha256 == hashlib.sha256(data).hexdigest()


def test_parse_model_layout_refuses_draco():
    gltf = _one_marker_document((0.0, 0.0, 0.0), 0.001)
    gltf["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    with pytest.raises(ModelLayoutError, match="Draco"):
        parse_model_layout(_build_glb(gltf), path="t.glb", pixel_count=1)


def test_parse_model_layout_refuses_bad_zone_name():
    gltf = _one_marker_document((0.0, 0.0, 0.0), 0.001, zone="Ring-1")
    with pytest.raises(ModelLayoutError, match=r"\[a-z0-9_\]"):
        parse_model_layout(_build_glb(gltf), path="t.glb", pixel_count=1)


def test_parse_model_layout_refuses_zone_named_primary():
    gltf = _one_marker_document((0.0, 0.0, 0.0), 0.001, zone="primary")
    with pytest.raises(ModelLayoutError, match="primary"):
        parse_model_layout(_build_glb(gltf), path="t.glb", pixel_count=1)


def test_parse_model_layout_refuses_count_mismatch():
    gltf = _one_marker_document((0.0, 0.0, 0.0), 0.001)
    with pytest.raises(ModelLayoutError, match="found 1"):
        parse_model_layout(_build_glb(gltf), path="t.glb", pixel_count=2)


# Fix round 1: malformed model robustness

def test_node_local_matrix_wrong_length_is_model_layout_error():
    """Issue 1: _node_local_matrix must raise ModelLayoutError (not ValueError)
    when an explicit matrix has wrong length, and it must locate the file path."""
    from control.model_layout import _node_local_matrix
    # This test uses _node_local_matrix directly with a path, but the current
    # implementation doesn't take a path parameter. So the fix must thread path
    # through, or we test via parse_model_layout. Testing via parse_model_layout:
    gltf = _one_marker_document((0.0, 0.0, 0.0), 0.001)
    gltf["nodes"][0]["matrix"] = [1.0, 0.0]  # Wrong length
    with pytest.raises(ModelLayoutError, match="t.glb"):
        parse_model_layout(_build_glb(gltf), path="t.glb", pixel_count=1)


def test_parse_model_layout_bounds_check_mesh_index():
    """Issue 2a: mesh index out of bounds must be ModelLayoutError naming file."""
    gltf = _one_marker_document((0.0, 0.0, 0.0), 0.001)
    gltf["nodes"][2]["mesh"] = 999  # Out of bounds
    with pytest.raises(ModelLayoutError, match="t.glb"):
        parse_model_layout(_build_glb(gltf), path="t.glb", pixel_count=1)


def test_parse_model_layout_bounds_check_accessor_index():
    """Issue 2b: POSITION accessor index out of bounds must be ModelLayoutError."""
    gltf = _one_marker_document((0.0, 0.0, 0.0), 0.001)
    gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"] = 999  # Out of bounds
    with pytest.raises(ModelLayoutError, match="t.glb"):
        parse_model_layout(_build_glb(gltf), path="t.glb", pixel_count=1)


def test_world_matrix_detects_parent_map_cycle():
    """Issue 3: _world_matrix must detect cycles in parent_map and raise ModelLayoutError."""
    from control.model_layout import _build_parent_map, _world_matrix
    nodes = [
        {"name": "node0", "children": [1]},
        {"name": "node1", "children": [2]},
        {"name": "node2", "children": [0]},  # Cycle back to 0
    ]
    parent_map = _build_parent_map(nodes)
    # Calling _world_matrix on any node in the cycle should detect it
    with pytest.raises(ModelLayoutError, match="cycle"):
        _world_matrix(0, nodes, parent_map)


def test_descendants_detects_cycle():
    """Issue 3: _descendants must detect cycles in children and raise ModelLayoutError."""
    from control.model_layout import _descendants
    nodes = [
        {"name": "node0", "children": [1]},
        {"name": "node1", "children": [2]},
        {"name": "node2", "children": [0]},  # Cycle back to 0
    ]
    # Calling _descendants on node 0 should detect the cycle
    with pytest.raises(ModelLayoutError, match="cycle"):
        list(_descendants(0, nodes))
