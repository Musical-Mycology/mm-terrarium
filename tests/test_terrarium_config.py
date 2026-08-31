from pathlib import Path

import pytest

from control.cues import ROOM, TARGET
from control.functions import Function, FunctionKind, GeneratorSpec
from control.terrarium_config import (
    TerrariumConfigError, load_terrarium_config, parse_terrarium_config,
    resolve_bit_roots, validate_rooms,
)

MINIMAL = """
schema = 1
[terrarium]
name = "t"

[instruments.dev_strip]
capabilities = ["light.surface"]
accepted_cues = ["midi", "play", "solid", "mute"]

[rooms.ONE]
backends = ["devicelink"]
[[rooms.ONE.fixtures]]
name = "main"
color_order = "GRB"
instrument = "dev_strip"
[[rooms.ONE.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.ONE.fixtures.zones]]
name = "all"
start = 0
count = 10
"""


def test_resolve_bit_roots_relative_paths_anchor_at_config_dir(tmp_path):
    subdir = tmp_path / "sub"
    subdir.mkdir()
    config_path = subdir / "terrarium.toml"
    cfg = parse_terrarium_config(MINIMAL, source=str(config_path))
    roots = resolve_bit_roots(cfg, str(config_path))
    assert roots == [subdir / "bits"]


def test_resolve_bit_roots_absolute_paths_pass_through(tmp_path):
    config_path = tmp_path / "terrarium.toml"
    text = MINIMAL.replace(
        '[terrarium]\nname = "t"',
        f'[terrarium]\nname = "t"\nbit_paths = ["{tmp_path.as_posix()}/abs_bits", "rel_bits"]',
    )
    cfg = parse_terrarium_config(text, source=str(config_path))
    roots = resolve_bit_roots(cfg, str(config_path))
    assert roots == [tmp_path / "abs_bits", tmp_path / "rel_bits"]


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
    # The shipped terrarium.toml is now the single source of truth for
    # these rooms' shapes (the old ROOM_PROFILES registry is deleted), so
    # this pins the exact fixture/block/zone literals the file declares.
    cfg = load_terrarium_config("terrarium.toml")
    assert set(cfg.rooms) == {"TEST", "DEMO"}

    test_room = cfg.rooms["TEST"]
    assert test_room.backends == ("devicelink",)
    assert test_room.node_id == "ROOM_TEST_NODE"
    assert test_room.profile.surface_id == "room_test"
    assert [f.name for f in test_room.profile.fixtures] == ["main", "accent"]
    main, accent = test_room.profile.fixtures
    assert main.color_order == "GRB"
    assert [(b.name, b.start, b.count) for b in main.blocks] == \
        [("main", 0, 60)]
    assert [(z.name, z.start, z.count) for z in main.zones] == \
        [("left", 0, 20), ("center", 20, 20), ("right", 40, 20)]
    assert accent.color_order == "GRB"
    assert [(b.name, b.start, b.count) for b in accent.blocks] == \
        [("accent", 0, 30)]
    assert [(z.name, z.start, z.count) for z in accent.zones] == \
        [("low", 0, 15), ("high", 15, 15)]
    assert test_room.profile.pixel_count == 90

    demo_room = cfg.rooms["DEMO"]
    assert demo_room.backends == ("devicelink", "array")
    assert demo_room.node_id == "ROOM_DEMO_NODE"
    assert demo_room.profile.surface_id == "room_demo"
    assert [f.name for f in demo_room.profile.fixtures] == ["array"]
    array = demo_room.profile.fixtures[0]
    assert array.color_order == "GRB"
    assert [(b.name, b.start, b.count) for b in array.blocks] == [
        ("m1", 0, 144), ("m2", 144, 144), ("m3", 288, 144),
        ("m4", 432, 144), ("m5", 576, 144), ("m6", 720, 144),
    ]
    assert [(z.name, z.start, z.count) for z in array.zones] == [
        ("left", 0, 288), ("center", 288, 288), ("right", 576, 288),
    ]
    assert demo_room.profile.pixel_count == 864


