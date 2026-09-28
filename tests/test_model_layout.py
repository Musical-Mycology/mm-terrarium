import json
import struct

import pytest

from control.model_layout import ModelLayoutError, read_glb_json

GLB_MAGIC = 0x46546C67
JSON_CHUNK_TYPE = 0x4E4F534A


def _build_glb(gltf: dict) -> bytes:
    body = json.dumps(gltf).encode("utf-8")
    pad = (-len(body)) % 4
    body += b" " * pad
    header = struct.pack("<III", GLB_MAGIC, 2, 12 + 8 + len(body))
    chunk_header = struct.pack("<II", len(body), JSON_CHUNK_TYPE)
    return header + chunk_header + body


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
