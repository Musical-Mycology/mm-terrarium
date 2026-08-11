import pytest

from control.arco_process import FakePopen
from control.boot import BootFailure, boot
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType
from control.state import State
from tests.test_engine import RoomCapableBit


def make_registry():
    return {"RoomCapableBit": RoomCapableBit}


def test_boot_happy_path_via_simulator_factory():
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _ready_arco(cmd),
        simulator_factory=lambda: "sim-room-dev")

    assert gs.state == State.SETUP
    assert gs.room.room_type == RoomType.TEST
    assert gs.room.bound_dev == "sim-room-dev"
    assert room_bridge.dev == "sim-room-dev"


def test_boot_happy_path_via_recorded_device_reconnect():
    binding = RoomBindingRegistry()
    binding.bind(RoomType.TEST, "ie7")
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")

    gs, room_bridge, arco = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=binding, arco_process_cls=_ready_arco,
        known_device_connected=lambda dev: dev == "ie7")

    assert gs.room.bound_dev == "ie7"
    assert room_bridge.dev == "ie7"


def test_boot_fails_when_room_type_unresolvable():
    config = BootConfig(room_type=RoomType.DEMO, bit_name="RoomCapableBit")
    with pytest.raises(BootFailure, match="requires an array backend"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco)


def test_boot_fails_when_arco_never_ready():
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit",
                        arco_ready_timeout=0.5)
    with pytest.raises(BootFailure, match="Arco failed to start"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_never_ready_arco,
             simulator_factory=lambda: "sim-room-dev")


def test_boot_fails_for_unknown_bit_name():
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")
    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=lambda: "sim-room-dev")


def test_boot_fails_when_bit_does_not_support_resolved_room_type():
    class DemoOnlyBit(RoomCapableBit):
        room_types = {RoomType.DEMO}

    registry = {"DemoOnlyBit": DemoOnlyBit}
    config = BootConfig(room_type=RoomType.TEST, bit_name="DemoOnlyBit")
    with pytest.raises(BootFailure, match="does not support TEST"):
        boot(config, registry, arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=lambda: "sim-room-dev")


def test_boot_shuts_down_arco_on_any_failure_after_start():
    fake_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")
    with pytest.raises(BootFailure):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=lambda: "sim-room-dev")
    assert fake_popen.signals   # Arco was told to stop, not orphaned


def _ready_arco(command, popen=None):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=popen or FakePopen(), probe=lambda: True)


def _never_ready_arco(command):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=FakePopen(), probe=lambda: False)
