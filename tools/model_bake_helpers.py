"""Pure-Python helpers for the LED-map bake pipeline (spec: mm-tuneshroom
docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md,
sections 4.1 and 5.3), importable and unit-testable without `bpy`
(Blender's bundled Python module, only importable from inside Blender).
tools/bake_model.py (Task 14) stays a thin bpy script; every piece of its
logic that does not need bpy lives here instead. This module also backs
tests/generate_model_fixture.py's "mmbake" fixture (Task 7), which is a
Blender-free stand-in for a real bake and therefore goes through the same
`inject_bake` a real bake does (spec D13's "one parser" principle extends
here: one bake-file writer, not two).

This file intentionally does NOT import tests/glb_builder.py: that module
is a *test* helper (it lives under tests/, is only ever installed as a
test dependency, and its box-mesh geometry is fixture-building logic that
has no place in the production bake path). `_read_glb_full`/`_write_glb`
below are a small, separately-maintained GLB reader/writer -- the GLB
container format is simple enough (a 12-byte header plus two length-
prefixed chunks) that duplicating ~20 lines of it here is cheaper and
clearer than adding a test->production import.
"""
from __future__ import annotations

import re
import struct
import zlib

from control.model_layout import read_glb_json

GLB_MAGIC = 0x46546C67
JSON_CHUNK_TYPE = 0x4E4F534A
BIN_CHUNK_TYPE = 0x004E4942

_LED_NAME_RE = re.compile(r"^LED_(\d{3})$")


class InjectBakeError(Exception):
    pass


def encode_png_rgba8(width: int, height: int, pixels: bytes) -> bytes:
    """A minimal, dependency-free 8-bit RGBA PNG encoder (stdlib `zlib` +
    `struct` only), used both by a real bake's normalised light maps and
    by tests/generate_model_fixture.py's flat stand-in maps (spec section
    5.3 step 5: "stdlib is preferred so it's shared with the fixture
    path"). `pixels` is exactly `width * height * 4` raw bytes, row-major,
    top row first; this function applies PNG filter type 0 ("None") to
    every scanline itself.
    """
    if len(pixels) != width * height * 4:
        raise ValueError(
            f"pixels must be exactly width*height*4 bytes "
            f"({width * height * 4}), got {len(pixels)}")

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    stride = width * 4
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # filter type 0 for every scanline
        raw.extend(pixels[row * stride:(row + 1) * stride])
    idat = zlib.compress(bytes(raw), level=9)
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _write_glb(gltf: dict, binary: bytes) -> bytes:
    """The production-side twin of tests/glb_builder.build_glb -- see
    this module's docstring for why it is not shared via import."""
    import json
    body = json.dumps(gltf).encode("utf-8")
    body += b" " * ((-len(body)) % 4)
    chunks = struct.pack("<II", len(body), JSON_CHUNK_TYPE) + body
    if binary:
        padded = binary + b"\x00" * ((-len(binary)) % 4)
        chunks += struct.pack("<II", len(padded), BIN_CHUNK_TYPE) + padded
    header = struct.pack("<III", GLB_MAGIC, 2, 12 + len(chunks))
    return header + chunks


def _read_glb_full(data: bytes, *, path: str) -> tuple:
    """(gltf_dict, binary_bytes). Reuses control.model_layout.read_glb_json
    for the header + JSON chunk, then separately locates the optional BIN
    chunk immediately following it -- glTF 2.0 requires chunk 0 to be
    JSON and, when present, chunk 1 to be BIN."""
    gltf = read_glb_json(data, path=path)
    json_chunk_length = struct.unpack_from("<I", data, 12)[0]
    bin_offset = 12 + 8 + json_chunk_length
    binary = b""
    if bin_offset + 8 <= len(data):
        bin_length, bin_type = struct.unpack_from("<II", data, bin_offset)
        if bin_type == BIN_CHUNK_TYPE:
            binary = data[bin_offset + 8: bin_offset + 8 + bin_length]
    return gltf, binary


