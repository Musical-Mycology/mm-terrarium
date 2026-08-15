import pytest

from control.arco_process import ArcoProcess, FakePopen
from control.boot import (BootFailure, RoomBindingTimeout, boot,
                          wait_for_room_binding, _abort_if_running)
from control.boot_config import BootConfig
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import RoomBridge
from control.rooms import Room, RoomType
from control.state import State
from control.teardown import TeardownStack
from tests.test_engine import RoomCapableBit


def make_registry():
    return {"RoomCapableBit": RoomCapableBit}


def test_boot_happy_path_via_simulator_factory():
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _ready_arco(cmd),
        simulator_factory=lambda td: "sim-room-dev")

    assert gs.state == State.SETUP
    assert gs.room.room_type == RoomType.TEST
    assert gs.room.bound_dev == "sim-room-dev"
    assert room_bridge.dev == "sim-room-dev"


def test_boot_happy_path_via_recorded_device_reconnect():
    binding = RoomBindingRegistry()
    binding.bind(RoomType.TEST, "ie7")
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")

    gs, room_bridge, arco, teardown = boot(
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
             simulator_factory=lambda td: "sim-room-dev")


def test_boot_fails_for_unknown_bit_name():
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")
    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=lambda td: "sim-room-dev")


def test_boot_fails_when_bit_does_not_support_resolved_room_type():
    class DemoOnlyBit(RoomCapableBit):
        room_types = {RoomType.DEMO}

    registry = {"DemoOnlyBit": DemoOnlyBit}
    config = BootConfig(room_type=RoomType.TEST, bit_name="DemoOnlyBit")
    with pytest.raises(BootFailure, match="does not support TEST"):
        boot(config, registry, arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=lambda td: "sim-room-dev")


def test_boot_shuts_down_arco_on_any_failure_after_start():
    fake_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")
    with pytest.raises(BootFailure):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=lambda td: "sim-room-dev")
    assert fake_popen.signals   # Arco was told to stop, not orphaned


def test_boot_shuts_down_arco_when_wait_ready_times_out():
    from control.arco_process import ArcoProcess

    fake_popen = FakePopen()
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit",
                        arco_ready_timeout=1.0)
    with pytest.raises(BootFailure, match="Arco failed to start"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: ArcoProcess(
                 cmd, popen=fake_popen, probe=lambda: False,
                 clock=clock, sleep=sleep),
             simulator_factory=lambda td: "sim-room-dev")
    assert fake_popen.signals   # Arco was told to stop, not orphaned


def test_wait_for_room_binding_returns_immediately_if_already_bound():
    gs, room_binding = _setup_loaded_room_bit()
    gs.room.bound_dev = "ie7"
    calls = []
    wait_for_room_binding(gs, room_binding, timeout=5.0,
                          tick=lambda: calls.append(1))
    assert calls == []


def test_wait_for_room_binding_arms_and_detects_a_late_join():
    gs, room_binding = _setup_loaded_room_bit()
    ticks = [0]

    def tick():
        ticks[0] += 1
        if ticks[0] == 3:
            gs.join("ie9", "ROOM_TEST_NODE")

    clock, sleep = _fake_clock()
    wait_for_room_binding(gs, room_binding, timeout=5.0, tick=tick,
                          clock=clock, sleep=sleep)

    assert gs.room.bound_dev == "ie9"
    assert room_binding.is_armed(gs.room.room_type) is False   # disarmed on success


def test_wait_for_room_binding_times_out_and_disarms():
    gs, room_binding = _setup_loaded_room_bit()
    clock, sleep = _fake_clock()

    with pytest.raises(RoomBindingTimeout):
        wait_for_room_binding(gs, room_binding, timeout=1.0, tick=lambda: None,
                              clock=clock, sleep=sleep)

    assert room_binding.is_armed(gs.room.room_type) is False


