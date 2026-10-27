import json

from console.fixture_models import build_fixture_models, fixture_route
from control.instrument import Instrument
from control.model_layout import PixelLayout
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone

SHA = "ab" * 32


def _instrument(path, sha=SHA, triangles=10, nbytes=100):
    layout = tuple(PixelLayout(i, i * 10, i * 20, i * 30, "small", "ring" if i < 2 else None)
                   for i in range(3))
    return Instrument(name="t", pixels=3, model_sha256=sha, model_path=str(path),
                      model_bytes=nbytes, model_triangles=triangles, layout=layout)


def _fixture(name, instrument):
    return RoomFixture(name=name, blocks=(RoomBlock(name, 0, 3),),
                       zones=(RoomZone("a", 0, 2), RoomZone("b", 2, 1)),
                       instrument=instrument)


def _models(tmp_path):
    glb = tmp_path / "f.glb"
    glb.write_bytes(b"glTF-bytes")
    profile = RoomProfile(surface_id="s", fixtures=(
        _fixture("f", _instrument(glb)),
        _fixture("nomodel", Instrument(name="n", pixels=3)),
        _fixture("big", _instrument(glb, triangles=10**9)),
        _fixture("tower a", _instrument(glb)),
    ))
    return build_fixture_models(profile)


def test_layout_json_lists_every_pixel_with_room_zones(tmp_path):
    models = _models(tmp_path)
    doc = json.loads(models["f"].layout_json)
    assert doc["fixture"] == "f" and doc["sha256"] == SHA
    assert len(doc["pixels"]) == 3
    assert doc["pixels"][2]["room_zones"] == ["f.b"]
    assert doc["pixels"][1]["room_zones"] == ["f.a"]
    assert doc["pixels"][1] == {"index": 1, "x_mm": 10, "y_mm": 20, "z_mm": 30,
                                "size": "small", "model_zone": "ring",
                                "room_zones": ["f.a"]}
    assert doc["pixels"][2]["model_zone"] is None


def test_only_ok_fixtures_are_published(tmp_path):
    assert set(_models(tmp_path)) == {"f", "tower a"}


def test_fixture_route_serves_layout_and_model(tmp_path):
    models = _models(tmp_path)
    body, ctype, headers = fixture_route(models, "/fixture-model/f.glb")
    assert body == b"glTF-bytes"
    assert ctype == "model/gltf-binary"
    assert headers["Cache-Control"] == "no-cache"
    body, ctype, headers = fixture_route(models, "/fixture-layout/f.json")
    assert json.loads(body)["fixture"] == "f"
    assert ctype == "application/json"
    assert headers["Cache-Control"] == "no-cache"


def test_fixture_route_404s_unknown_fixture_and_bad_names(tmp_path):
    models = _models(tmp_path)
    for p in ["/fixture-model/nope.glb", "/fixture-model/../x.glb",
              "/fixture-layout/tower.glb", "/fixture-model/f/../f.glb",
              "/fixture-model/.glb", "/fixture-model/f.glb/",
              "/fixture-model/..%2Fx.glb", "/fixture-model/f%2Fx.glb",
              "/fixture-model/%2e%2e%2Ff.glb",
              "/fixture-model/nomodel.glb", "/fixture-layout/big.json", "/other"]:
        assert fixture_route(models, p) is None


def test_model_route_404s_when_file_vanished(tmp_path):
    models = _models(tmp_path)
    (tmp_path / "f.glb").unlink()
    assert fixture_route(models, "/fixture-model/f.glb") is None


def test_percent_encoded_fixture_names_match_their_route(tmp_path):
    models = _models(tmp_path)
    body, _ctype, _headers = fixture_route(models, "/fixture-layout/tower%20a.json")
    assert json.loads(body)["fixture"] == "tower a"
    body, _ctype, _headers = fixture_route(models, "/fixture-model/tower%20a.glb")
    assert body == b"glTF-bytes"
    assert fixture_route(models, "/fixture-model/..%2Fx.glb") is None
