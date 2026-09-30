"""Pure-Python helpers for the LED-map bake pipeline (spec: mm-tuneshroom
docs/superpowers/specs/2026-09-28-3d-tuneshroom-model-and-view-design.md,
sections 4.1 and 5.3), importable and unit-testable without `bpy`
(Blender's bundled Python module, only importable from inside Blender).
tools/bake_model.py (Task 14) stays a thin bpy script; every piece of its
logic that does not need bpy lives here instead. This module also backs
tools/generate_model_fixture.py's "mmbake" fixture (Task 7), which is a
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

import json
import math
import re
import struct
import zlib
from pathlib import Path

from control.model_layout import ModelLayoutError, layout_to_json, read_glb_json

GLB_MAGIC = 0x46546C67
JSON_CHUNK_TYPE = 0x4E4F534A
BIN_CHUNK_TYPE = 0x004E4942

_LED_NAME_RE = re.compile(r"^LED_(\d{3})$")


class BakeContractError(Exception):
    """A baked .glb (or an mm_bake block) breaks spec section 4.1."""


class InjectBakeError(BakeContractError):
    """inject_bake refused its input or its own output."""


LIGHTMAP_TEXCOORD = "TEXCOORD_1"
MM_BAKE_KEYS = ("source_sha256", "pixels", "map_scale", "maps", "uv",
                "resolution", "blender", "layout")
_LAYOUT_KEYS = ("index", "x_mm", "y_mm", "z_mm", "size", "zone")
_SIZES = ("small", "medium", "large")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ZONE_RE = re.compile(r"^[a-z0-9_]+$")


def encode_png_rgba8(width: int, height: int, pixels: bytes) -> bytes:
    """A minimal, dependency-free 8-bit RGBA PNG encoder (stdlib `zlib` +
    `struct` only), used both by a real bake's normalised light maps and
    by tools/generate_model_fixture.py's flat stand-in maps (spec section
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
    body = json.dumps(gltf, allow_nan=False).encode("utf-8")
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
    for mesh_idx, mesh in enumerate(gltf.get("meshes", [])):
        for prim_idx, prim in enumerate(mesh.get("primitives", [])):
            if LIGHTMAP_TEXCOORD not in prim.get("attributes", {}):
                mesh_name = mesh.get("name")
                located = (f"mesh {mesh_idx} ({mesh_name!r})" if mesh_name
                          else f"mesh {mesh_idx}")
                raise BakeContractError(
                    f"{located} primitive {prim_idx} has no {LIGHTMAP_TEXCOORD} "
                    f"accessor; the Blender exporter did not write the "
                    f"lightmap UV set; check export_texcoords")


def _refuse_remaining_marker_meshes(gltf: dict) -> None:
    for node in gltf.get("nodes", []):
        name = node.get("name", "")
        if _LED_NAME_RE.match(name) and "mesh" in node:
            raise BakeContractError(
                f"node {name!r} is still an LED marker mesh; a bake must "
                f"delete markers before export (spec section 5.3 step 1)")


