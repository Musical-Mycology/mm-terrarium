"""Writes .glb files for control/model_layout.py's tests,
tests/generate_model_fixture.py (the shared marker/bake fixture pair),
and tools/export_models.py's tests.

Two tiers, both stdlib-only (struct, json):

- `build_glb` + `GlbBuilder`: a VALID glTF 2.0 document with real geometry
  (a box mesh per node, backed by an actual 4-byte-aligned binary chunk).
  Anything that Blender must import or Three.js must render (the shared
  fixture, Task 7; anything fed through tools/model_bake_helpers.inject_bake)
  is built this way.
- `accessor_only_box` / `mesh_with_position`: a JSON-only accessor/mesh
  pair with a `min`/`max` but no backing geometry at all (no
  `bufferView`, no binary chunk). control/model_layout.py never reads a
  GLB's binary chunk (spec section 4 step 1: "The binary chunk is never
  read"), so these are sufficient -- and much simpler to hand-edit -- for
  tests that only exercise the parser's accessor-`min`/`max` math and are
  never loaded by Blender or a browser.
"""
from __future__ import annotations

import json
import struct

GLB_MAGIC = 0x46546C67          # b"glTF" little-endian
JSON_CHUNK_TYPE = 0x4E4F534A    # b"JSON" little-endian
BIN_CHUNK_TYPE = 0x004E4942     # b"BIN\0" little-endian

COMPONENT_TYPE_FLOAT = 5126
COMPONENT_TYPE_UNSIGNED_SHORT = 5123
TARGET_ARRAY_BUFFER = 34962
TARGET_ELEMENT_ARRAY_BUFFER = 34963

