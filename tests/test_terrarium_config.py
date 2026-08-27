import pytest

from control.room_profile import ROOM_PROFILES
from control.rooms import RoomType
from control.terrarium_config import (
    TerrariumConfigError, load_terrarium_config, parse_terrarium_config,
    validate_rooms,
)

MINIMAL = """
schema = 1
[terrarium]
name = "t"
[rooms.ONE]
backends = ["devicelink"]
[[rooms.ONE.fixtures]]
name = "main"
color_order = "GRB"
[[rooms.ONE.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.ONE.fixtures.zones]]
name = "all"
start = 0
count = 10
"""


def test_minimal_config_parses():
    cfg = parse_terrarium_config(MINIMAL, source="inline")
    assert cfg.schema == 1
    assert cfg.name == "t"
    assert cfg.bit_paths == ("bits",)          # default
    spec = cfg.rooms["ONE"]
    assert spec.name == "ONE"
    assert spec.node_id == "ROOM_ONE_NODE"     # default shape
    assert spec.backends == ("devicelink",)
    assert spec.profile.pixel_count == 10
    assert spec.profile.fixtures[0].zones[0].name == "all"


def test_version_is_schema_plus_content_hash():
    a = parse_terrarium_config(MINIMAL, source="inline")
    b = parse_terrarium_config(MINIMAL + "\n# comment\n", source="inline")
    assert a.version.startswith("1-") and len(a.version) == 2 + 12
    assert a.version != b.version              # content-addressed


def test_unknown_backend_is_a_located_error():
    bad = MINIMAL.replace('backends = ["devicelink"]',
                          'backends = ["hologram"]')
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(bad, source="inline")
    assert "rooms.ONE" in str(exc.value) and "hologram" in str(exc.value)


def test_profile_validation_errors_are_located():
    # zone overruns the 10 px fixture -> RoomProfile's own ValueError,
    # wrapped with the room's config location.
    bad = MINIMAL.replace(
        '[[rooms.ONE.fixtures.zones]]\nname = "all"\nstart = 0\ncount = 10',
        '[[rooms.ONE.fixtures.zones]]\nname = "all"\nstart = 0\ncount = 99')
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(bad, source="inline")
    assert "rooms.ONE" in str(exc.value)


def test_shipped_config_matches_code_profiles_golden():
    cfg = load_terrarium_config("terrarium.toml")
    assert set(cfg.rooms) == {"TEST", "DEMO"}
    assert cfg.rooms["TEST"].profile == ROOM_PROFILES[RoomType.TEST]
    assert cfg.rooms["DEMO"].profile == ROOM_PROFILES[RoomType.DEMO]
    assert cfg.rooms["TEST"].backends == ("devicelink",)
    assert cfg.rooms["DEMO"].backends == ("devicelink", "array")
    assert cfg.rooms["TEST"].node_id == "ROOM_TEST_NODE"
    assert cfg.rooms["DEMO"].node_id == "ROOM_DEMO_NODE"


def test_validate_rooms_reports_per_room():
    cfg = load_terrarium_config("terrarium.toml")
    status = validate_rooms(cfg, array_backend_configured=False)
    assert status["TEST"] is None
    assert "array" in status["DEMO"]           # reason names the missing backend
    status = validate_rooms(cfg, array_backend_configured=True)
    assert status["DEMO"] is None
