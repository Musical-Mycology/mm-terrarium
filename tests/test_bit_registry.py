from pathlib import Path

import pytest

from control.bit_config import ManifestError, parse_manifest
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
requires_terrarium_api = 1
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


def test_lazy_class_map_imports_only_on_access(tmp_path):
    make_pkg(tmp_path, "good", GOOD, MODULE)
    make_pkg(tmp_path, "boom", GOOD.replace("GoodBit", "BoomBit"),
             "raise RuntimeError('imported')\n")
    reg = BitRegistry.discover(tmp_path)
    m = reg.lazy_class_map()
    assert set(m) == {"GoodBit", "BoomBit"}      # no import yet
    assert m["GoodBit"].__name__ == "GoodBit"
    with pytest.raises(Exception):               # ManifestError on access
        m["BoomBit"]


def test_lazy_class_map_unknown_name_is_keyerror(tmp_path):
    make_pkg(tmp_path, "good", GOOD, MODULE)
    with pytest.raises(KeyError):
        BitRegistry.discover(tmp_path).lazy_class_map()["Nope"]


def test_gameserver_loads_a_real_packaged_bit_through_the_lazy_map():
    from control.engine import GameServer

    reg = BitRegistry.discover()
    server = GameServer(reg.lazy_class_map())
    server.load_bit("TestBit")
    assert server.bit.__class__.__name__ == "TestBit"


def test_real_bits_tree_discovers_cleanly():
    reg = BitRegistry.discover()
    assert reg.errors == []
    assert {"TestBit", "MetronomeBit", "CaptureBit"} <= set(reg.packages)


def test_scan_multiple_roots_discovers_both(tmp_path):
    root_a = tmp_path / "a_root"
    root_b = tmp_path / "b_root"
    make_pkg(root_a, "one", GOOD, MODULE)
    make_pkg(root_b, "two", HIDDEN, MODULE)
    reg = BitRegistry.scan((root_a, root_b))
    assert set(reg.packages) == {"GoodBit", "HiddenBit"}
    assert reg.errors == []


def test_scan_duplicate_name_across_roots_first_root_wins(tmp_path):
    root_a = tmp_path / "a_root"
    root_b = tmp_path / "b_root"
    a_dir = make_pkg(root_a, "pkg", GOOD, MODULE)
    make_pkg(root_b, "pkg", GOOD, MODULE)
    reg = BitRegistry.scan((root_a, root_b))
    assert len(reg.packages) == 1
    assert reg.packages["GoodBit"].path == a_dir
    assert len(reg.errors) == 1
    assert "duplicate" in reg.errors[0].message
    assert "b_root" in reg.errors[0].path


def test_scan_missing_root_is_a_located_error_other_roots_still_scanned(tmp_path):
    root_a = tmp_path / "does_not_exist"
    root_b = tmp_path / "b_root"
    make_pkg(root_b, "two", GOOD, MODULE)
    reg = BitRegistry.scan((root_a, root_b))
    assert set(reg.packages) == {"GoodBit"}
    assert len(reg.errors) == 1
    assert str(root_a) in reg.errors[0].path


def test_scan_default_roots_matches_discover_default():
    reg = BitRegistry.scan()
    assert reg.errors == []
    assert {"TestBit", "MetronomeBit", "CaptureBit"} <= set(reg.packages)


class _RaisingBit:
    """Constructor raises: list_view must degrade to roles=None, never
    propagate."""
    def __init__(self, config=None):
        raise RuntimeError("boom")


def test_list_view_carries_a_role_summary_for_testbit():
    registry = BitRegistry.discover()
    row = next(r for r in registry.list_view() if r["name"] == "TestBit")
    # TestBit: scored SHARED 'player' (capacity=None, unbounded -- counts as
    # 1 open scored slot) + unscored JAM 'jammer' (+ hidden ROOM roles, which
    # must NOT be counted).
    assert row["roles"] == {"scored": 1, "shared_open": True, "jam_open": True}


def test_list_view_role_summary_counts_unique_capacity():
    registry = BitRegistry.discover()
    row = next(r for r in registry.list_view() if r["name"] == "MetronomeBit")
    # MetronomeBit: one UNIQUE scored role, capacity 2, no jam.
    assert row["roles"] == {"scored": 2, "shared_open": False, "jam_open": False}


def test_list_view_role_summary_is_none_when_the_bit_raises(monkeypatch):
    registry = BitRegistry.discover()
    monkeypatch.setattr(registry, "bit_class", lambda name: _RaisingBit)
    for row in registry.list_view():
        assert row["roles"] is None