def test_shutdown_aborts_a_running_bit_then_tears_down():
    gs, room_binding = _setup_loaded_room_bit()
    gs.run()
    room_bridge = RoomBridge()
    room_bridge.bind("ie7")
    fake_popen = FakePopen()
    arco = ArcoProcess(["arco-server"], popen=fake_popen)
    arco.start()

    teardown = TeardownStack()
    teardown.push("arco", arco.shutdown)
    teardown.push("room-bridge", room_bridge.shutdown)
    teardown.push("bit", lambda: _abort_if_running(gs))
    teardown.close()

    assert gs.state == State.IDLE
    assert room_bridge.dev is None
    assert fake_popen.signals


def test_shutdown_on_already_idle_server_does_not_raise():
    gs = GameServer({"RoomCapableBit": RoomCapableBit})
    room_bridge = RoomBridge()
    fake_popen = FakePopen()
    arco = ArcoProcess(["arco-server"], popen=fake_popen)
    arco.start()

    teardown = TeardownStack()
    teardown.push("arco", arco.shutdown)
    teardown.push("room-bridge", room_bridge.shutdown)
    teardown.push("bit", lambda: _abort_if_running(gs))
    teardown.close()   # must not raise

    assert fake_popen.signals


def _setup_loaded_room_bit():
    room_binding = RoomBindingRegistry()
    gs = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=room_binding)
    gs.room = Room(room_type=RoomType.TEST)
    gs.load_bit("RoomCapableBit")
    return gs, room_binding


def _fake_clock():
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    return clock, sleep


def _ready_arco(command, popen=None):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=popen or FakePopen(), probe=lambda: True)


def _never_ready_arco(command):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=FakePopen(), probe=lambda: False)


class _SpyProcess:
    """Stands in for control/simulator_process.py's SimulatorProcess:
    boot() only ever calls shutdown() on it."""

    def __init__(self):
        self.shutdowns = 0

    def shutdown(self):
        self.shutdowns += 1


class _SpyFactory:
    """A simulator_factory that SPAWNS. The contract is now
    Callable[[TeardownStack], str]: a factory that spawns a process
    registers its own teardown on the stack it is handed. That replaces
    PR #24's getattr(factory, "process", None) convention, which existed
    only because the factory had no way to hand its handle back."""

    def __init__(self):
        self.process = None

    def __call__(self, teardown):
        self.process = _SpyProcess()
        teardown.push("simulator", self.process.shutdown)
        return "sim-room-dev"


def test_teardown_stops_the_simulator_before_arco():
    """THE regression this slice exists for. control/boot.py's own
    shutdown() used to end with arco.shutdown(), which is right within this
    module's scope and wrong composed with a caller that owns o2lite client
    subprocesses: the hub died first and the clients spent their last
    moments on a dead socket."""
    order = []

    class _RecordingProcess:
        def shutdown(self):
            order.append("simulator")

    class _RecordingFactory:
        def __call__(self, teardown):
            teardown.push("simulator", _RecordingProcess().shutdown)
            return "sim-room-dev"

    class _RecordingArco(ArcoProcess):
        def shutdown(self):
            order.append("arco")

    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _RecordingArco(
            cmd, popen=FakePopen(), probe=lambda: True),
        simulator_factory=_RecordingFactory())

    teardown.close()

    assert order == ["simulator", "arco"]


def test_teardown_aborts_the_bit_before_the_room_bridge(monkeypatch):
    """Deliberate push order, not creation order: the Bit's on_unload may
    still cue into the room bridge, so the bridge must not die first.

    Patched at the class, before boot() runs: boot() pushes the room
    bridge's BOUND method (teardown.push("room-bridge", room_bridge.
    shutdown)), captured at push time, so an instance-attribute
    monkeypatch applied after boot() returns would land on the instance
    and never be seen by that already-captured reference. Same pattern as
    test_build_threads_its_clock_into_the_default_room_audio in
    tests/test_terrarium_boot.py, which patches ArcoSynthPool/AudioBridge
    at their defining module before the call that reads them."""
    order = []
    monkeypatch.setattr(RoomBridge, "shutdown",
                        lambda self: order.append("room-bridge"))

    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
        simulator_factory=lambda td: "sim-room-dev")

    gs.run()
    gs.abort = lambda: order.append("bit")

    teardown.close()

    assert order == ["bit", "room-bridge"]


