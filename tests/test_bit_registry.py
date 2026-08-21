from pathlib import Path

import pytest

from control.bit_registry import BitRegistry


def make_pkg(root: Path, dirname: str, manifest: str, module: str = ""):
    d = root / dirname
    d.mkdir(parents=True)
    (d / "bit.toml").write_text(manifest)
    (d / "__init__.py").write_text("")
    if module:
        (d / "fake_bit.py").write_text(module)
    return d


GOOD = """
[bit]
name = "GoodBit"
entry = "fake_bit:GoodBit"
[console]
hidden = false
"""

HIDDEN = GOOD.replace("GoodBit", "HiddenBit").replace(
    "hidden = false", "hidden = true")

MODULE = "class GoodBit:\n    pass\n"


def test_discover_scans_and_isolates_broken_manifests(tmp_path):
    make_pkg(tmp_path, "good", GOOD, MODULE)
    make_pkg(tmp_path, "broken", "[bit]\nkind = 'sport'")
    make_pkg(tmp_path, "notoml", "this is { not toml")
    reg = BitRegistry.discover(tmp_path)
    assert set(reg.packages) == {"GoodBit"}
    assert len(reg.errors) == 2
    assert all("broken" in e.path or "notoml" in e.path for e in reg.errors)


def test_duplicate_name_is_an_error_not_a_clobber(tmp_path):
    make_pkg(tmp_path, "a", GOOD, MODULE)
    make_pkg(tmp_path, "b", GOOD, MODULE)
    reg = BitRegistry.discover(tmp_path)
    assert len(reg.packages) == 1
    assert any("duplicate" in e.message for e in reg.errors)


def test_discovery_never_imports_bit_code(tmp_path):
    make_pkg(tmp_path, "boom", GOOD.replace("GoodBit", "BoomBit"),
             "raise RuntimeError('imported at discovery')\n")
    reg = BitRegistry.discover(tmp_path)  # must not raise
    assert "BoomBit" in reg.packages


def test_list_view_shape_and_hidden_filter(tmp_path):
    make_pkg(tmp_path, "good", GOOD, MODULE)
    make_pkg(tmp_path, "hid", HIDDEN, MODULE)
    reg = BitRegistry.discover(tmp_path)
    names = [row["name"] for row in reg.list_view()]
    assert names == ["GoodBit", "HiddenBit"]
    visible = [row["name"] for row in reg.list_view(include_hidden=False)]
    assert visible == ["GoodBit"]
    row = reg.list_view()[0]
    assert row["start"]["when"] == "immediate"
    assert row["room_types"] == ["TEST"]


def test_real_bits_tree_discovers_cleanly():
    reg = BitRegistry.discover()
    assert reg.errors == []
    assert {"TestBit", "MetronomeBit", "CaptureBit"} <= set(reg.packages)