def _validate_buffers_structure(gltf: dict) -> None:
    """Refuse if the GLB has more than one buffer, or if buffers[0] has a uri
    (external buffer). A GLB's buffer 0 must be the embedded BIN chunk."""
    buffers = gltf.get("buffers")
    if buffers is None:
        return
    if len(buffers) > 1:
        raise BakeContractError(
            "the GLB has multiple buffers; inject_bake only supports a single "
            "embedded buffer (buffer 0)")
    if buffers[0].get("uri"):
        raise BakeContractError(
            "buffers[0] has a uri; inject_bake only supports embedded binary "
            "buffers, not external ones")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_mm_bake(mm_bake) -> None:
    """Spec section 4.1's root extras.mm_bake, checked field by field with
    the same rules mm-tuneshroom's lib/render/model_layout.dart applies
    (plus uv/resolution/blender, which the spec lists and this writer
    always sets). Raises BakeContractError naming the field and rule."""
    if not isinstance(mm_bake, dict):
        raise BakeContractError("mm_bake must be an object")
    missing = [k for k in MM_BAKE_KEYS if k not in mm_bake]
    if missing:
        raise BakeContractError(f"mm_bake is missing required key(s) {missing}")
    extra = [k for k in mm_bake if k not in MM_BAKE_KEYS]
    if extra:
        raise BakeContractError(f"mm_bake has unexpected key(s) {sorted(extra)}")
    sha = mm_bake["source_sha256"]
    if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
        raise BakeContractError("mm_bake.source_sha256 must be 64 lowercase hex characters")
    pixels = mm_bake["pixels"]
    if not _is_int(pixels) or pixels < 1:
        raise BakeContractError("mm_bake.pixels must be an integer >= 1")
    scale = mm_bake["map_scale"]
    if (not isinstance(scale, (int, float)) or isinstance(scale, bool)
            or not math.isfinite(scale) or scale <= 0):
        raise BakeContractError("mm_bake.map_scale must be a finite number > 0")
    if mm_bake["uv"] != LIGHTMAP_TEXCOORD:
        raise BakeContractError(f"mm_bake.uv must be {LIGHTMAP_TEXCOORD!r}")
    if not _is_int(mm_bake["resolution"]) or mm_bake["resolution"] < 1:
        raise BakeContractError("mm_bake.resolution must be an integer >= 1")
    if not isinstance(mm_bake["blender"], str) or not mm_bake["blender"]:
        raise BakeContractError("mm_bake.blender must be a non-empty string")
    maps = mm_bake["maps"]
    if not isinstance(maps, list) or not all(_is_int(m) and m >= 0 for m in maps):
        raise BakeContractError("mm_bake.maps must be a list of non-negative integers")
    expected_maps = (pixels + 3) // 4
    if len(maps) != expected_maps:
        raise BakeContractError(
            f"mm_bake.maps has {len(maps)} entries, expected {expected_maps} "
            f"(one per group of 4 pixels)")
    layout = mm_bake["layout"]
    if not isinstance(layout, list) or len(layout) != pixels:
        raise BakeContractError(
            f"mm_bake.layout must be a list of {pixels} entries (mm_bake.pixels)")
    for i, entry in enumerate(layout):
        if not isinstance(entry, dict):
            raise BakeContractError(f"mm_bake.layout[{i}] must be an object")
        absent = [k for k in _LAYOUT_KEYS if k not in entry]
        if absent:
            raise BakeContractError(f"mm_bake.layout[{i}] is missing key(s) {absent}")
        if not _is_int(entry["index"]) or entry["index"] != i:
            raise BakeContractError(
                f"mm_bake.layout[{i}].index must be {i} (indices run 0 to "
                f"{pixels - 1} in order), got {entry['index']!r}")
        if not all(_is_int(entry[k]) for k in ("x_mm", "y_mm", "z_mm")):
            raise BakeContractError(f"mm_bake.layout[{i}].x_mm/y_mm/z_mm must be integers")
        if entry["size"] not in _SIZES:
            raise BakeContractError(
                f"mm_bake.layout[{i}].size must be one of small, medium, large, "
                f"got {entry['size']!r}")
        zone = entry["zone"]
        if zone is not None:
            if not isinstance(zone, str) or not _ZONE_RE.fullmatch(zone):
                raise BakeContractError(
                    f"mm_bake.layout[{i}].zone {zone!r} must match [a-z0-9_]+")
            if zone == "primary":
                raise BakeContractError(f"mm_bake.layout[{i}].zone 'primary' is reserved")


