import struct

import pytest

from control.model_layout import read_glb_json
from tests.glb_builder import GlbBuilder
from tools.model_bake_helpers import InjectBakeError, encode_png_rgba8, inject_bake


def _flat_png(width: int, height: int, rgba: tuple) -> bytes:
    return encode_png_rgba8(width, height, bytes(rgba) * (width * height))


def test_encode_png_rgba8_starts_with_the_png_signature_and_declares_rgba():
    png = _flat_png(2, 2, (10, 20, 30, 255))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", png[16:26])
    assert (width, height, bit_depth, color_type) == (2, 2, 8, 6)  # 6 = RGBA


def test_encode_png_rgba8_refuses_the_wrong_pixel_length():
    with pytest.raises(ValueError, match=r"width\*height\*4"):
        encode_png_rgba8(2, 2, b"\x00" * 3)


def _mm_bake(pixels: int) -> dict:
    """A valid mm_bake for `pixels` LEDs (inject_bake rewrites "maps")."""
    return {"source_sha256": "ab" * 32, "pixels": pixels, "map_scale": 1.0,
            "maps": [], "uv": "TEXCOORD_1", "resolution": 2, "blender": "test",
            "layout": [{"index": i, "x_mm": 0, "y_mm": 0, "z_mm": i,
                        "size": "medium", "zone": None} for i in range(pixels)]}


def _pngs(pixels: int) -> list:
    return [_flat_png(2, 2, (i, i, i, 255)) for i in range((pixels + 3) // 4)]


def _bakeable_glb_bytes() -> bytes:
    """One non-marker mesh with both TEXCOORD_0 and TEXCOORD_1 -- what a
    real Blender export produces after the bake (spec section 4.1)."""
    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06,
                                     texcoord0=True, texcoord1=True)
    return builder.build([{"name": "Body", "mesh": mesh_idx}])


def test_inject_bake_adds_images_textures_sampler_and_root_extras():
    png = _flat_png(2, 2, (1, 2, 3, 4))
    mm_bake = {"source_sha256": "ab" * 32, "pixels": 1, "map_scale": 1.0,
               "resolution": 2, "blender": "4.2", "uv": "TEXCOORD_1",
               "layout": [{"index": 0, "x_mm": 0, "y_mm": 0, "z_mm": 0,
                           "size": "medium", "zone": None}]}
    out = inject_bake(_bakeable_glb_bytes(), [png], mm_bake)
    gltf = read_glb_json(out, path="out.glb")
    assert len(gltf["images"]) == 1
    assert gltf["images"][0]["mimeType"] == "image/png"
    assert len(gltf["textures"]) == 1
    sampler = gltf["samplers"][gltf["textures"][0]["sampler"]]
    assert sampler == {"magFilter": 9729, "minFilter": 9729,
                        "wrapS": 33071, "wrapT": 33071}
    root_extras = gltf["extras"]["mm_bake"]
    assert root_extras["maps"] == [0]
    assert root_extras["layout"] == mm_bake["layout"]
    assert root_extras["source_sha256"] == "ab" * 32
    assert "mm_bake" not in (gltf.get("scenes", [{}])[0] or {}).get("extras", {})


def test_inject_bake_refuses_a_png_count_that_does_not_match_pixels():
    with pytest.raises(InjectBakeError, match="maps has 0 entries, expected 1"):
        inject_bake(_bakeable_glb_bytes(), [], _mm_bake(1))


def test_inject_bake_orders_maps_by_png_order():
    pngs = [_flat_png(2, 2, (i, i, i, 255)) for i in range(3)]
    out = inject_bake(_bakeable_glb_bytes(), pngs, _mm_bake(12))
    gltf = read_glb_json(out, path="out.glb")
    assert gltf["extras"]["mm_bake"]["maps"] == [0, 1, 2]
    assert len(gltf["textures"]) == 3


def test_inject_bake_refuses_missing_texcoord1():
    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.0, 0.0, 0.0), 0.06, texcoord0=True)
    glb_bytes = builder.build([{"name": "Body", "mesh": mesh_idx}])
    with pytest.raises(InjectBakeError, match="TEXCOORD_1"):
        inject_bake(glb_bytes, [], {"pixels": 0, "layout": []})


def test_inject_bake_missing_texcoord1_names_the_mesh_and_primitive():
    """Minor finding: the error must name the offending mesh/primitive, as
    inject_bake's own docstring promises ("naming the offending mesh or
    rule")."""
    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.0, 0.0, 0.0), 0.06, texcoord0=True)
    glb_bytes = builder.build([{"name": "Body", "mesh": mesh_idx}])
    with pytest.raises(InjectBakeError, match=f"mesh {mesh_idx}") as exc:
        inject_bake(glb_bytes, [], {"pixels": 0, "layout": []})
    assert "primitive 0" in str(exc.value)


