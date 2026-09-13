import json
from pathlib import Path

from control.catalog import load_catalog
from control.role_config import carried_instrument_view
from tools.export_solo import export_solo, main

ROOT = Path(__file__).resolve().parents[1]


def _tuneshroom():
    return load_catalog(ROOT / "instruments").published["tuneshroom"]


def test_export_instrument_section_is_the_wire_shape():
    inst = _tuneshroom()
    out = export_solo(inst, catalog_path="instruments/tuneshroom.toml", commit="abc123")
    assert out["instrument"] == carried_instrument_view(inst)


def test_export_carries_triggers_solo_and_provenance():
    out = export_solo(_tuneshroom(), catalog_path="instruments/tuneshroom.toml", commit="abc123")
    assert out["triggers"] == {
        "tap": {"peak_g": 2.0, "window_ms": 200, "double_ms": 400},
        "shake": {"peak_g": 2.0, "window_ms": 200}}
    assert out["solo"]["bindings"] == {
        "tap": "play_aurora", "double_tap": "win", "shake": "fireworks_player"}
    assert out["solo"]["ambient"]["light"]["instruments"][0]["instrument"] == "aurora"
    assert out["_provenance"] == {
        "catalog": "instruments/tuneshroom.toml", "commit": "abc123", "tool": "export_solo/1"}


def test_export_refuses_instrument_without_solo():
    import pytest
    inst = load_catalog(ROOT / "instruments").published["defaultshroom"]
    with pytest.raises(SystemExit, match="declares no \\[solo\\] table"):
        main(["defaultshroom", "/dev/null"])


def test_main_writes_json_file(tmp_path):
    out = tmp_path / "t.json"
    main(["tuneshroom", str(out)])
    data = json.loads(out.read_text())
    assert data["instrument"]["name"] == "tuneshroom"
    assert set(data) == {"_provenance", "instrument", "triggers", "solo"}