# A unit box's 8 corners (scaled by half_size_m, offset by center_m in
# GlbBuilder.add_box_mesh).
_BOX_CORNERS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
]
# 12 triangles (36 indices), outward winding. The exact triangulation is
# not load-bearing for any consumer in this repo -- control/model_layout.py
# reads only the POSITION accessor's min/max, never a vertex or a face --
# it exists so the box is a closed, non-degenerate solid a real glTF
# viewer (Blender, Three.js) can import and render without complaint.
_BOX_FACES = [
    (0, 1, 2), (0, 2, 3),   # -Z
    (4, 6, 5), (4, 7, 6),   # +Z
    (0, 4, 5), (0, 5, 1),   # -Y
    (3, 2, 6), (3, 6, 7),   # +Y
    (0, 3, 7), (0, 7, 4),   # -X
    (1, 5, 6), (1, 6, 2),   # +X
]
_BOX_UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0),
            (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def build_glb(gltf: dict, binary: bytes = b"") -> bytes:
    """Serialises a parsed glTF JSON document plus an optional binary
    blob into a valid GLB: the JSON chunk (UTF-8, space-padded to a
    4-byte boundary), then a BIN chunk (zero-padded to a 4-byte
    boundary), present only when `binary` is non-empty. `GlbBuilder`
    normally supplies both; this function is also called directly for
    JSON-only documents that declare no meshes at all (e.g.
    control/model_layout.py's GLB-header/JSON-chunk tests) and by
    tools/model_bake_helpers.py's own GLB writer (a separate, tiny,
    intentionally-duplicated copy in production code -- see that
    module's docstring for why it does not import this test helper).
    """
    body = json.dumps(gltf).encode("utf-8")
    body += b" " * ((-len(body)) % 4)
    chunks = struct.pack("<II", len(body), JSON_CHUNK_TYPE) + body
    if binary:
        padded = binary + b"\x00" * ((-len(binary)) % 4)
        chunks += struct.pack("<II", len(padded), BIN_CHUNK_TYPE) + padded
    header = struct.pack("<III", GLB_MAGIC, 2, 12 + len(chunks))
    return header + chunks


def accessor_only_box(center_m: tuple, radius_m: float, count: int = 1) -> dict:
    """A POSITION accessor dict for a box of half-extent `radius_m`
    centred at `center_m` (glTF metres) -- a `min`/`max` and nothing
    else. parse_model_layout reads only these two fields off a marker's
    POSITION accessor (spec section 4 step 4), so this is sufficient for
    every control/model_layout.py test that isn't the shared fixture --
    but the result has no `bufferView` and is NOT valid, renderable
    glTF: do not feed it to Blender or a browser. Use GlbBuilder instead
    when the output must actually be loadable."""
    mn = [c - radius_m for c in center_m]
    mx = [c + radius_m for c in center_m]
    return {"componentType": COMPONENT_TYPE_FLOAT, "count": count,
            "type": "VEC3", "min": mn, "max": mx}


def mesh_with_position(accessor_idx: int) -> dict:
    """Pairs with accessor_only_box: a mesh dict whose one primitive has
    only a POSITION attribute and no `indices` -- see accessor_only_box's
    docstring for the same "not renderable" caveat."""
    return {"primitives": [{"attributes": {"POSITION": accessor_idx}}]}


class GlbBuilder:
    """Accumulates one or more box meshes into a single shared binary
    buffer, then assembles one valid GLB. One instance per document --
    every accessor a builder produces references buffer 0 (the buffer it
    is assembling), so meshes added by the same instance combine freely
    into one `nodes` list.
    """

    def __init__(self) -> None:
        self._binary = bytearray()
        self.accessors: list = []
        self.buffer_views: list = []
        self.meshes: list = []

    def _append(self, raw: bytes, *, target) -> int:
        pad = (-len(self._binary)) % 4
        self._binary.extend(b"\x00" * pad)
        offset = len(self._binary)
        self._binary.extend(raw)
        view: dict = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target is not None:
            view["target"] = target
        self.buffer_views.append(view)
        return len(self.buffer_views) - 1

    def add_box_mesh(self, center_m: tuple, half_size_m: float, *,
                      texcoord0: bool = False, texcoord1: bool = False) -> int:
        """A box centred at `center_m` (glTF metres) with half-extent
        `half_size_m` on every axis: real POSITION and indices data (8
        vertices, 36 indices) backed by the shared binary buffer -- a
        valid, closed, Blender-importable, Three.js-renderable primitive,
        not just an accessor carrying a `min`/`max`. Optionally adds a
        placeholder TEXCOORD_0 and/or TEXCOORD_1 (VEC2 float32, one of
        the box's 8 fixed corner UVs per vertex -- UV *content* is never
        read by anything in this repo, only the accessor's presence and
        validity are). Returns the new mesh's index.
        """
        positions = [(center_m[0] + cx * half_size_m,
                      center_m[1] + cy * half_size_m,
                      center_m[2] + cz * half_size_m)
                     for cx, cy, cz in _BOX_CORNERS]
        pos_view = self._append(
            b"".join(struct.pack("<3f", *p) for p in positions),
            target=TARGET_ARRAY_BUFFER)
        self.accessors.append({
            "bufferView": pos_view, "componentType": COMPONENT_TYPE_FLOAT,
            "count": len(positions), "type": "VEC3",
            "min": [min(p[i] for p in positions) for i in range(3)],
            "max": [max(p[i] for p in positions) for i in range(3)],
        })
        pos_accessor = len(self.accessors) - 1

        indices = [i for face in _BOX_FACES for i in face]
        idx_view = self._append(
            b"".join(struct.pack("<H", i) for i in indices),
            target=TARGET_ELEMENT_ARRAY_BUFFER)
        self.accessors.append({
            "bufferView": idx_view, "componentType": COMPONENT_TYPE_UNSIGNED_SHORT,
            "count": len(indices), "type": "SCALAR",
            "min": [min(indices)], "max": [max(indices)],
        })
        idx_accessor = len(self.accessors) - 1

        attributes = {"POSITION": pos_accessor}
        for enabled, key in ((texcoord0, "TEXCOORD_0"), (texcoord1, "TEXCOORD_1")):
            if not enabled:
                continue
            uv_view = self._append(
                b"".join(struct.pack("<2f", *uv) for uv in _BOX_UVS),
                target=TARGET_ARRAY_BUFFER)
            self.accessors.append({
                "bufferView": uv_view, "componentType": COMPONENT_TYPE_FLOAT,
                "count": len(_BOX_UVS), "type": "VEC2",
            })
            attributes[key] = len(self.accessors) - 1

        self.meshes.append({"primitives": [
            {"attributes": attributes, "indices": idx_accessor}]})
        return len(self.meshes) - 1

    def build(self, nodes: list, *, extras: dict | None = None,
              extensions_required: list | None = None) -> bytes:
        """Assembles the full glTF JSON around every mesh added so far
        and returns the finished GLB (JSON chunk + BIN chunk). Computes
        and emits a default scene with the root nodes (nodes not listed
        in any node's children) so three.js and Blender can instantiate it."""
        gltf: dict = {"asset": {"version": "2.0"}, "nodes": nodes}
        if self.meshes:
            gltf["meshes"] = self.meshes
            gltf["accessors"] = self.accessors
            gltf["bufferViews"] = self.buffer_views
            gltf["buffers"] = [{"byteLength": len(self._binary)}]
        if extensions_required:
            gltf["extensionsRequired"] = extensions_required
        if extras is not None:
            gltf["extras"] = extras

        # Compute root nodes: those not listed in any node's children
        all_children = set()
        for node in nodes:
            if "children" in node:
                all_children.update(node["children"])
        roots = [i for i in range(len(nodes)) if i not in all_children]
        gltf["scene"] = 0
        gltf["scenes"] = [{"nodes": roots}]

        return build_glb(gltf, bytes(self._binary))
