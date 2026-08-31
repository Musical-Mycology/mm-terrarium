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
