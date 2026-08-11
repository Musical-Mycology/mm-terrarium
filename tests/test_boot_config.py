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
