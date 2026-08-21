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