def test_inject_bake_refuses_a_remaining_marker_mesh():
    builder = GlbBuilder()
    body_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06,
                                     texcoord0=True, texcoord1=True)
    marker_idx = builder.add_box_mesh((0.0, 0.0, 0.0), 0.002)
    nodes = [{"name": "Body", "mesh": body_idx},
             {"name": "LED_000", "mesh": marker_idx}]
    glb_bytes = builder.build(nodes)
    with pytest.raises(InjectBakeError, match="LED_000"):
        inject_bake(glb_bytes, [], {"pixels": 1, "layout": []})


def test_inject_bake_output_is_itself_a_valid_glb_with_a_larger_buffer():
    before = _bakeable_glb_bytes()
    before_gltf = read_glb_json(before, path="before.glb")
    png = _flat_png(2, 2, (9, 9, 9, 9))
    out = inject_bake(before, [png], _mm_bake(4))
    after_gltf = read_glb_json(out, path="out.glb")
    assert after_gltf["buffers"][0]["byteLength"] > before_gltf["buffers"][0]["byteLength"]


def test_inject_bake_preserves_existing_keys_on_buffers_0():
    """(a) An existing extra key on buffers[0] survives the update."""
    from tools.model_bake_helpers import _write_glb, _read_glb_full
    import json

    # Build a GLB with an extra key on buffers[0]
    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06,
                                     texcoord0=True, texcoord1=True)
    glb_bytes = builder.build([{"name": "Body", "mesh": mesh_idx}])

    # Modify the GLB to add an extra key on buffers[0]
    gltf, binary = _read_glb_full(glb_bytes, path="test.glb")
    gltf["buffers"][0]["customKey"] = "customValue"
    glb_with_custom = _write_glb(gltf, binary)

    # Now inject a bake
    png = _flat_png(2, 2, (1, 2, 3, 4))
    out = inject_bake(glb_with_custom, [png], _mm_bake(1))

    # Verify the custom key survived
    result_gltf = read_glb_json(out, path="out.glb")
    assert result_gltf["buffers"][0]["customKey"] == "customValue"


def test_inject_bake_refuses_multiple_buffers():
    """(b) Two buffers → InjectBakeError."""
    from tools.model_bake_helpers import _write_glb, _read_glb_full

    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06,
                                     texcoord0=True, texcoord1=True)
    glb_bytes = builder.build([{"name": "Body", "mesh": mesh_idx}])

    # Modify to have two buffers
    gltf, binary = _read_glb_full(glb_bytes, path="test.glb")
    gltf["buffers"] = [
        {"byteLength": len(binary)},
        {"byteLength": 100}
    ]
    glb_with_two_buffers = _write_glb(gltf, binary)

    # Should refuse
    with pytest.raises(InjectBakeError, match="multiple.*buffer"):
        inject_bake(glb_with_two_buffers, [], {"pixels": 1, "layout": []})


def test_inject_bake_refuses_buffers_0_with_uri():
    """(c) buffers[0] with a uri (external buffer) → InjectBakeError."""
    from tools.model_bake_helpers import _write_glb, _read_glb_full

    builder = GlbBuilder()
    mesh_idx = builder.add_box_mesh((0.0, 0.05, 0.0), 0.06,
                                     texcoord0=True, texcoord1=True)
    glb_bytes = builder.build([{"name": "Body", "mesh": mesh_idx}])

    # Modify buffers[0] to have a uri
    gltf, binary = _read_glb_full(glb_bytes, path="test.glb")
    gltf["buffers"][0]["uri"] = "external.bin"
    glb_with_uri = _write_glb(gltf, binary)

    # Should refuse
    with pytest.raises(InjectBakeError, match="uri|external"):
        inject_bake(glb_with_uri, [], {"pixels": 1, "layout": []})


def test_inject_bake_creates_buffer_0_when_the_input_declares_none():
    from tools.model_bake_helpers import _read_glb_full, _write_glb
    gltf, binary = _read_glb_full(_bakeable_glb_bytes(), path="t.glb")
    del gltf["buffers"]
    out = inject_bake(_write_glb(gltf, binary), _pngs(1), _mm_bake(1))
    result = read_glb_json(out, path="out.glb")
    assert len(result["buffers"]) == 1
    assert result["buffers"][0]["byteLength"] > len(binary)


import copy
from pathlib import Path

