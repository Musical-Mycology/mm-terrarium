"""Catalog load-side: instruments/*.toml published, instruments/drafts/*.toml drafts."""
from pathlib import Path

import pytest

from control.catalog import load_catalog
from control.terrarium_config import TerrariumConfigError

GOOD = '''
description = "a test instrument"
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
    assert "dev_strip" in config.instruments
    fixture = config.rooms["TEST"].profile.fixtures[0]
    assert fixture.instrument.name == "dev_strip"


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