def _refuse_missing_texcoord1(gltf: dict) -> None:
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if "TEXCOORD_1" not in prim.get("attributes", {}):
                raise InjectBakeError(
                    "a mesh primitive has no TEXCOORD_1 accessor; the "
                    "Blender exporter did not write the lightmap UV set; "
                    "check export_texcoords")


def _refuse_remaining_marker_meshes(gltf: dict) -> None:
    for node in gltf.get("nodes", []):
        name = node.get("name", "")
        if _LED_NAME_RE.match(name) and "mesh" in node:
            raise InjectBakeError(
                f"node {name!r} is still an LED marker mesh; a bake must "
                f"delete markers before export (spec section 5.3 step 1)")


def _validate_buffers_structure(gltf: dict) -> None:
    """Refuse if the GLB has more than one buffer, or if buffers[0] has a uri
    (external buffer). A GLB's buffer 0 must be the embedded BIN chunk."""
    buffers = gltf.get("buffers")
    if buffers is None:
        return
    if len(buffers) > 1:
        raise InjectBakeError(
            "the GLB has multiple buffers; inject_bake only supports a single "
            "embedded buffer (buffer 0)")
    if buffers[0].get("uri"):
        raise InjectBakeError(
            "buffers[0] has a uri; inject_bake only supports embedded binary "
            "buffers, not external ones")


def inject_bake(glb_bytes: bytes, pngs: list, mm_bake: dict) -> bytes:
    """Appends each of `pngs` (raw PNG bytes, one per RGBA-packed
    texture, in the order LED-group 0, 1, 2, ...) to the GLB's binary
    chunk as a new `bufferView`/`image`/`texture`, adds one shared
    LINEAR/LINEAR, CLAMP_TO_EDGE `sampler`, sets the **root**
    `extras.mm_bake` to `mm_bake` with `"maps"` rewritten to the new
    textures' actual indices (spec section 4.1), and re-serialises the
    whole document. Raises InjectBakeError, naming the offending mesh or
    rule, if any mesh primitive lacks `TEXCOORD_1` or if any mesh is
    still named like an LED marker (`LED_###`), if the GLB has multiple
    buffers, or if buffers[0] is external (has a uri)."""
    gltf, binary = _read_glb_full(glb_bytes, path="<inject_bake input>")
    binary = bytearray(binary)

    _refuse_remaining_marker_meshes(gltf)
    _refuse_missing_texcoord1(gltf)
    _validate_buffers_structure(gltf)

    images = list(gltf.get("images", []))
    textures = list(gltf.get("textures", []))
    buffer_views = list(gltf.get("bufferViews", []))
    samplers = list(gltf.get("samplers", []))

    sampler_idx = len(samplers)
    samplers.append({"magFilter": 9729, "minFilter": 9729,
                      "wrapS": 33071, "wrapT": 33071})  # LINEAR/LINEAR, CLAMP_TO_EDGE

    new_texture_indices = []
    for png_bytes in pngs:
        pad = (-len(binary)) % 4
        binary.extend(b"\x00" * pad)
        offset = len(binary)
        binary.extend(png_bytes)
        buffer_views.append({"buffer": 0, "byteOffset": offset,
                              "byteLength": len(png_bytes)})
        image_idx = len(images)
        images.append({"bufferView": len(buffer_views) - 1, "mimeType": "image/png"})
        textures.append({"sampler": sampler_idx, "source": image_idx})
        new_texture_indices.append(len(textures) - 1)

    gltf["images"] = images
    gltf["textures"] = textures
    gltf["bufferViews"] = buffer_views
    gltf["samplers"] = samplers

    # Update buffers: preserve buffers[0]'s existing keys, only update byteLength.
    # Only create a buffers entry if one exists or if we added pngs.
    if gltf.get("buffers") is not None:
        gltf["buffers"][0]["byteLength"] = len(binary)
    elif pngs:
        gltf["buffers"] = [{"byteLength": len(binary)}]

    extras = dict(mm_bake)
    extras["maps"] = new_texture_indices
    root_extras = dict(gltf.get("extras") or {})
    root_extras["mm_bake"] = extras
    gltf["extras"] = root_extras

    return _write_glb(gltf, bytes(binary))
