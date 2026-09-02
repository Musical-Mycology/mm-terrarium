"""Catalog load-side: instruments/*.toml published, instruments/drafts/*.toml drafts."""
from pathlib import Path

import pytest

from control.catalog import clone_entry, load_catalog, publish_entry, save_draft
from control.terrarium_config import TerrariumConfigError

GOOD = '''
description = "a test instrument"
pixels = 12
capabilities = ["light.pixels"]
accepted_cues = ["midi"]
'''


def make_catalog(tmp_path: Path) -> Path:
    root = tmp_path / "instruments"
    (root / "drafts").mkdir(parents=True)
    return root


def test_missing_root_is_an_empty_catalog(tmp_path):
    cat = load_catalog(tmp_path / "nope")
    assert cat.entries == {}
    assert cat.published == {}


def test_published_entry_parses_to_an_instrument(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    cat = load_catalog(root)
    entry = cat.get("published", "glowcap")
    assert entry.state == "published"
    assert entry.instrument.name == "glowcap"
    assert entry.error is None
    assert "glowcap" in cat.published


def test_published_parse_failure_raises_located(tmp_path):
    root = make_catalog(tmp_path)
    (root / "bad.toml").write_text('capabilities = ["no.such.capability"]')
    with pytest.raises(TerrariumConfigError) as exc:
        load_catalog(root)
    assert "bad.toml" in str(exc.value)


def test_draft_parse_failure_is_collected_not_raised(tmp_path):
    root = make_catalog(tmp_path)
    (root / "drafts" / "wip.toml").write_text('capabilities = ["no.such.capability"]')
    cat = load_catalog(root)
    entry = cat.get("draft", "wip")
    assert entry.state == "draft"
    assert entry.instrument is None
    assert "no.such.capability" in entry.error
    assert cat.published == {}


def test_draft_shadowing_published_keeps_both_reachable(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    (root / "drafts" / "glowcap.toml").write_text(GOOD)
    cat = load_catalog(root)
    # entries key is "<state>:<name>" precisely so a draft edit of a
    # published entry does not hide it.
    assert cat.entries["published:glowcap"].state == "published"
    assert cat.entries["draft:glowcap"].state == "draft"


def test_bad_stem_is_refused_even_as_draft(tmp_path):
    root = make_catalog(tmp_path)
    (root / "drafts" / "we ird.toml").write_text(GOOD)
    with pytest.raises(TerrariumConfigError):
        load_catalog(root)


from control.instrument import TUNESHROOM
from control.terrarium_config import load_terrarium_config, parse_terrarium_config


def test_shipped_tuneshroom_catalog_file_matches_the_code_constant():
    cat = load_catalog(Path("instruments"))
    assert cat.published["tuneshroom"] == TUNESHROOM


def test_shipped_config_still_resolves_fixture_instruments():
    config = load_terrarium_config("terrarium.toml")
    assert "venue_array" in config.instruments
    assert "dev_strip_main" in config.instruments
    fixture = config.rooms["TEST"].profile.fixtures[0]
    assert fixture.instrument.name == "dev_strip_main"


def test_extra_instrument_collision_with_inline_is_located(tmp_path):
    text = (
        'schema = 1\n[terrarium]\nname = "t"\n'
        '[instruments.dupe]\ncapabilities = []\n'
        '[rooms.T]\ndescription = "d"\nbackends = ["devicelink"]\n')
    from control.instrument import Instrument
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(
            text, source="test",
            extra_instruments={"dupe": Instrument(name="dupe")})
    assert "dupe" in str(exc.value)


def test_save_draft_roundtrips_and_reports_errors(tmp_path):
    root = make_catalog(tmp_path)
    refusal, errors = save_draft(root, "wip", 'capabilities = ["nope"]')
    assert refusal is None
    assert errors and "nope" in errors[0]
    assert (root / "drafts" / "wip.toml").read_text() == 'capabilities = ["nope"]'
    refusal, errors = save_draft(root, "wip", GOOD)
    assert refusal is None and errors == []


def test_save_draft_refuses_bad_name(tmp_path):
    root = make_catalog(tmp_path)
    refusal, _ = save_draft(root, "../evil", GOOD)
    assert refusal is not None
    assert not (tmp_path / "evil.toml").exists()


def test_clone_published_to_new_draft(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    assert clone_entry(root, "published", "glowcap", "glowcap2") is None
    assert (root / "drafts" / "glowcap2.toml").read_text() == GOOD
    # refuses to clobber an existing draft
    assert clone_entry(root, "published", "glowcap", "glowcap2") is not None


def test_publish_moves_a_valid_draft(tmp_path):
    root = make_catalog(tmp_path)
    save_draft(root, "wip", GOOD)
    assert publish_entry(root, "wip") is None
    assert (root / "wip.toml").exists()
    assert not (root / "drafts" / "wip.toml").exists()


def test_publish_refuses_an_invalid_draft_in_place(tmp_path):
    root = make_catalog(tmp_path)
    save_draft(root, "wip", 'capabilities = ["nope"]')
    reason = publish_entry(root, "wip")
    assert reason is not None and "nope" in reason
    assert (root / "drafts" / "wip.toml").exists()
    assert not (root / "wip.toml").exists()


def test_shipped_defaultshroom_catalog_file_matches_the_code_constant():
    from control.instrument import DEFAULTSHROOM
    cat = load_catalog(Path("instruments"))
    assert cat.published["defaultshroom"] == DEFAULTSHROOM


def test_testshroom_catalog_entry_resolves_with_audio_samples():
    from control.terrarium_config import load_terrarium_config
    cfg = load_terrarium_config("terrarium.toml")
    inst = cfg.instruments["testshroom"]
    assert inst.pixels == 12
    assert "audio.samples" in inst.capabilities
    assert "light.pixels" in inst.capabilities      # carriable (engine gate)
    assert "audio.mic" not in inst.capabilities


from control.instrument import Instrument
from control.catalog import Catalog, CatalogEntry, KINDS

STRIP = Instrument(name="strip", capabilities=frozenset({"light.surface"}),
                   accepted_cues=("midi", "play", "solid", "mute"))
INSTRUMENTS = {"strip": STRIP}

ROOM_TOML = '''description = "Two strips"
backends = ["devicelink"]

[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "strip"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 60
  [[fixtures.zones]]
  name = "left"
  start = 0
  count = 30
  [[fixtures.zones]]
  name = "right"
  start = 30
  count = 30

[[fixtures]]
name = "accent"
color_order = "GRB"
instrument = "strip"
  [[fixtures.blocks]]
  name = "accent"
  start = 0
  count = 30
'''


def test_kinds_are_instrument_and_room():
    assert KINDS == ("instrument", "room")


def test_room_catalog_requires_instruments(tmp_path):
    with pytest.raises(ValueError, match="instruments"):
        load_catalog(tmp_path, kind="room")


def test_published_room_parses_to_a_room_spec(tmp_path):
    (tmp_path / "LOFT.toml").write_text(ROOM_TOML)
    cat = load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)
    entry = cat.get("published", "LOFT")
    assert entry.kind == "room" and entry.instrument is None
    spec = entry.room
    assert spec.name == "LOFT"
    assert [f.name for f in spec.profile.fixtures] == ["main", "accent"]
    assert spec.profile.surface_id == "room_loft"
    assert cat.published == {"LOFT": spec}


def test_published_room_with_unknown_instrument_raises_located(tmp_path):
    (tmp_path / "LOFT.toml").write_text(ROOM_TOML.replace('"strip"', '"ghost"'))
    with pytest.raises(TerrariumConfigError, match="ghost"):
        load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)


def test_room_draft_errors_are_collected_not_raised(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "LOFT.toml").write_text("description = 1\n[[fixtures]]\nname = 'x'\n")
    cat = load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)
    entry = cat.get("draft", "LOFT")
    assert entry.room is None and entry.error


