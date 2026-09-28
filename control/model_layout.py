"""Parses the LED-marker layout out of an artist-authored .glb, both for
mm-terrarium's own catalog (control/terrarium_config.py's _parse_instrument)
and for tools/bake_model.py / tools/export_models.py. Pure stdlib
(struct, json, hashlib, math) -- control/ discipline. The binary chunk of
the GLB is never read: every value this module needs (LED marker names,
node hierarchy, POSITION accessor min/max) lives in the JSON chunk, which
glTF requires POSITION accessors to carry.

Spec: mm-tuneshroom docs/superpowers/specs/
2026-09-28-3d-tuneshroom-model-and-view-design.md, section 4 (the layout
parser, both repos, one rule set) and section 3 (the artist convention).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass

GLB_MAGIC = 0x46546C67          # b"glTF" little-endian
JSON_CHUNK_TYPE = 0x4E4F534A    # b"JSON" little-endian
DRACO_EXTENSION = "KHR_draco_mesh_compression"

_LED_NAME_RE = re.compile(r"^LED_(\d{3})$")
_ZONE_NAME_RE = re.compile(r"^[a-z0-9_]+$")


class ModelLayoutError(Exception):
    """A located parse/validation failure: names the file and, when the
    failure is about one marker, that marker's object name."""

    def __init__(self, *, path: str, message: str, marker: str | None = None) -> None:
        self.path = str(path)
        self.marker = marker
        self.message = message
        located = f" marker {marker!r}:" if marker else ":"
        super().__init__(f"{self.path}{located} {message}")


def read_glb_json(data: bytes, *, path: str) -> dict:
    """Parse a GLB's 12-byte header and its JSON chunk; return the parsed
    document. The binary chunk (if any) is never read."""
    if len(data) < 12:
        raise ModelLayoutError(path=path, message="file too small to be a GLB")
    magic, _version, total_length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise ModelLayoutError(path=path, message="not a GLB file (bad magic)")
    if total_length > len(data):
        raise ModelLayoutError(path=path, message="header length exceeds file size")
    offset = 12
    if offset + 8 > len(data):
        raise ModelLayoutError(path=path, message="missing JSON chunk header")
    chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
    offset += 8
    if chunk_type != JSON_CHUNK_TYPE:
        raise ModelLayoutError(path=path, message="first GLB chunk is not JSON")
    if offset + chunk_length > len(data):
        raise ModelLayoutError(path=path, message="JSON chunk length exceeds file size")
    json_bytes = data[offset:offset + chunk_length]
    try:
        return json.loads(json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelLayoutError(
            path=path, message=f"JSON chunk is not valid JSON: {exc}") from exc


IDENTITY: tuple = (1.0, 0.0, 0.0, 0.0,
                   0.0, 1.0, 0.0, 0.0,
                   0.0, 0.0, 1.0, 0.0,
                   0.0, 0.0, 0.0, 1.0)


def _mat_from_trs(translation, rotation, scale) -> tuple:
    """A column-major 4x4 matrix (glTF's own storage convention) from
    optional translation/rotation(quaternion x,y,z,w)/scale, composed as
    glTF defines it: M = T * R * S."""
    tx, ty, tz = translation if translation is not None else (0.0, 0.0, 0.0)
    qx, qy, qz, qw = rotation if rotation is not None else (0.0, 0.0, 0.0, 1.0)
    sx, sy, sz = scale if scale is not None else (1.0, 1.0, 1.0)
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    return (
        (1.0 - 2.0 * (yy + zz)) * sx, (2.0 * (xy + wz)) * sx, (2.0 * (xz - wy)) * sx, 0.0,
        (2.0 * (xy - wz)) * sy, (1.0 - 2.0 * (xx + zz)) * sy, (2.0 * (yz + wx)) * sy, 0.0,
        (2.0 * (xz + wy)) * sz, (2.0 * (yz - wx)) * sz, (1.0 - 2.0 * (xx + yy)) * sz, 0.0,
        tx, ty, tz, 1.0,
    )


def _node_local_matrix(node: dict) -> tuple:
    """A node's own local transform: its explicit `matrix` (16 floats,
    column-major, used verbatim) or its TRS fields, defaulting to
    identity when neither is present."""
    if "matrix" in node:
        m = node["matrix"]
        if len(m) != 16:
            raise ValueError(f"node {node.get('name')!r} matrix must have 16 elements")
        return tuple(float(x) for x in m)
    return _mat_from_trs(node.get("translation"), node.get("rotation"), node.get("scale"))


def _mat_mul(a: tuple, b: tuple) -> tuple:
    """a @ b, both column-major 16-tuples (glTF storage: element
    col*4+row)."""
    result = [0.0] * 16
    for col in range(4):
        for row in range(4):
            s = 0.0
            for k in range(4):
                s += a[k * 4 + row] * b[col * 4 + k]
            result[col * 4 + row] = s
    return tuple(result)


def _transform_point(m: tuple, p: tuple) -> tuple:
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def _build_parent_map(nodes: list) -> dict:
    """{child node index: parent node index}, from every node's
    `children` list."""
    parent: dict = {}
    for i, node in enumerate(nodes):
        for child in node.get("children", []):
            parent[child] = i
    return parent


def _world_matrix(node_idx: int, nodes: list, parent_map: dict) -> tuple:
    """The cumulative world transform of nodes[node_idx]: the product of
    every ancestor's local matrix, root-first, down to this node's own
    local matrix (identity ancestors for a Rhino export, but composed in
    general per spec section 4 step 4)."""
    chain = [node_idx]
    cur = node_idx
    while cur in parent_map:
        cur = parent_map[cur]
        chain.append(cur)
    chain.reverse()
    world = IDENTITY
    for idx in chain:
        world = _mat_mul(world, _node_local_matrix(nodes[idx]))
    return world
