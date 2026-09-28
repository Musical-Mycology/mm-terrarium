"""Generates mm-terrarium's shared marker-layout fixture pair -- the
source-side .glb, the Blender-free "mmbake" stand-in for a real bake, and
their common expected layout. The Python parser (control/model_layout.py)
is tested against the source-side .glb; per D13's "one parser" principle
the mm-tuneshroom app never re-parses a glTF scene graph at all -- it
reads the already-computed layout straight out of
marker_fixture.mmbake.glb's `extras.mm_bake.layout` -- so
tools/export_models.py (Task 15) copies marker_fixture.mmbake.glb and
expected_layout.json (the fixture this generator's `layout` is checked
against, and what PR B's in-browser Three.js check renders per spec
section 6.4) -- never the source fixture -- into the mm-tuneshroom
checkout it targets.

12 LEDs: an 8-marker ring at z=100mm, radius 40mm (5mm-diameter boxes,
"medium"), and a 4-marker stem along z from 20mm to 80mm at x=y=0
(3mm-diameter boxes, "small"), plus one non-marker body mesh so the
parser is exercised against a document that isn't 100% markers.

    .venv/bin/python -m tools.generate_model_fixture
"""
from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

from control.model_layout import parse_model_layout
from tests.glb_builder import GlbBuilder
from tools.model_bake_helpers import encode_png_rgba8, inject_bake

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "models"

RING_COUNT = 8
STEM_COUNT = 4
RING_RADIUS_MM = 40.0
RING_Z_MM = 100.0
RING_MARKER_DIAMETER_MM = 5.0    # "medium"
STEM_MARKER_DIAMETER_MM = 3.0    # "small"
STEM_Z_START_MM = 20.0
STEM_Z_STEP_MM = 20.0
MAP_RESOLUTION = 4   # flat 4x4 PNGs: a real image, small enough to commit
_GROUP_COLOURS = [(64, 64, 64, 255), (128, 128, 128, 255), (192, 192, 192, 255)]


def _layout_mm_to_gltf_m(x_mm: float, y_mm: float, z_mm: float) -> tuple:
    """Inverse of parse_model_layout's axis conversion: layout
    (X, Y, Z) mm -> gltf (X, Y, Z) metres, i.e. gltf = (x/1000, z/1000,
    -y/1000)."""
    return (x_mm / 1000.0, z_mm / 1000.0, -y_mm / 1000.0)


def _pixel_positions_mm() -> list:
    """12 (x_mm, y_mm, z_mm, diameter_mm, zone) tuples, ring first (index
    0-7) then stem (index 8-11), matching the artist convention's index
    order (spec section 3: "the LED's position on the physical data
    chain")."""
    out = []
    for i in range(RING_COUNT):
        angle = 2.0 * math.pi * i / RING_COUNT
        out.append((float(round(RING_RADIUS_MM * math.cos(angle))),
                    float(round(RING_RADIUS_MM * math.sin(angle))),
                    RING_Z_MM, RING_MARKER_DIAMETER_MM, "ring"))
    for i in range(STEM_COUNT):
        out.append((0.0, 0.0, STEM_Z_START_MM + i * STEM_Z_STEP_MM,
                    STEM_MARKER_DIAMETER_MM, "stem"))
    return out


def build_source_fixture() -> bytes:
    """marker_fixture.glb: 12 real, Blender-importable LED marker box
    meshes under LEDs::ring / LEDs::stem, plus one non-marker body mesh."""
    builder = GlbBuilder()
    nodes = [
        {"name": "LEDs", "children": [1, 2]},
        {"name": "ring", "children": []},
        {"name": "stem", "children": []},
    ]
    for idx, (x_mm, y_mm, z_mm, diameter_mm, zone) in enumerate(_pixel_positions_mm()):
        center_m = _layout_mm_to_gltf_m(x_mm, y_mm, z_mm)
        mesh_idx = builder.add_box_mesh(center_m, (diameter_mm / 2.0) / 1000.0)
        marker_node_idx = len(nodes)
        nodes.append({"name": f"LED_{idx:03d}", "mesh": mesh_idx})
        nodes[1 if zone == "ring" else 2]["children"].append(marker_node_idx)

    body_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06, texcoord0=True)
    nodes.append({"name": "Body", "mesh": body_idx})
    return builder.build(nodes)


def build_mmbake_fixture(layout_pixels: tuple, source_sha256: str) -> bytes:
    """marker_fixture.mmbake.glb: a Blender-free stand-in for a real bake
    (spec section 6.4) -- the body mesh only (no markers), TEXCOORD_0 +
    TEXCOORD_1, flat 4x4 PNG maps, and a root extras.mm_bake built from
    `layout_pixels` -- control.model_layout.parse_model_layout's own
    output on the source fixture, the same parser a real bake runs (spec
    D13: "one parser")."""
    builder = GlbBuilder()
    body_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06,
                                     texcoord0=True, texcoord1=True)
    body_only_glb = builder.build([{"name": "Body", "mesh": body_idx}])

    layout = [dataclasses.asdict(p) for p in layout_pixels]
    mm_bake = {
        "source_sha256": source_sha256,
        "pixels": len(layout_pixels),
        "map_scale": 1.0,
        "uv": "TEXCOORD_1",
        "resolution": MAP_RESOLUTION,
        "blender": "n/a (synthetic fixture, no Blender involved)",
        "layout": layout,
    }
    group_count = (len(layout_pixels) + 3) // 4
    pngs = [encode_png_rgba8(MAP_RESOLUTION, MAP_RESOLUTION,
                              bytes(colour) * (MAP_RESOLUTION * MAP_RESOLUTION))
            for colour in _GROUP_COLOURS[:group_count]]
    return inject_bake(body_only_glb, pngs, mm_bake)


def build_fixture() -> tuple:
    """(source_glb_bytes, mmbake_glb_bytes, expected_layout_dict), all
    deterministic."""
    source_glb = build_source_fixture()
    layout = parse_model_layout(source_glb, path="marker_fixture.glb",
                                 pixel_count=RING_COUNT + STEM_COUNT)
    mmbake_glb = build_mmbake_fixture(layout.pixels, layout.model_sha256)
    expected_layout = {
        "model_sha256": layout.model_sha256,
        "pixels": [dataclasses.asdict(p) for p in layout.pixels],
    }
    return source_glb, mmbake_glb, expected_layout


def main() -> None:
    source_glb, mmbake_glb, expected_layout = build_fixture()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURE_DIR / "marker_fixture.glb").write_bytes(source_glb)
    (FIXTURE_DIR / "marker_fixture.mmbake.glb").write_bytes(mmbake_glb)
    (FIXTURE_DIR / "expected_layout.json").write_text(
        json.dumps(expected_layout, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE_DIR}/marker_fixture.glb, marker_fixture.mmbake.glb, "
          f"and expected_layout.json")


if __name__ == "__main__":
    main()
