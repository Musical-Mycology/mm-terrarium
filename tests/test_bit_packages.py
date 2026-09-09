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
    # Re-enabled 2026-09-08 (spec 2026-09-08-metronome-bit-on-o2lite): the
    # shipped manifest must resolve through the registry's enabled gate,
    # exactly the path --profile and the Console take.
    reg = BitRegistry.discover()
    cls = reg.bit_class("MetronomeBit")
    fast_cfg = reg.resolve_config("MetronomeBit", {"rhythm": {"bpm": 120}})
    fast = cls(fast_cfg)
    assert abs(fast.BEAT_S - 0.5) < 1e-9
    default = cls()
    assert abs(default.BEAT_S - 0.6) < 1e-9
    # The lead-in is a beat PLUS the adoption ceremony it has to clear
    # (MetronomeBit.WELCOME_S, luxaeterna's 1.5 s sys:loaded signature).
    assert abs(fast.LEAD_IN_S - (1.5 + 0.5)) < 1e-9


def test_metronome_package_is_enabled():
    reg = BitRegistry.discover()
    assert reg.packages["MetronomeBit"].config.identity.enabled is True
    assert "MetronomeBit" in reg.lazy_class_map()


def test_capturebit_package_resolves_and_constructs():
    reg = BitRegistry.discover()
    assert "CaptureBit" in reg.packages, reg.errors
    cls = reg.bit_class("CaptureBit")
    bit = cls(config=reg.resolve_config("CaptureBit"))
    assert isinstance(bit.status(), dict)
