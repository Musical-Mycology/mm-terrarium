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