from tools.model_bake_helpers import (
    BakeContractError, _read_glb_full, _write_glb, validate_baked_glb, validate_mm_bake,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "models"


def _valid_bake() -> dict:
    return {**_mm_bake(5), "maps": [0, 1]}


def test_validate_mm_bake_accepts_a_valid_block():
    validate_mm_bake(_valid_bake())


@pytest.mark.parametrize("mutate, match", [
    (lambda b: b.pop("uv"), "missing required key"),
    (lambda b: b.update(extra=1), "unexpected key"),
    (lambda b: b.update(source_sha256="AB" * 32), "source_sha256"),
    (lambda b: b.update(source_sha256="a" * 64 + "\n"), "source_sha256"),
    (lambda b: b.update(pixels=0), "pixels"),
    (lambda b: b.update(pixels=True), "pixels"),
    (lambda b: b.update(map_scale=float("nan")), "map_scale"),
    (lambda b: b.update(map_scale=0), "map_scale"),
    (lambda b: b.update(uv="TEXCOORD_0"), "uv"),
    (lambda b: b.update(resolution=0), "resolution"),
    (lambda b: b.update(blender=""), "blender"),
    (lambda b: b.update(maps=[0]), "maps has 1 entries, expected 2"),
    (lambda b: b.update(maps=[0, -1]), "non-negative"),
    (lambda b: b["layout"].pop(), "layout must be a list of 5"),
    (lambda b: b["layout"][2].update(index=3), r"layout\[2\]\.index"),
    (lambda b: b["layout"][0].update(x_mm=1.5), "x_mm"),
    (lambda b: b["layout"][0].update(size="huge"), "size"),
    (lambda b: b["layout"][0].update(zone="Ring"), "zone"),
    (lambda b: b["layout"][0].update(zone="ring\n"), "zone"),
    (lambda b: b["layout"][0].update(zone="primary"), "primary"),
    (lambda b: b["layout"][0].pop("zone"), "zone"),
])
def test_validate_mm_bake_refuses_each_contract_violation(mutate, match):
    bake = copy.deepcopy(_valid_bake())
    mutate(bake)
    with pytest.raises(BakeContractError, match=match):
        validate_mm_bake(bake)


def test_validate_baked_glb_accepts_the_committed_mmbake_fixture():
    data = (_FIXTURES / "marker_fixture.mmbake.glb").read_bytes()
    mm_bake = validate_baked_glb(data, path="marker_fixture.mmbake.glb")
    assert mm_bake["pixels"] == 12 and mm_bake["maps"] == [0, 1, 2]


def test_validate_baked_glb_refuses_the_source_fixture():
    data = (_FIXTURES / "marker_fixture.glb").read_bytes()
    with pytest.raises(BakeContractError, match="marker_fixture.glb"):
        validate_baked_glb(data, path="marker_fixture.glb")


def test_validate_baked_glb_refuses_a_map_naming_no_texture():
    gltf, binary = _read_glb_full(
        (_FIXTURES / "marker_fixture.mmbake.glb").read_bytes(), path="f.glb")
    gltf["extras"]["mm_bake"]["maps"] = [0, 1, 7]
    with pytest.raises(BakeContractError, match=r"maps\[2\] = 7 names no texture"):
        validate_baked_glb(_write_glb(gltf, binary), path="f.glb")


def test_validate_baked_glb_refuses_a_file_without_mm_bake():
    with pytest.raises(BakeContractError, match="no root extras.mm_bake"):
        validate_baked_glb(_bakeable_glb_bytes(), path="plain.glb")


from control.model_layout import PixelLayout
from tools.model_bake_helpers import (
    MIN_LIGHTMAP_TEXELS, BakeError, bake_output_path, build_mm_bake_extras,
    check_blender_version, flip_rows, group_leds_by_four, layout_to_blender_m,
    lightmap_island_margin, lightmap_texels, normalise_maps,
    quantise_group_rgba8, refuse_thin_lightmap, uv_polygon_area,
)


def test_check_blender_version_accepts_the_pinned_major_minor_and_returns_xyz():
    assert check_blender_version((4, 5, 14), "4.5") == "4.5.14"


def test_check_blender_version_refuses_another_major_minor():
    with pytest.raises(BakeError, match=r"pinned to Blender 4\.5.*found 5\.2\.2"):
        check_blender_version((5, 2, 2), "4.5")


def test_group_leds_by_four():
    assert group_leds_by_four(12) == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]]
    assert group_leds_by_four(5) == [[0, 1, 2, 3], [4]]


def test_layout_to_blender_m_is_layout_mm_over_1000_on_every_axis():
    p = PixelLayout(index=0, x_mm=40, y_mm=-28, z_mm=100, size="medium", zone="ring")
    assert layout_to_blender_m(p) == (0.04, -0.028, 0.1)


def test_flip_rows_turns_blender_bottom_up_rows_into_top_down_rows():
    # 2 wide x 3 tall; Blender row 0 is the BOTTOM row.
    assert flip_rows([1, 2, 3, 4, 5, 6], 2, 3) == [5, 6, 3, 4, 1, 2]


def test_flip_rows_refuses_the_wrong_length():
    with pytest.raises(ValueError, match="width\\*height"):
        flip_rows([1, 2, 3], 2, 2)


