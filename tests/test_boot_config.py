from control.boot_config import BootConfig
from control.rooms import RoomType


def test_array_backend_configured_false_when_none():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    assert config.array_backend_configured is False


def test_array_backend_configured_true_for_simulator():
    config = BootConfig(room_type=RoomType.DEMO, bit_name="TestBit",
                        array_backend="simulator")
    assert config.array_backend_configured is True


def test_array_backend_configured_true_for_real_host():
    config = BootConfig(room_type=RoomType.DEMO, bit_name="TestBit",
                        array_backend="10.44.0.50")
    assert config.array_backend_configured is True


def test_default_timeouts():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    assert config.arco_ready_timeout == 15.0
    assert config.room_setup_timeout == 30.0


def test_cue_horizon_has_a_conservative_default():
    """One installation-wide constant, never a per-cue value: a per-cue
    horizon would let two cues from one gesture land on different frames
    and make the clamp counter uninterpretable. The default must clear one
    44 Hz frame (22.7 ms) with room to spare."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    assert config.cue_horizon >= 0.0227
    assert config.o2_ensemble == "arco"


def test_stale_timeout_default():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    assert config.stale_timeout == 15.0
