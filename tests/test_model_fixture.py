"""Guards the three committed shared fixture files (tests/fixtures/models/)
against drift from tools/generate_model_fixture.py, the way
test_export_contract.py guards contract.json against the code it was
generated from."""
import json
from pathlib import Path

from control.model_layout import parse_model_layout, read_glb_json
from tools.generate_model_fixture import build_fixture

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "models"


def test_committed_fixtures_match_regeneration():
    source_glb, mmbake_glb, expected_layout = build_fixture()
    assert (FIXTURE_DIR / "marker_fixture.glb").read_bytes() == source_glb
    assert (FIXTURE_DIR / "marker_fixture.mmbake.glb").read_bytes() == mmbake_glb
    committed_json = json.loads((FIXTURE_DIR / "expected_layout.json").read_text())
    assert committed_json == expected_layout


def test_source_fixture_has_twelve_pixels_in_ring_and_stem_zones():
    glb_bytes = (FIXTURE_DIR / "marker_fixture.glb").read_bytes()
    layout = parse_model_layout(glb_bytes, path="marker_fixture.glb", pixel_count=12)
    assert [p.zone for p in layout.pixels] == ["ring"] * 8 + ["stem"] * 4


def test_mmbake_fixture_has_no_marker_meshes_and_a_root_mm_bake():
    gltf = read_glb_json((FIXTURE_DIR / "marker_fixture.mmbake.glb").read_bytes(),
                          path="marker_fixture.mmbake.glb")
    names = [n.get("name", "") for n in gltf["nodes"]]
    assert not any(n.startswith("LED_") for n in names)
    mm_bake = gltf["extras"]["mm_bake"]
    assert mm_bake["uv"] == "TEXCOORD_1"
    assert len(mm_bake["layout"]) == 12
    assert mm_bake["maps"] == [0, 1, 2]  # 12 pixels -> 3 groups of 4


def test_mmbake_fixture_layout_matches_the_source_fixtures_own_parse():
    source_layout = parse_model_layout(
        (FIXTURE_DIR / "marker_fixture.glb").read_bytes(),
        path="marker_fixture.glb", pixel_count=12)
    gltf = read_glb_json((FIXTURE_DIR / "marker_fixture.mmbake.glb").read_bytes(),
                          path="marker_fixture.mmbake.glb")
    assert gltf["extras"]["mm_bake"]["layout"] == [
        {"index": p.index, "x_mm": p.x_mm, "y_mm": p.y_mm, "z_mm": p.z_mm,
         "size": p.size, "zone": p.zone}
        for p in source_layout.pixels
    ]


import hashlib

from tools.model_bake_helpers import validate_baked_glb


def test_committed_real_bake_is_a_contract_valid_bake_of_the_source_fixture():
    """The PR T2 gate's evidence: a real headless-Blender bake of
    marker_fixture.glb, made on the bake host with the pinned Blender
    (docs/MM_TERRARIUM.md, LED layout models)."""
    path = FIXTURE_DIR / "marker_fixture.baked.glb"
    mm_bake = validate_baked_glb(path.read_bytes(), path=str(path))
    source = (FIXTURE_DIR / "marker_fixture.glb").read_bytes()
    expected = json.loads((FIXTURE_DIR / "expected_layout.json").read_text())
    assert mm_bake["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert mm_bake["layout"] == expected["pixels"]
    assert mm_bake["blender"].startswith("4.5.")
    assert mm_bake["resolution"] == 256