def validate_baked_glb(data: bytes, *, path: str) -> dict:
    """The whole spec section 4.1 contract on a baked file's bytes: no
    LED marker meshes, TEXCOORD_1 on every primitive, a valid root
    extras.mm_bake, and every maps entry naming a PNG texture. Returns
    the mm_bake block. Used by inject_bake (on its own output),
    tools/export_models.py, and the committed-bake test."""
    try:
        gltf = read_glb_json(data, path=path)
    except ModelLayoutError as exc:
        raise BakeContractError(str(exc)) from exc
    try:
        if not isinstance(gltf, dict):
            raise BakeContractError("glTF root must be a JSON object")
        _refuse_remaining_marker_meshes(gltf)
        _refuse_missing_texcoord1(gltf)
        extras = gltf.get("extras")
        mm_bake = extras.get("mm_bake") if isinstance(extras, dict) else None
        if mm_bake is None:
            raise BakeContractError("no root extras.mm_bake; this is not a baked model")
        validate_mm_bake(mm_bake)
        textures = gltf.get("textures", [])
        images = gltf.get("images", [])
        for group, tex in enumerate(mm_bake["maps"]):
            if tex >= len(textures):
                raise BakeContractError(
                    f"mm_bake.maps[{group}] = {tex} names no texture "
                    f"(the file has {len(textures)})")
            src = textures[tex].get("source")
            if (not _is_int(src) or not 0 <= src < len(images)
                    or images[src].get("mimeType") != "image/png"):
                raise BakeContractError(
                    f"mm_bake.maps[{group}]: texture {tex} is not a PNG image")
    except BakeContractError as exc:
        raise BakeContractError(f"{path}: {exc}") from exc
    except (IndexError, KeyError, TypeError, AttributeError) as exc:
        raise BakeContractError(f"{path}: malformed glTF: {exc}") from exc
    return mm_bake


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
    buffers, or if buffers[0] is external (has a uri). The final mm_bake
    (with "maps" rewritten) must pass validate_mm_bake, so len(pngs)
    must equal ceil(pixels / 4), and the written file must pass
    validate_baked_glb; any failure raises InjectBakeError."""
    try:
        gltf, binary = _read_glb_full(glb_bytes, path="<inject_bake input>")
        _refuse_remaining_marker_meshes(gltf)
        _refuse_missing_texcoord1(gltf)
        _validate_buffers_structure(gltf)
        out = _append_maps(gltf, bytearray(binary), pngs, mm_bake)
        validate_baked_glb(out, path="<inject_bake output>")
    except InjectBakeError:
        raise
    except (BakeContractError, ModelLayoutError) as exc:
        raise InjectBakeError(str(exc)) from exc
    return out


def _append_maps(gltf: dict, binary: bytearray, pngs: list, mm_bake: dict) -> bytes:
    images = list(gltf.get("images", []))
    textures = list(gltf.get("textures", []))
    buffer_views = list(gltf.get("bufferViews", []))
    samplers = list(gltf.get("samplers", []))

    sampler_idx = None
    if pngs:
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
    validate_mm_bake(extras)
    root_extras = dict(gltf.get("extras") or {})
    root_extras["mm_bake"] = extras
    gltf["extras"] = root_extras

    return _write_glb(gltf, bytes(binary))


class BakeError(Exception):
    """tools/bake_model.py refused to run or to write a result."""


def check_blender_version(actual: tuple, pinned: str) -> str:
    """`actual` is bpy.app.version, a (major, minor, patch) tuple (never
    parse bpy.app.version_string: it carries a suffix like " LTS").
    Refuses any major.minor other than `pinned` ("X.Y"); returns the
    full "X.Y.Z" recorded as mm_bake.blender."""
    found = ".".join(str(n) for n in actual[:3])
    want = tuple(int(part) for part in pinned.split(".")[:2])
    if tuple(actual[:2]) != want:
        raise BakeError(
            f"tools/bake_model.py is pinned to Blender {pinned}.x, found {found}; "
            f"bake on the host documented in docs/MM_TERRARIUM.md (LED layout "
            f"models), or re-run tools/blender_probe.py and update PINNED_BLENDER")
    return found


def group_leds_by_four(pixel_count: int) -> list:
    """LED index groups, one per RGBA texture: LEDs 4i..4i+3 go to
    R, G, B, A of texture i (spec section 5.3 step 4)."""
    return [list(range(i, min(i + 4, pixel_count))) for i in range(0, pixel_count, 4)]


def layout_to_blender_m(pixel) -> tuple:
    """A layout pixel's Blender scene position in metres. The layout is
    Z-up millimetres (D12) and Blender's glTF importer converts the
    source's Y-up to the same Z-up frame, so it is mm / 1000 per axis."""
    return (pixel.x_mm / 1000.0, pixel.y_mm / 1000.0, pixel.z_mm / 1000.0)


