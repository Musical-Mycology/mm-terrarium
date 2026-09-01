import logging

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


def test_terrarium_api_constant_is_one():
    from control.api_version import TERRARIUM_API
    assert TERRARIUM_API == 1


def test_requires_terrarium_api_parses_as_int():
    text = MINIMAL + "requires_terrarium_api = 1\n"
    config = parse_manifest(text, source="t")
    assert config.identity.requires_terrarium_api == 1


def test_requires_terrarium_api_absent_is_none():
    config = parse_manifest(MINIMAL, source="t")
    assert config.identity.requires_terrarium_api is None


def test_requires_terrarium_api_bool_refused():
    # TOML `true` must not pass as 1 (spec 2.2).
    text = MINIMAL + "requires_terrarium_api = true\n"
    with pytest.raises(ManifestError) as exc:
        parse_manifest(text, source="t")
    assert "requires_terrarium_api" in str(exc.value)


def test_min_terrarium_now_warns_as_unknown(caplog):
    text = MINIMAL + 'min_terrarium = "0.1"\n'
    with caplog.at_level(logging.WARNING):
        config = parse_manifest(text, source="t")
    assert not hasattr(config.identity, "min_terrarium")
    assert any("min_terrarium" in r.message for r in caplog.records)


def test_assets_parse_as_sorted_pairs():
    text = MINIMAL + '\n[assets]\nchime = "assets/chime.wav"\n'
    config = parse_manifest(text, source="t")
    assert config.assets == (("chime", "assets/chime.wav"),)


def test_asset_absolute_path_refused():
    text = MINIMAL + '\n[assets]\nchime = "/etc/passwd"\n'
    with pytest.raises(ManifestError) as exc:
        parse_manifest(text, source="t")
    assert "assets.chime" in str(exc.value)


def test_asset_parent_escape_refused():
    text = MINIMAL + '\n[assets]\nchime = "../outside.wav"\n'
    with pytest.raises(ManifestError):
        parse_manifest(text, source="t")


def test_asset_non_string_value_refused():
    text = MINIMAL + "\n[assets]\nchime = 3\n"
    with pytest.raises(ManifestError):
        parse_manifest(text, source="t")


MINIMAL_ENABLED_FALSE = """
[bit]
name = "Off"
entry = "off:Off"
requires_terrarium_api = 1
enabled = false
"""


def test_bit_enabled_parses_and_defaults_true():
    from control.bit_config import parse_manifest
    off = parse_manifest(MINIMAL_ENABLED_FALSE, source="t")
    assert off.identity.enabled is False
    on = parse_manifest(MINIMAL_ENABLED_FALSE.replace(
        "enabled = false\n", ""), source="t")
    assert on.identity.enabled is True
