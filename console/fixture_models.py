"""What the Console's 3D fixture view fetches over HTTP: each loaded Room
fixture's pixel layout (JSON) and its model (.glb, the instrument's `model_path`).

Routes are keyed by fixture name in the loaded Room, never by a client path:
`fixture_route` parses the name out of the path (percent-decoding it, since
the client sends encodeURIComponent(name)) and does an exact dict lookup,
so a name with `/`, `..` or anything else a fixture does not carry simply
misses. The .glb is read from disk inside `fixture_route`, which runs on the
HTTP thread; the tick thread only builds the (small) layout JSON.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import unquote

from control.room_view import model_view_status

LAYOUT_PREFIX = "/fixture-layout/"
LAYOUT_SUFFIX = ".json"
MODEL_PREFIX = "/fixture-model/"
MODEL_SUFFIX = ".glb"

_HEADERS = {"Cache-Control": "no-cache"}


@dataclass(frozen=True)
class FixtureModel:
    name: str
    sha256: str
    model_path: str
    layout_json: bytes


def _layout_json(fixture, sha256: str) -> bytes:
    pixels = []
    for px in fixture.instrument.layout:
        room_zones = [f"{fixture.name}.{z.name}" for z in fixture.zones
                      if z.start <= px.index < z.start + z.count]
        pixels.append({
            "index": px.index,
            "x_mm": px.x_mm,
            "y_mm": px.y_mm,
            "z_mm": px.z_mm,
            "size": px.size,
            "model_zone": px.zone,
            "room_zones": room_zones,
        })
    doc = {"fixture": fixture.name, "sha256": sha256, "pixels": pixels}
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def build_fixture_models(profile) -> dict[str, FixtureModel]:
    """One entry per fixture whose model the 3D view can load (status `ok`)."""
    out: dict[str, FixtureModel] = {}
    for fixture in profile.fixtures:
        inst = fixture.instrument
        status = model_view_status(inst)
        if status["status"] != "ok" or not inst.model_path:
            continue
        out[fixture.name] = FixtureModel(
            name=fixture.name,
            sha256=status["sha256"],
            model_path=inst.model_path,
            layout_json=_layout_json(fixture, status["sha256"]),
        )
    return out


def _name_from(path: str, prefix: str, suffix: str) -> str | None:
    if not (path.startswith(prefix) and path.endswith(suffix)):
        return None
    name = unquote(path[len(prefix):len(path) - len(suffix)])
    return name or None


def fixture_route(models: dict[str, FixtureModel],
                  path: str) -> tuple[bytes, str, dict] | None:
    """The ConsoleServer route provider body: (body, content_type, headers)
    or None (404). `models` is read as one consistent snapshot."""
    name = _name_from(path, LAYOUT_PREFIX, LAYOUT_SUFFIX)
    if name is not None:
        model = models.get(name)
        if model is None:
            return None
        return model.layout_json, "application/json", dict(_HEADERS)
    name = _name_from(path, MODEL_PREFIX, MODEL_SUFFIX)
    if name is not None:
        model = models.get(name)
        if model is None:
            return None
        try:
            with open(model.model_path, "rb") as f:
                body = f.read()
        except OSError:
            return None
        return body, "model/gltf-binary", dict(_HEADERS)
    return None
