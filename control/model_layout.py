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


def _node_local_matrix(node: dict, *, path: str = "") -> tuple:
    """A node's own local transform: its explicit `matrix` (16 floats,
    column-major, used verbatim) or its TRS fields, defaulting to
    identity when neither is present."""
    if "matrix" in node:
        m = node["matrix"]
        if len(m) != 16:
            raise ModelLayoutError(
                path=path,
                message=f"node {node.get('name')!r} matrix must have 16 elements")
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


def _world_matrix(node_idx: int, nodes: list, parent_map: dict, *, path: str = "") -> tuple:
    """The cumulative world transform of nodes[node_idx]: the product of
    every ancestor's local matrix, root-first, down to this node's own
    local matrix (identity ancestors for a Rhino export, but composed in
    general per spec section 4 step 4). Detects cycles in parent_map."""
    chain = [node_idx]
    cur = node_idx
    visited = {node_idx}
    while cur in parent_map:
        cur = parent_map[cur]
        if cur in visited:
            raise ModelLayoutError(
                path=path,
                message=f"node hierarchy has a cycle at node {cur}")
        chain.append(cur)
        visited.add(cur)
    chain.reverse()
    world = IDENTITY
    for idx in chain:
        world = _mat_mul(world, _node_local_matrix(nodes[idx], path=path))
    return world


def _find_leds_node(nodes: list, path: str) -> int:
    matches = [i for i, n in enumerate(nodes) if n.get("name") == "LEDs"]
    if not matches:
        raise ModelLayoutError(path=path, message="no node named 'LEDs' found")
    if len(matches) > 1:
        raise ModelLayoutError(path=path, message="more than one node named 'LEDs' found")
    return matches[0]


def _descendants(node_idx: int, nodes: list, *, path: str = ""):
    """Every descendant index of nodes[node_idx] (not including itself),
    in no particular order. Detects cycles in children hierarchy."""
    stack = list(nodes[node_idx].get("children", []))
    visited = {node_idx}
    while stack:
        idx = stack.pop()
        if idx in visited:
            raise ModelLayoutError(
                path=path,
                message=f"node hierarchy has a cycle at node {idx}")
        visited.add(idx)
        yield idx
        stack.extend(nodes[idx].get("children", []))


def _collect_markers(nodes: list, leds_idx: int, parent_map: dict, path: str) -> dict:
    """{LED index: node index}. A node named LED_### with no 'mesh' key
    is not a marker and is silently skipped (spec: "...and that has a
    mesh is a marker"). A node named LED_### that DOES have a mesh but
    sits outside the LEDs subtree is a located error, as is a duplicate
    index."""
    leds_descendants = set(_descendants(leds_idx, nodes, path=path))
    markers: dict = {}
    for i, node in enumerate(nodes):
        name = node.get("name", "")
        match = _LED_NAME_RE.match(name)
        if not match or "mesh" not in node:
            continue
        if i not in leds_descendants:
            raise ModelLayoutError(
                path=path, marker=name,
                message="named like an LED marker but is not a descendant "
                        "of the 'LEDs' node")
        idx = int(match.group(1))
        if idx in markers:
            raise ModelLayoutError(
                path=path, marker=name, message=f"duplicate LED index {idx}")
        markers[idx] = i
    return markers


def _zone_for(marker_node_idx: int, leds_idx: int, parent_map: dict, nodes: list):
    """The marker's zone: the name of its immediate parent, unless that
    parent IS the LEDs node itself (no named zone). A marker already
    validated as a descendant of LEDs always has an immediate parent
    that is either LEDs or a node on the path to LEDs."""
    parent_idx = parent_map.get(marker_node_idx)
    if parent_idx is None or parent_idx == leds_idx:
        return None
    return nodes[parent_idx].get("name")


@dataclass(frozen=True)
class PixelLayout:
    index: int
    x_mm: int
    y_mm: int
    z_mm: int
    size: str          # "small" | "medium" | "large"
    zone: str | None   # None when the marker sits directly on 'LEDs'


@dataclass(frozen=True)
class ModelLayout:
    pixels: tuple  # tuple[PixelLayout, ...], ordered by index
    model_sha256: str


def _refuse_draco(gltf: dict, path: str) -> None:
    required = gltf.get("extensionsRequired", [])
    if DRACO_EXTENSION in required:
        raise ModelLayoutError(
            path=path,
            message=f"model requires {DRACO_EXTENSION}; in the exporter, "
                    f"turn off 'Use Draco compression'")


def _band_size(diameter_mm: float) -> str:
    if diameter_mm < 4.0:
        return "small"
    if diameter_mm < 8.0:
        return "medium"
    return "large"


def _mesh_local_aabb(mesh: dict, accessors: list, path: str, marker_name: str) -> tuple:
    """The union bounding box, in local (node) space, of every primitive
    in `mesh`, read straight from each POSITION accessor's required
    min/max -- the binary chunk is never touched."""
    prims = mesh.get("primitives", [])
    if not prims:
        raise ModelLayoutError(path=path, marker=marker_name,
                               message="marker mesh has no primitives")
    mn = [math.inf, math.inf, math.inf]
    mx = [-math.inf, -math.inf, -math.inf]
    for prim in prims:
        pos_idx = prim.get("attributes", {}).get("POSITION")
        if pos_idx is None:
            raise ModelLayoutError(path=path, marker=marker_name,
                                   message="marker mesh primitive has no POSITION attribute")
        if pos_idx >= len(accessors) or pos_idx < 0:
            raise ModelLayoutError(path=path, marker=marker_name,
                                   message=f"POSITION accessor index {pos_idx} is out of bounds "
                                           f"(have {len(accessors)} accessors)")
        accessor = accessors[pos_idx]
        a_min, a_max = accessor.get("min"), accessor.get("max")
        if a_min is None or a_max is None:
            raise ModelLayoutError(path=path, marker=marker_name,
                                   message="POSITION accessor is missing min/max")
        for i in range(3):
            mn[i] = min(mn[i], a_min[i])
            mx[i] = max(mx[i], a_max[i])
    return tuple(mn), tuple(mx)


