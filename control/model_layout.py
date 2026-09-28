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