def test_validate_rooms_reports_per_room():
    cfg = load_terrarium_config("terrarium.toml")
    status = validate_rooms(cfg, array_backend_configured=False)
    assert status["TEST"] is None
    assert "array" in status["DEMO"]           # reason names the missing backend
    status = validate_rooms(cfg, array_backend_configured=True)
    assert status["DEMO"] is None


CONFIG_WITH_INSTRUMENTS = """
schema = 1
[terrarium]
name = "t"

[instruments.venue_array]
description = "6 m SK6812 venue array"
capabilities = ["light.surface", "audio.flsyn"]
functions = []
accepted_cues = ["midi", "play", "solid", "mute"]
  [instruments.venue_array.ambient]
  [instruments.venue_array.ambient.light]
  instruments = [ { instrument = "aurora", target = "primary" } ]
  [instruments.venue_array.ambient.ugen]
  instruments = [ { instrument = "flsyn", program = 89, drone = { key = 48, velocity = 80 } } ]

[rooms.DEMO]
backends = ["devicelink"]
[[rooms.DEMO.fixtures]]
name = "main"
color_order = "GRB"
instrument = "venue_array"
[[rooms.DEMO.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.DEMO.fixtures.zones]]
name = "all"
start = 0
count = 10
"""

CONFIG_MISSING_INSTRUMENT_KEY = """
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

CONFIG_BAD_REFERENCE = """
schema = 1
[terrarium]
name = "t"
[rooms.ONE]
backends = ["devicelink"]
[[rooms.ONE.fixtures]]
name = "main"
color_order = "GRB"
instrument = "no_such"
[[rooms.ONE.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.ONE.fixtures.zones]]
name = "all"
start = 0
count = 10
"""

CONFIG_BAD_TAG = """
schema = 1
[terrarium]
name = "t"

[instruments.bad_one]
capabilities = ["light.warp"]
accepted_cues = ["midi"]

[rooms.ONE]
backends = ["devicelink"]
[[rooms.ONE.fixtures]]
name = "main"
color_order = "GRB"
instrument = "bad_one"
[[rooms.ONE.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.ONE.fixtures.zones]]
name = "all"
start = 0
count = 10
"""


CONFIG_LEGACY_ACCEPTED_TRIGGERS = (
    "\nschema = 1\n[terrarium]\nname = \"t\"\n\n"
    "[instruments.bad_one]\n"
    "capabilities = [\"light.surface\"]\n"
    "accepted_triggers = [\"midi\"]\n"  # legacy-vocabulary-ok
)


def test_legacy_accepted_cues_key_is_a_located_error():
    with pytest.raises(TerrariumConfigError, match="accepted_cues"):
        parse_terrarium_config(CONFIG_LEGACY_ACCEPTED_TRIGGERS, "t.toml")


def test_instruments_parse_and_resolve_onto_fixtures():
    cfg = parse_terrarium_config(CONFIG_WITH_INSTRUMENTS, "terrarium.toml")
    inst = cfg.instruments["venue_array"]
    assert inst.capabilities == frozenset({"light.surface", "audio.flsyn"})
    assert inst.light_manifest["instruments"][0]["instrument"] == "aurora"
    room = cfg.rooms["DEMO"]
    assert room.profile.fixtures[0].instrument is inst


def test_fixture_without_instrument_key_is_rejected():
    with pytest.raises(TerrariumConfigError, match="instrument"):
        parse_terrarium_config(CONFIG_MISSING_INSTRUMENT_KEY, "t.toml")


def test_unknown_instrument_reference_is_rejected():
    with pytest.raises(TerrariumConfigError, match="no_such"):
        parse_terrarium_config(CONFIG_BAD_REFERENCE, "t.toml")


def test_unknown_capability_tag_in_config_is_rejected():
    with pytest.raises(TerrariumConfigError, match="light.warp"):
        parse_terrarium_config(CONFIG_BAD_TAG, "t.toml")


CONFIG_WITH_GENERATOR_FUNCTION = """
schema = 1
[terrarium]
name = "t"