def test_a_caller_supplied_stack_gets_boots_steps_pushed_onto_it():
    """harness/terrarium_boot.py starts the devicelink server BEFORE boot()
    (the simulator must have something to connect to), so it needs to
    register that server on the same stack first and have it torn down
    last. Passing the stack in is what makes the LIFO invariant hold across
    the boundary between the two modules."""
    order = []
    teardown = TeardownStack()
    teardown.push("devicelink-server", lambda: order.append("server"))

    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, returned = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _ready_arco(cmd),
        simulator_factory=lambda td: "sim-room-dev",
        teardown=teardown)

    assert returned is teardown
    returned.close()
    assert order[-1] == "server"      # started first, therefore stopped last


def test_boot_shuts_down_the_simulator_on_a_failure_after_it_spawned():
    """boot()'s structural guarantee covered Arco and never the simulator
    the same function spawns, three lines earlier. An orphaned Room
    simulator never exits on its own, reconnects to the NEXT Arco and
    claims sim-room there, so that run's own simulator is refused by O2
    (o2/src/bridge.cpp:231-237) and renders nothing."""
    factory = _SpyFactory()
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=factory)

    assert factory.process.shutdowns == 1


def test_boot_shuts_down_the_simulator_when_the_bit_fails_to_load():
    class _BrokenBit(RoomCapableBit):
        def __init__(self):
            raise ValueError("bad Bit")

    factory = _SpyFactory()
    config = BootConfig(room_type=RoomType.TEST, bit_name="BrokenBit")

    with pytest.raises(BootFailure, match="Bit load failed"):
        boot(config, {"BrokenBit": _BrokenBit}, arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=factory)

    assert factory.process.shutdowns == 1


def test_boot_shuts_both_down_on_a_keyboard_interrupt():
    """`except Exception` does not catch KeyboardInterrupt, so a Ctrl-C
    during boot leaked Arco AND the simulator. GameServer.load_bit's own
    handler is also `except Exception` (control/engine.py:80), so a
    KeyboardInterrupt raised while instantiating a Bit propagates straight
    out to boot()."""
    class _InterruptingBit(RoomCapableBit):
        def __init__(self):
            raise KeyboardInterrupt

    factory = _SpyFactory()
    fake_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="InterruptingBit")

    with pytest.raises(KeyboardInterrupt):
        boot(config, {"InterruptingBit": _InterruptingBit},
             arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=factory)

    assert factory.process.shutdowns == 1
    assert fake_popen.signals      # and Arco was told to stop too


def test_boot_still_accepts_a_factory_that_spawns_nothing():
    """Every other test in this file passes `lambda: "sim-room-dev"`, which
    has no .process at all. That must stay a no-op, not an AttributeError."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=lambda td: "sim-room-dev")


def test_boot_shuts_arco_down_even_if_the_simulator_shutdown_raises():
    """Cleanup must not mask the failure that triggered it, and must not
    let one leaked subprocess cause a second."""
    class _RaisingProcess:
        def shutdown(self):
            raise OSError("no such process")

    class _RaisingFactory:
        def __init__(self):
            self.process = None

        def __call__(self, teardown):
            self.process = _RaisingProcess()
            teardown.push("simulator", self.process.shutdown)
            return "sim-room-dev"

    fake_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=_RaisingFactory())

    assert fake_popen.signals


def test_boot_raises_the_original_failure_even_if_arco_shutdown_raises():
    """Sibling of test_boot_shuts_arco_down_even_if_the_simulator_shutdown_
    raises above, but for arco.shutdown() itself raising. This is the real
    race the whole-branch review flagged: ArcoProcess.shutdown() calls
    Popen.wait(), and a second Ctrl-C landing inside that wait() re-raises
    as KeyboardInterrupt -- which, unguarded, would replace this
    well-labeled BootFailure with a bare, undiagnosable KeyboardInterrupt."""
    class _RaisingArco(ArcoProcess):
        def shutdown(self):
            raise OSError("no such process")

    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: _RaisingArco(
                 cmd, popen=FakePopen(), probe=lambda: True),
             simulator_factory=lambda td: "sim-room-dev")