def test_normalise_maps_scales_by_the_brightest_texel_across_all_leds():
    normalised, scale = normalise_maps({0: [0.5, 1.0], 1: [2.0, 0.0]})
    assert scale == 2.0
    assert normalised == {0: [0.25, 0.5], 1: [1.0, 0.0]}


def test_normalise_maps_clamps_negative_texels_to_zero():
    normalised, scale = normalise_maps({0: [-0.5, 1.0]})
    assert (normalised, scale) == ({0: [0.0, 1.0]}, 1.0)


def test_normalise_maps_refuses_an_all_black_bake():
    with pytest.raises(BakeError, match="black"):
        normalise_maps({0: [0.0, 0.0], 1: [0.0, 0.0]})


def test_uv_polygon_area_of_a_unit_square_and_a_triangle():
    assert uv_polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)]) == 1.0
    assert uv_polygon_area([(0, 0), (0.5, 0), (0, 0.5)]) == 0.125


def test_uv_polygon_area_ignores_winding_and_degenerate_polygons():
    assert uv_polygon_area([(0, 1), (1, 1), (1, 0), (0, 0)]) == 1.0
    assert uv_polygon_area([(0, 0), (1, 1)]) == 0.0


def test_lightmap_texels_scales_summed_area_by_resolution_squared():
    quarter = [(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)]
    assert lightmap_texels([quarter, quarter], 4) == 8.0


def test_lightmap_island_margin_is_two_bake_margins_as_a_fraction():
    assert lightmap_island_margin(1024, 4) == 8 / 1024


def test_refuse_thin_lightmap_names_the_thinnest_mesh():
    with pytest.raises(BakeError, match=r"'Mesh_39' gets 3 light-map texels at 1024 px \(minimum 16\)"):
        refuse_thin_lightmap({"Body": 900.0, "Mesh_39": 3.2, "Mesh_7": 10.0}, 1024)


def test_refuse_thin_lightmap_passes_at_the_floor():
    refuse_thin_lightmap({"Body": float(MIN_LIGHTMAP_TEXELS)}, 256)


def test_quantise_group_rgba8_packs_leds_into_r_g_b_a_in_group_order():
    normalised = {4: [1.0, 0.0], 5: [0.5, 0.0], 6: [0.0, 1.0]}
    out = quantise_group_rgba8([4, 5, 6], normalised, texel_count=2)
    # texel 0: R=LED4 255, G=LED5 128, B=LED6 0, A=unused 0
    assert out == bytes([255, 128, 0, 0, 0, 0, 255, 0])


def test_quantise_group_rgba8_refuses_a_map_of_the_wrong_length():
    with pytest.raises(ValueError, match="LED 1"):
        quantise_group_rgba8([0, 1], {0: [0.0, 0.0], 1: [0.0]}, texel_count=2)


def test_build_mm_bake_extras_passes_the_contract_and_uses_the_one_serializer():
    layout = tuple(PixelLayout(index=i, x_mm=i, y_mm=0, z_mm=10, size="small",
                               zone=None) for i in range(5))
    extras = build_mm_bake_extras(source_sha256="cd" * 32, layout_pixels=layout,
                                  map_scale=0.87, resolution=256,
                                  blender_version="4.5.14")
    validate_mm_bake(extras)
    assert extras["pixels"] == 5 and extras["maps"] == [0, 1]
    assert extras["uv"] == "TEXCOORD_1" and extras["blender"] == "4.5.14"
    assert extras["layout"][4] == {"index": 4, "x_mm": 4, "y_mm": 0, "z_mm": 10,
                                   "size": "small", "zone": None}


def test_bake_output_path_sits_beside_the_source():
    assert bake_output_path(Path("instruments/models/cap.glb")) == Path(
        "instruments/models/cap.baked.glb")


def test_bake_output_path_matches_the_catalog_stale_bake_check(tmp_path, caplog):
    """Two independently written naming rules must fail together."""
    import hashlib
    import logging

    from control.terrarium_config import _warn_if_bake_stale
    from tools.model_bake_helpers import bake_output_path

    models = tmp_path / "models"
    models.mkdir()
    model_path = models / "cap.glb"
    model_path.write_bytes(b"source")
    source_sha = hashlib.sha256(b"source").hexdigest()
    builder = GlbBuilder()
    builder.add_box_mesh((0.0, 0.05, 0.0), 0.06)
    data = builder.build([{"name": "Body", "mesh": 0}],
                         extras={"mm_bake": {"source_sha256": "0" * 64}})
    (models / "cap.baked.glb").write_bytes(data)
    with caplog.at_level(logging.WARNING):
        _warn_if_bake_stale(model_path, source_sha, "cap")
    stale = [r.getMessage() for r in caplog.records if "stale" in r.getMessage()]
    assert stale and str(bake_output_path(model_path)) in stale[0]