def test_room_save_draft_reports_room_errors(tmp_path):
    refusal, errors = save_draft(tmp_path, "LOFT", ROOM_TOML.replace('"strip"', '"ghost"'),
                                 kind="room", instruments=INSTRUMENTS)
    assert refusal is None
    assert any("ghost" in e for e in errors)
    refusal, errors = save_draft(tmp_path, "LOFT", ROOM_TOML,
                                 kind="room", instruments=INSTRUMENTS)
    assert (refusal, errors) == (None, [])


def test_room_publish_moves_a_valid_draft(tmp_path):
    save_draft(tmp_path, "LOFT", ROOM_TOML, kind="room", instruments=INSTRUMENTS)
    assert publish_entry(tmp_path, "LOFT", kind="room", instruments=INSTRUMENTS) is None
    assert (tmp_path / "LOFT.toml").is_file()
    assert not (tmp_path / "drafts" / "LOFT.toml").exists()


def test_room_publish_refuses_an_invalid_draft_in_place(tmp_path):
    save_draft(tmp_path, "LOFT", ROOM_TOML.replace('"strip"', '"ghost"'),
               kind="room", instruments=INSTRUMENTS)
    refusal = publish_entry(tmp_path, "LOFT", kind="room", instruments=INSTRUMENTS)
    assert refusal and "ghost" in refusal
    assert (tmp_path / "drafts" / "LOFT.toml").is_file()


def test_room_clone_names_the_kind_in_its_refusal(tmp_path):
    refusal = clone_entry(tmp_path, "published", "NOPE", "NEW", kind="room")
    assert refusal == "no published room named 'NOPE'"


def test_instrument_kind_is_the_default_and_unchanged(tmp_path):
    (tmp_path / "glow.toml").write_text('description = "g"\ncapabilities = ["light.surface"]\n'
                                        'accepted_cues = ["midi"]\n')
    cat = load_catalog(tmp_path)
    assert cat.kind == "instrument"
    entry = cat.get("published", "glow")
    assert entry.kind == "instrument" and entry.room is None
    assert entry.instrument.name == "glow"
