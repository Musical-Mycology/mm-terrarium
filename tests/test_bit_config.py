import pytest

from control.bit_config import (
    BitConfig, ManifestError, StartCondition, merge_overrides, parse_manifest,
)

MINIMAL = """
[bit]
name = "TestBit"
entry = "test_bit:TestBit"
"""

FULL = """
[bit]
name = "MetronomeBit"
version = "1.0.0"
description = "Call-and-response rhythm game"
entry = "metronome_bit:MetronomeBit"
kind = "r_game"
author = "Musical Mycology"

[launch]
room_types = ["DEMO"]
default_room_type = "DEMO"
default_devices = 2
setup_seconds = 20
expected_run_seconds = 45
transport = "any"
default_join_role = "player"

[launch.nodes]
player = "METRO_PLAYER_NODE"

[start]
when = "players"
min_scored = 1
timeout_seconds = 120
on_timeout = "start"

[console]
display_name = "Metronome"
notes = "Players tap back the 4-beat call."

[results]
keys = ["phrases", "successes"]

[rhythm]
bpm = 100
beats_per_cycle = 8
cycles = 4
grading_window_ms = 50
input_offset_ms = 0

[defaults]
extra_knob = 7
"""


def test_minimal_manifest_fills_defaults():
    cfg = parse_manifest(MINIMAL, source="bits/test/bit.toml")
    assert cfg.identity.name == "TestBit"
    assert cfg.identity.kind == "game"
    assert cfg.launch.room_types == ("TEST",)
    assert cfg.start.when == "immediate"
    assert cfg.rhythm is None and cfg.ambient is None
    assert cfg.join_node() is None


def test_full_manifest_round_trip():
    cfg = parse_manifest(FULL, source="bits/metronome/bit.toml")
    assert cfg.identity.kind == "r_game"
    assert cfg.launch.default_devices == 2
    assert cfg.launch.expected_run_seconds == 45
    assert cfg.node_for("player") == "METRO_PLAYER_NODE"
    assert cfg.join_node() == "METRO_PLAYER_NODE"
    assert cfg.start == StartCondition(
        when="players", min_scored=1, timeout_seconds=120, on_timeout="start")
    assert cfg.rhythm.bpm == 100.0
    assert cfg.extras == {"extra_knob": 7}
    assert cfg.results_keys == ("phrases", "successes")


@pytest.mark.parametrize("mutation,key", [
    ("[bit]\nentry = 'a:B'", "bit.name"),
    ("[bit]\nname = 'X'", "bit.entry"),
    ("[bit]\nname='X'\nentry='a:B'\nkind='sport'", "bit.kind"),
    (MINIMAL + "[start]\nwhen = 'scheduled'", "start.when"),
    (MINIMAL + "[start]\nwhen='players'\nmin_scored = 0", "start.min_scored"),
    (MINIMAL + "[launch]\ntransport = 'udp'", "launch.transport"),
])
def test_bad_manifest_raises_located_error(mutation, key):
    with pytest.raises(ManifestError) as exc:
        parse_manifest(mutation, source="bits/x/bit.toml")
    assert exc.value.source == "bits/x/bit.toml"
    assert exc.value.key == key


def test_unknown_key_warns_not_fails(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        cfg = parse_manifest(MINIMAL + "[mystery]\nx = 1", source="s")
    assert cfg.identity.name == "TestBit"
    assert any("mystery" in r.message for r in caplog.records)


def test_merge_overrides_precedence_and_strictness():
    cfg = parse_manifest(FULL, source="s")
    merged = merge_overrides(
        cfg, {"launch": {"setup_seconds": 5}, "defaults": {"extra_knob": 9}},
        source="cli")
    assert merged.launch.setup_seconds == 5
    assert merged.extras == {"extra_knob": 9}
    assert cfg.launch.setup_seconds == 20  # original untouched (frozen)
    with pytest.raises(ManifestError):
        merge_overrides(cfg, {"launch": {"no_such_key": 1}}, source="cli")


def test_merge_overrides_revalidates_semantic_values():
    cfg = parse_manifest(FULL, source="s")
    with pytest.raises(ManifestError) as exc:
        merge_overrides(cfg, {"start": {"when": "scheduled"}}, source="cli")
    assert exc.value.key == "start.when"

    with pytest.raises(ManifestError) as exc:
        merge_overrides(cfg, {"launch": {"transport": "udp"}}, source="cli")
    assert exc.value.key == "launch.transport"


def test_merge_overrides_rejects_bool_as_numeric():
    cfg = parse_manifest(FULL, source="s")
    with pytest.raises(ManifestError) as exc:
        merge_overrides(cfg, {"launch": {"setup_seconds": True}}, source="cli")
    assert exc.value.key == "launch.setup_seconds"


def test_merge_overrides_results_unknown_key_strict():
    cfg = parse_manifest(FULL, source="s")
    with pytest.raises(ManifestError) as exc:
        merge_overrides(
            cfg, {"results": {"keys": ["a"], "typo": 1}}, source="cli")
    assert exc.value.key == "results.typo"


def test_bad_assets_table_error_key_has_no_leading_dot():
    with pytest.raises(ManifestError) as exc:
        parse_manifest("assets = 1\n" + MINIMAL, source="s")
    assert exc.value.key == "assets"
