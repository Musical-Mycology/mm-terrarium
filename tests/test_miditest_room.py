"""The MIDITEST Room: a simulated Tower with a room speaker, for MIDI playback
(Deux Ara Drop). Same fixture shape as TOWER; only the zones, the instrument
name and the audio capability differ."""

from control.terrarium_config import load_terrarium_config

CONFIG = load_terrarium_config("terrarium.toml")


def _fixture():
    profile = CONFIG.rooms["MIDITEST"].profile
    assert [f.name for f in profile.fixtures] == ["tower"]
    return profile.fixtures[0]


def test_miditest_zones():
    fx = _fixture()
    assert [(z.name, z.start, z.count) for z in fx.zones] == [
        ("bottom", 0, 3), ("middle", 3, 2), ("top", 5, 3),
        ("responder", 8, 4), ("beat", 12, 2)]


def test_miditest_blocks_match_tower():
    tower = CONFIG.rooms["TOWER"].profile.fixtures[0]
    assert [(b.name, b.start, b.count) for b in _fixture().blocks] == [
        (b.name, b.start, b.count) for b in tower.blocks]


def test_miditest_room_uses_devicelink():
    assert CONFIG.rooms["MIDITEST"].backends == ("devicelink",)


def test_miditest_tower_has_audio():
    inst = _fixture().instrument
    assert inst.name == "tower_sound"
    assert "audio.flsyn" in inst.capabilities
    assert "light.surface" in inst.capabilities
    assert inst.pixels == 14
    assert _fixture().pixel_count == 14


def test_miditest_model_loads():
    inst = _fixture().instrument
    # tower.glb tags the 8 progress markers "progress"; the room refines it.
    assert [p.zone for p in inst.layout] == ["progress"] * 8 + ["responder"] * 4 + ["beat"] * 2
    assert inst.model_sha256 is not None
    tower = CONFIG.instruments["tower"]
    assert inst.model_sha256 == tower.model_sha256


def test_tower_instrument_unchanged():
    assert CONFIG.instruments["tower"].capabilities == frozenset({"light.surface"})
    assert CONFIG.rooms["TOWER"].profile.fixtures[0].instrument.name == "tower"