def _manifest(name: str, api_line: str = "requires_terrarium_api = 1") -> str:
    return f"""
[bit]
name = "{name}"
entry = "{name.lower()}:{name}"
{api_line}
"""


def test_matching_api_version_discovers(tmp_path):
    pkg = tmp_path / "ok"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(_manifest("Ok"))
    reg = BitRegistry.scan((tmp_path,))
    assert "Ok" in reg.packages and not reg.errors


def test_missing_api_key_is_located_package_error(tmp_path):
    pkg = tmp_path / "old"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(_manifest("Old", api_line=""))
    reg = BitRegistry.scan((tmp_path,))
    assert "Old" not in reg.packages
    assert len(reg.errors) == 1
    err = reg.errors[0]
    assert "requires_terrarium_api" in err.message
    assert err.path.endswith("old/bit.toml")


def test_wrong_api_version_names_both_numbers(tmp_path):
    pkg = tmp_path / "future"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(
        _manifest("Future", api_line="requires_terrarium_api = 2"))
    reg = BitRegistry.scan((tmp_path,))
    assert "Future" not in reg.packages
    assert "2" in reg.errors[0].message and "1" in reg.errors[0].message


def test_api_refusal_is_package_scoped(tmp_path):
    for name, line in (("Good", "requires_terrarium_api = 1"),
                       ("Bad", "requires_terrarium_api = 99")):
        pkg = tmp_path / name.lower()
        pkg.mkdir()
        (pkg / "bit.toml").write_text(_manifest(name, api_line=line))
    reg = BitRegistry.scan((tmp_path,))
    assert "Good" in reg.packages and "Bad" not in reg.packages


def test_declared_asset_must_exist(tmp_path):
    pkg = tmp_path / "a"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(
        _manifest("A") + '\n[assets]\nchime = "assets/chime.wav"\n')
    reg = BitRegistry.scan((tmp_path,))
    assert "A" not in reg.packages
    assert "chime" in reg.errors[0].message


def test_present_asset_discovers(tmp_path):
    pkg = tmp_path / "a"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "assets" / "chime.wav").write_bytes(b"RIFF")
    (pkg / "bit.toml").write_text(
        _manifest("A") + '\n[assets]\nchime = "assets/chime.wav"\n')
    reg = BitRegistry.scan((tmp_path,))
    assert "A" in reg.packages and not reg.errors


def test_symlink_escape_refused(tmp_path):
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFF")
    pkg = tmp_path / "a"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "assets" / "chime.wav").symlink_to(outside)
    (pkg / "bit.toml").write_text(
        _manifest("A") + '\n[assets]\nchime = "assets/chime.wav"\n')
    reg = BitRegistry.scan((tmp_path,))
    assert "A" not in reg.packages
    assert "escapes" in reg.errors[0].message


def _asset_pkg(tmp_path):
    pkg = tmp_path / "a"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "assets" / "chime.wav").write_bytes(b"RIFF")
    (pkg / "bit.toml").write_text(
        _manifest("A") + '\n[assets]\nchime = "assets/chime.wav"\n')
    return pkg


def test_resolve_config_stamps_assets_root(tmp_path):
    pkg = _asset_pkg(tmp_path)
    reg = BitRegistry.scan((tmp_path,))
    config = reg.resolve_config("A")
    assert config.asset_path("chime") == pkg / "assets" / "chime.wav"
    # override branch stamps too
    config2 = reg.resolve_config("A", {"console": {"notes": "x"}})
    assert config2.asset_path("chime") == pkg / "assets" / "chime.wav"


def test_bit_package_asset_path(tmp_path):
    pkg = _asset_pkg(tmp_path)
    reg = BitRegistry.scan((tmp_path,))
    assert reg.packages["A"].asset_path("chime") == pkg / "assets" / "chime.wav"


def test_asset_path_unknown_key_raises(tmp_path):
    _asset_pkg(tmp_path)
    reg = BitRegistry.scan((tmp_path,))
    with pytest.raises(ManifestError) as exc:
        reg.resolve_config("A").asset_path("nope")
    assert "nope" in str(exc.value)


def test_asset_path_with_no_root_raises():
    config = parse_manifest(
        '[bit]\nname = "X"\nentry = "x:X"\nrequires_terrarium_api = 1\n'
        '\n[assets]\nchime = "assets/chime.wav"\n', source="t")
    with pytest.raises(ManifestError):
        config.asset_path("chime")
