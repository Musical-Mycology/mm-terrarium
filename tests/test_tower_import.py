"""Dry run of the Tower model import recipe (spec
docs/superpowers/specs/2026-10-02-tower-fixture-layout-design.md section 6):
copy the real TOWER room and tower instrument into a temp Terrarium, drop in
a synthetic 14-marker .glb, add the model line, and load. This is not a Tower
model: it is 14 boxes at the contract's nominal positions."""

import shutil
from pathlib import Path

import pytest

from control.terrarium_config import TerrariumConfigError, load_terrarium_config
from tests.glb_builder import GlbBuilder

REPO = Path(__file__).resolve().parents[1]

# (px, zone, x_mm, z_mm, size): spec section 4.
TOWER_LAYOUT = [
    (0, "progress", 0, 480, "medium"),
    (1, "progress", 0, 632, "medium"),
    (2, "progress", 0, 785, "medium"),
    (3, "progress", 0, 937, "medium"),
    (4, "progress", 0, 1089, "medium"),
    (5, "progress", 0, 1242, "medium"),
    (6, "progress", 0, 1394, "medium"),
    (7, "progress", 0, 1547, "medium"),
    (8, "responder", -330, 900, "medium"),
    (9, "responder", 315, 1045, "medium"),
    (10, "responder", -330, 1370, "medium"),
    (11, "responder", 310, 1510, "medium"),
    (12, "beat", -80, 90, "large"),
    (13, "beat", 80, 90, "large"),
]
_WIDTH_MM = {"medium": 5.0, "large": 10.0}
_ZONES = ["progress", "responder", "beat"]


def _markers_glb(zone_override=None) -> bytes:
    """14 box markers under LEDs::<zone>; zone_override maps px to a
    different zone layer, to model an artist's mistake."""
    zone_override = zone_override or {}
    builder = GlbBuilder()
    nodes = [{"name": "LEDs", "children": [1, 2, 3]}]
    nodes += [{"name": z, "children": []} for z in _ZONES]
    for px, zone, x_mm, z_mm, size in TOWER_LAYOUT:
        center_m = (x_mm / 1000.0, z_mm / 1000.0, 0.0)
        mesh = builder.add_box_mesh(center_m, _WIDTH_MM[size] / 2000.0)
        nodes.append({"name": f"LED_{px:03d}", "mesh": mesh})
        layer = zone_override.get(px, zone)
        nodes[1 + _ZONES.index(layer)]["children"].append(len(nodes) - 1)
    return builder.build(nodes)


def _terrarium_with_model(tmp_path: Path, glb: bytes) -> str:
    (tmp_path / "instruments" / "models").mkdir(parents=True)
    (tmp_path / "rooms").mkdir()
    shutil.copy(REPO / "rooms" / "TOWER.toml", tmp_path / "rooms" / "TOWER.toml")
    inst_text = (REPO / "instruments" / "tower.toml").read_text()
    assert "\nmodel" not in inst_text  # the repo still runs on the fallback
    (tmp_path / "instruments" / "tower.toml").write_text(
        inst_text + 'model = "models/tower.glb"\n')
    (tmp_path / "instruments" / "models" / "tower.glb").write_bytes(glb)
    cfg = tmp_path / "terrarium.toml"
    cfg.write_text('schema = 1\n[terrarium]\nname = "tower-import"\n'
                   'instrument_paths = ["instruments"]\nroom_paths = ["rooms"]\n')
    return str(cfg)


def test_a_model_matching_the_contract_imports(tmp_path):
    config = load_terrarium_config(_terrarium_with_model(tmp_path, _markers_glb()))
    layout = config.rooms["TOWER"].profile.fixtures[0].instrument.layout
    assert len(layout) == 14
    for p, (px, zone, x_mm, z_mm, size) in zip(layout, TOWER_LAYOUT):
        assert p.index == px
        assert p.zone == zone
        assert abs(p.x_mm - x_mm) <= 1
        assert abs(p.z_mm - z_mm) <= 1
        assert p.size == size


def test_a_marker_in_the_wrong_layer_is_refused_by_name(tmp_path):
    path = _terrarium_with_model(tmp_path, _markers_glb({12: "responder"}))
    with pytest.raises(TerrariumConfigError, match="LED_012"):
        load_terrarium_config(path)