[instruments.venue_array]
description = "6 m SK6812 venue array"
capabilities = ["light.surface", "audio.flsyn"]
accepted_cues = ["midi", "play", "solid", "mute"]
  [[instruments.venue_array.functions]]
  name = "glow"
  description = "ambient breathing glow"
  kind = "generator"
  waveform = "triangle"
  period = 12.0
  lo = 0
  hi = 127
    [instruments.venue_array.functions.lane]
    dev = "room"
    status = 176
    data1 = 74

[rooms.DEMO]
backends = ["devicelink"]
[[rooms.DEMO.fixtures]]
name = "main"
color_order = "GRB"
instrument = "venue_array"
[[rooms.DEMO.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.DEMO.fixtures.zones]]
name = "all"
start = 0
count = 10
"""


def test_instrument_functions_table_parses_to_a_function():
    cfg = parse_terrarium_config(CONFIG_WITH_GENERATOR_FUNCTION, "terrarium.toml")
    inst = cfg.instruments["venue_array"]
    assert inst.functions == (Function(
        name="glow", description="ambient breathing glow",
        kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=ROOM, status=176, data1=74,
                                waveform="triangle", period=12.0, lo=0, hi=127),
    ),)


def test_instrument_functions_table_dev_target_maps_to_cues_target():
    text = CONFIG_WITH_GENERATOR_FUNCTION.replace('dev = "room"', 'dev = "target"')
    cfg = parse_terrarium_config(text, "terrarium.toml")
    assert cfg.instruments["venue_array"].functions[0].generator.dev == TARGET


def test_instrument_functions_table_defect_is_located():
    bad = CONFIG_WITH_GENERATOR_FUNCTION.replace(
        'waveform = "triangle"', 'waveform = "square"')
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(bad, "t.toml")
    assert "instruments.venue_array" in str(exc.value)
    assert "square" in str(exc.value)


CONFIG_WITH_LEGACY_FUNCTIONS_LIST = """
schema = 1
[terrarium]
name = "t"

[instruments.dev_strip]
capabilities = ["light.surface"]
accepted_cues = ["midi", "play", "solid", "mute"]
functions = ["tap"]

[rooms.ONE]
backends = ["devicelink"]
[[rooms.ONE.fixtures]]
name = "main"
color_order = "GRB"
instrument = "dev_strip"
[[rooms.ONE.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.ONE.fixtures.zones]]
name = "all"
start = 0
count = 10
"""


def test_legacy_bare_functions_list_is_a_located_error():
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(CONFIG_WITH_LEGACY_FUNCTIONS_LIST, "t.toml")
    assert "instruments.dev_strip" in str(exc.value)
    assert "[[instruments" in str(exc.value) and "functions" in str(exc.value)


EVENT_TRIGGER_CONFIG = """
schema = 1
[terrarium]
name = "t"
[instruments.shroomy]
capabilities = ["gesture.tap"]
accepted_cues = ["midi"]
  [[instruments.shroomy.event_triggers]]
  name = "tap"
  description = "a tap"
    [instruments.shroomy.event_triggers.thresholds]
    peak_g = 2.0
    window_ms = 200
  [[instruments.shroomy.stream_triggers]]
  name = "smooth_tilt"
  description = "EMA over tilt"
  verb = "tilt"
  arg = 0
  transform = "smooth"
    [instruments.shroomy.stream_triggers.params]
    alpha = 0.4
[rooms.T]
description = "d"
backends = ["devicelink"]
[[rooms.T.fixtures]]
name = "main"
color_order = "GRB"
instrument = "shroomy"
[[rooms.T.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.T.fixtures.zones]]
name = "all"
start = 0
count = 10
"""


def test_instrument_event_and_stream_triggers_parse():
    config = parse_terrarium_config(EVENT_TRIGGER_CONFIG, source="test")
    inst = config.instruments["shroomy"]
    (tap,) = inst.event_triggers
    assert tap.name == "tap"
    assert tap.thresholds == {"peak_g": 2.0, "window_ms": 200}
    (smooth,) = inst.stream_triggers
    assert smooth.verb == "tilt" and smooth.transform == "smooth"


def test_event_trigger_missing_name_is_located():
    bad = EVENT_TRIGGER_CONFIG.replace('name = "tap"\n  ', "")
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(bad, source="test")
    assert "instruments.shroomy" in str(exc.value)
