from dataclasses import replace

from control.bit_config import merge_overrides, parse_manifest
from control.bit_registry import BitRegistry


def test_testbit_package_resolves_and_constructs():
    reg = BitRegistry.discover()
    assert "TestBit" in reg.packages, reg.errors
    cls = reg.bit_class("TestBit")
    cfg = reg.resolve_config(
        "TestBit", {"defaults": {"run_duration_seconds": 0.5}})
    bit = cls(cfg)
    assert bit.run_duration == 0.5
    assert cfg.node_for("player") == "TEST_PLAYER_NODE"


def test_metronome_package_rhythm_block_reaches_instance():
    # MetronomeBit is disabled ([bit] enabled = false) pending redesign, so
    # resolve_config() refuses it; construct its config directly from the
    # package's own manifest instead of going through the registry's
    # enabled gate.
    reg = BitRegistry.discover()
    cls = reg.bit_class("MetronomeBit")
    pkg = reg.packages["MetronomeBit"]
    base = replace(pkg.resolved_config(), assets_root=pkg.path)
    fast_cfg = replace(
        merge_overrides(base, {"rhythm": {"bpm": 120}}, source="test"),
        assets_root=pkg.path)
    fast = cls(fast_cfg)
    assert abs(fast.BEAT_S - 0.5) < 1e-9
    default = cls()
    assert abs(default.BEAT_S - 0.6) < 1e-9
    assert abs(fast.LEAD_IN_S - 0.5) < 1e-9


def test_capturebit_package_resolves_and_constructs():
    reg = BitRegistry.discover()
    assert "CaptureBit" in reg.packages, reg.errors
    cls = reg.bit_class("CaptureBit")
    bit = cls(config=reg.resolve_config("CaptureBit"))
    assert isinstance(bit.status(), dict)
