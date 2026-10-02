"""The TOWER Room: one 14-pixel fixture, the layout contract of spec
docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md section 4."""

from control.terrarium_config import load_terrarium_config

CONFIG = load_terrarium_config("terrarium.toml")


def _fixture():
    profile = CONFIG.rooms["TOWER"].profile
    assert [f.name for f in profile.fixtures] == ["tower"]
    return profile.fixtures[0]


def test_tower_is_one_14_pixel_grb_fixture():
    fx = _fixture()
    assert fx.pixel_count == 14
    assert fx.color_order == "GRB"
    assert fx.channels == 3


def test_tower_blocks_follow_the_physical_runs():
    fx = _fixture()
    assert [(b.name, b.start, b.count) for b in fx.blocks] == [
        ("lower", 0, 4), ("upper", 4, 4), ("responders", 8, 4), ("par", 12, 2)]


def test_tower_zones_are_progress_responder_beat():
    fx = _fixture()
    assert [(z.name, z.start, z.count) for z in fx.zones] == [
        ("progress", 0, 8), ("responder", 8, 4), ("beat", 12, 2)]


def test_tower_room_uses_devicelink():
    assert CONFIG.rooms["TOWER"].backends == ("devicelink",)
    assert CONFIG.rooms["TOWER"].node_id == "ROOM_TOWER_NODE"


def test_tower_instrument_runs_on_the_no_model_fallback():
    inst = _fixture().instrument
    assert inst.name == "tower"
    assert inst.pixels == 14
    assert "light.surface" in inst.capabilities
    assert inst.layout == ()
    assert inst.model_sha256 is None