def _transform_aabb(m: tuple, mn: tuple, mx: tuple) -> tuple:
    """The axis-aligned bounding box of a local AABB's 8 corners after
    each is transformed by world matrix `m` (handles any rotation on the
    node chain, though a Rhino export carries none)."""
    xs, ys, zs = (mn[0], mx[0]), (mn[1], mx[1]), (mn[2], mx[2])
    pts = [_transform_point(m, (x, y, z)) for x in xs for y in ys for z in zs]
    tmn = tuple(min(p[i] for p in pts) for i in range(3))
    tmx = tuple(max(p[i] for p in pts) for i in range(3))
    return tmn, tmx


def _validate_indices(markers: dict, pixel_count: int, path: str) -> None:
    expected, found = set(range(pixel_count)), set(markers)
    if found == expected:
        return
    # Spec section 4 step 7: name the file, the marker and the rule. Always
    # compute both halves of the mismatch so the message is never
    # self-contradictory (e.g. 1-based marker numbering against a correct
    # pixel count looks like both a "missing" and an "unexpected" index,
    # never a bare count mismatch that claims the wrong total).
    missing = [f"LED_{i:03d}" for i in sorted(expected - found)]
    extra = [f"LED_{i:03d}" for i in sorted(found - expected)]
    raise ModelLayoutError(
        path=path,
        message=f"LED marker indices must be exactly 0..{pixel_count - 1} "
                f"(pixels={pixel_count}); found {len(markers)} marker(s); "
                f"missing {missing}; unexpected {extra}")


def parse_model_layout(data: bytes, *, path: str, pixel_count: int) -> ModelLayout:
    """Parse an artist-authored .glb into an ordered PixelLayout tuple
    plus the source file's SHA-256, per spec section 4. Raises
    ModelLayoutError, located to the file and (where applicable) the
    offending marker, on any rule violation -- including ordinary
    malformed JSON (an out-of-range index, a non-dict node, a value of
    the wrong type) that would otherwise escape as a raw, unlocated
    IndexError/KeyError/TypeError/ValueError/AttributeError."""
    gltf = read_glb_json(data, path=path)
    if not isinstance(gltf, dict):
        raise ModelLayoutError(path=path, message="glTF root must be a JSON object")
    try:
        return _parse_model_layout_body(gltf, data, path=path, pixel_count=pixel_count)
    except ModelLayoutError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ModelLayoutError(path=path, message=f"malformed glTF: {exc}") from exc


def _parse_model_layout_body(gltf: dict, data: bytes, *, path: str,
                             pixel_count: int) -> ModelLayout:
    _refuse_draco(gltf, path)
    nodes = gltf.get("nodes", [])
    leds_idx = _find_leds_node(nodes, path)
    parent_map = _build_parent_map(nodes)
    markers = _collect_markers(nodes, leds_idx, parent_map, path)
    _validate_indices(markers, pixel_count, path)

    meshes = gltf.get("meshes", [])
    accessors = gltf.get("accessors", [])
    pixels = []
    for idx in range(pixel_count):
        node_idx = markers[idx]
        node = nodes[node_idx]
        name = node.get("name")
        mesh_idx = node["mesh"]
        if mesh_idx >= len(meshes) or mesh_idx < 0:
            raise ModelLayoutError(
                path=path, marker=name,
                message=f"mesh index {mesh_idx} is out of bounds (have {len(meshes)} meshes)")
        mesh = meshes[mesh_idx]
        local_mn, local_mx = _mesh_local_aabb(mesh, accessors, path, name)
        world = _world_matrix(node_idx, nodes, parent_map, path=path)
        tmn, tmx = _transform_aabb(world, local_mn, local_mx)

        cx = (tmn[0] + tmx[0]) / 2.0
        cy = (tmn[1] + tmx[1]) / 2.0
        cz = (tmn[2] + tmx[2]) / 2.0
        x_mm = round(cx * 1000.0)
        y_mm = round(-cz * 1000.0)
        z_mm = round(cy * 1000.0)
        diameter_mm = max(tmx[i] - tmn[i] for i in range(3)) * 1000.0
        size = _band_size(diameter_mm)

        zone = _zone_for(node_idx, leds_idx, parent_map, nodes)
        if zone is not None:
            if not _ZONE_NAME_RE.match(zone):
                raise ModelLayoutError(
                    path=path, marker=name,
                    message=f"zone name {zone!r} must match [a-z0-9_]+")
            if zone == "primary":
                raise ModelLayoutError(
                    path=path, marker=name,
                    message="'primary' must not be used as a zone name")

        pixels.append(PixelLayout(index=idx, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm,
                                  size=size, zone=zone))

    model_sha256 = hashlib.sha256(data).hexdigest()
    return ModelLayout(pixels=tuple(pixels), model_sha256=model_sha256)
