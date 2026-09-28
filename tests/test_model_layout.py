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
