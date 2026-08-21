from control.bit_config import parse_manifest
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
    reg = BitRegistry.discover()
    cls = reg.bit_class("MetronomeBit")
    fast = cls(reg.resolve_config("MetronomeBit", {"rhythm": {"bpm": 120}}))
    assert abs(fast.BEAT_S - 0.5) < 1e-9
    default = cls()
    assert abs(default.BEAT_S - 0.6) < 1e-9
    assert abs(fast.LEAD_IN_S - 0.5) < 1e-9