def flip_rows(values: list, width: int, height: int) -> list:
    """Reverse the row order of a single-channel, row-major buffer.
    Blender's Image.pixels is bottom row first; PNG rows and glTF
    texture space (UV (0,0) = top-left) are top row first, and Blender's
    glTF exporter writes v' = 1 - v, so an unflipped map would be upside
    down on the model."""
    if len(values) != width * height:
        raise ValueError(f"values must be width*height ({width * height}), got {len(values)}")
    rows = [values[r * width:(r + 1) * width] for r in range(height)]
    return [v for row in reversed(rows) for v in row]


def normalise_maps(raw_maps: dict) -> tuple:
    """Divide every LED's texels by the brightest texel across all LEDs
    (spec section 5.3 step 4); map_scale is that peak, so
    texel * map_scale recovers the baked value. Negative texels (bake
    noise) clamp to 0. An all-black bake is refused: it means no LED's
    light reached any baked surface, which is a modelling or material
    error, not a result."""
    clamped = {led: [t if t > 0.0 else 0.0 for t in texels]
               for led, texels in raw_maps.items()}
    peak = max((max(texels) for texels in clamped.values() if texels), default=0.0)
    if peak <= 0.0:
        raise BakeError(
            "every light map is black: no LED lit any baked surface; check the "
            "markers sit where the surface can see them and the materials "
            "are not fully opaque around them")
    return {led: [t / peak for t in texels] for led, texels in clamped.items()}, peak


def _to_byte(value: float) -> int:
    if value <= 0.0:
        return 0
    if value >= 1.0:
        return 255
    return int(value * 255.0 + 0.5)


def quantise_group_rgba8(group: list, normalised: dict, texel_count: int) -> bytes:
    """One texture's raw RGBA8 bytes (encode_png_rgba8's input): the LEDs
    of `group` in R, G, B, A order, unused channels of a short final
    group 0."""
    out = bytearray(texel_count * 4)
    for channel, led in enumerate(group):
        texels = normalised[led]
        if len(texels) != texel_count:
            raise ValueError(f"LED {led} map has {len(texels)} texels, expected {texel_count}")
        out[channel::4] = bytes(_to_byte(t) for t in texels)
    return bytes(out)


def build_mm_bake_extras(*, source_sha256: str, layout_pixels: tuple, map_scale: float,
                         resolution: int, blender_version: str) -> dict:
    """extras.mm_bake for a real bake (spec section 4.1). "maps" is a
    placeholder in the right shape; inject_bake rewrites it with the
    actual texture indices. Validated before it is returned."""
    extras = {
        "source_sha256": source_sha256,
        "pixels": len(layout_pixels),
        "map_scale": map_scale,
        "maps": list(range(len(group_leds_by_four(len(layout_pixels))))),
        "uv": LIGHTMAP_TEXCOORD,
        "resolution": resolution,
        "blender": blender_version,
        "layout": layout_to_json(layout_pixels),
    }
    validate_mm_bake(extras)
    return extras


def bake_output_path(model_path: Path) -> Path:
    """<source dir>/<source stem>.baked.glb, the file the catalog's
    stale-bake check (control/terrarium_config.py) looks for."""
    return model_path.with_name(f"{model_path.stem}.baked.glb")
