import pytest

from control.arco_process import ArcoProcess, FakePopen
from control.boot import (BootFailure, RoomBindingTimeout, boot,
                          wait_for_room_binding, _abort_if_running)
from control.boot_config import BootConfig
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import RoomBridge
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.rooms import Room
from control.state import State
from control.teardown import TeardownStack
from control.terrarium_config import RoomSpec
from tests.test_engine import RoomCapableBit

TEST_PROFILE = RoomProfile(surface_id="room_test", fixtures=(
    RoomFixture(name="main", color_order="GRB",
               blocks=(RoomBlock("main", 0, 10),),
               zones=(RoomZone("all", 0, 10),)),
    RoomFixture(name="accent", color_order="GRB",
               blocks=(RoomBlock("accent", 0, 10),),
               zones=(RoomZone("all", 0, 10),)),
))
TEST_SPEC = RoomSpec(name="TEST", description="", backends=("devicelink",),
                     node_id="ROOM_TEST_NODE", profile=TEST_PROFILE)

DEMO_PROFILE = RoomProfile(surface_id="room_demo", fixtures=(
    RoomFixture(name="array", color_order="GRB",
               blocks=(RoomBlock("array", 0, 10),),
               zones=(RoomZone("all", 0, 10),)),
))
DEMO_SPEC = RoomSpec(name="DEMO", description="",
                     backends=("devicelink", "array"),
                     node_id="ROOM_DEMO_NODE", profile=DEMO_PROFILE)


def make_registry():
    return {"RoomCapableBit": RoomCapableBit}


def test_canonical_room_dev_prefers_profile_order_over_bind_order():
    """Regression test: control/engine.py needed two review rounds because
    a similar canonical-dev pick used dict-insertion order instead of the
    profile's declared order. Same algorithm here, tested directly against
    a dict whose insertion order is deliberately reversed from profile
    declaration order (accent inserted first, main second)."""
    from control.boot import _canonical_room_dev
    profile = TEST_PROFILE
    bound = {"accent": "accent-dev", "main": "main-dev"}
    assert _canonical_room_dev(profile, bound) == "main-dev"


def test_canonical_room_dev_returns_none_when_nothing_bound():
    from control.boot import _canonical_room_dev
    profile = TEST_PROFILE
    assert _canonical_room_dev(profile, {}) is None


def test_boot_happy_path_via_simulator_factory():
    config = BootConfig(room_name="TEST", bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        arco_process_cls=lambda cmd: _ready_arco(cmd),
        simulator_factory=lambda td, fixture: f"sim-room-{fixture}-dev")

    assert gs.state == State.SETUP
    assert gs.room.name == "TEST"
    assert gs.room.bound == {"main": "sim-room-main-dev",
                             "accent": "sim-room-accent-dev"}
    assert room_bridge.dev == "sim-room-main-dev"   # canonical: first declared


def test_boot_happy_path_via_recorded_device_reconnect():
    binding = RoomBindingRegistry()
    binding.bind("TEST", "main", "ie7")
    binding.bind("TEST", "accent", "ie8")
    config = BootConfig(room_name="TEST", bit_name="RoomCapableBit")

    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=binding, room_spec=TEST_SPEC, arco_process_cls=_ready_arco,
        known_device_connected=lambda dev: dev in ("ie7", "ie8"))

    assert gs.room.bound == {"main": "ie7", "accent": "ie8"}
    assert room_bridge.dev == "ie7"


def test_boot_fails_when_room_type_unresolvable():
    config = BootConfig(room_name="DEMO", bit_name="RoomCapableBit")
    with pytest.raises(BootFailure, match="requires an array backend"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=DEMO_SPEC,
             arco_process_cls=_ready_arco)


def test_boot_fails_when_arco_never_ready():
    config = BootConfig(room_name="TEST", bit_name="RoomCapableBit",
                        arco_ready_timeout=0.5)
    with pytest.raises(BootFailure, match="Arco failed to start"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC, arco_process_cls=_never_ready_arco,
             simulator_factory=lambda td, fixture: "sim-room-dev")


def test_boot_fails_for_unknown_bit_name():
    config = BootConfig(room_name="TEST", bit_name="NoSuchBit")
    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC, arco_process_cls=_ready_arco,
             simulator_factory=lambda td, fixture: "sim-room-dev")


def test_boot_fails_when_bit_does_not_support_resolved_room_type():
    class DemoOnlyBit(RoomCapableBit):
        room_types = {"DEMO"}

    registry = {"DemoOnlyBit": DemoOnlyBit}
    config = BootConfig(room_name="TEST", bit_name="DemoOnlyBit")
    with pytest.raises(BootFailure, match="does not support TEST"):
        boot(config, registry, arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC, arco_process_cls=_ready_arco,
             simulator_factory=lambda td, fixture: "sim-room-dev")


def test_boot_shuts_down_arco_on_any_failure_after_start():
    fake_popen = FakePopen()
    config = BootConfig(room_name="TEST", bit_name="NoSuchBit")
    with pytest.raises(BootFailure):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=lambda td, fixture: "sim-room-dev")
    assert fake_popen.signals   # Arco was told to stop, not orphaned


def test_boot_shuts_down_arco_when_wait_ready_times_out():
    from control.arco_process import ArcoProcess

    fake_popen = FakePopen()
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    config = BootConfig(room_name="TEST", bit_name="RoomCapableBit",
                        arco_ready_timeout=1.0)
    with pytest.raises(BootFailure, match="Arco failed to start"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
             arco_process_cls=lambda cmd: ArcoProcess(
                 cmd, popen=fake_popen, probe=lambda: False,
                 clock=clock, sleep=sleep),
             simulator_factory=lambda td, fixture: "sim-room-dev")
    assert fake_popen.signals   # Arco was told to stop, not orphaned


def test_wait_for_room_binding_returns_immediately_if_already_bound():
    gs, room_binding = _setup_loaded_room_bit()
    gs.room.bound = {"main": "ie7", "accent": "ie8"}
    calls = []
    wait_for_room_binding(gs, room_binding, timeout=5.0,
                          tick=lambda: calls.append(1))
    assert calls == []


def test_wait_for_room_binding_arms_each_unbound_fixture_in_turn():
    gs, room_binding = _setup_loaded_room_bit()
    ticks = [0]
    joined = []

    def tick():
        ticks[0] += 1
        armed = room_binding.armed_fixture(gs.room.name)
        if armed is not None and armed not in joined and ticks[0] % 3 == 0:
            joined.append(armed)
            gs.join(f"dev-{armed}", "ROOM_TEST_NODE")

    clock, sleep = _fake_clock()
    wait_for_room_binding(gs, room_binding, timeout=5.0, tick=tick,
                          clock=clock, sleep=sleep)

    assert gs.room.bound == {"main": "dev-main", "accent": "dev-accent"}
    assert room_binding.is_armed(gs.room.name) is False


def test_wait_for_room_binding_times_out_with_nothing_bound():
    gs, room_binding = _setup_loaded_room_bit()
    clock, sleep = _fake_clock()

    with pytest.raises(RoomBindingTimeout):
        wait_for_room_binding(gs, room_binding, timeout=1.0, tick=lambda: None,
                              clock=clock, sleep=sleep)

    assert room_binding.is_armed(gs.room.name) is False


def test_wait_for_room_binding_proceeds_partially_bound_after_timeout(caplog):
    """One unresponsive fixture must not fail the whole boot -- design spec
    section 7."""
    gs, room_binding = _setup_loaded_room_bit()
    ticks = [0]

    def tick():
        ticks[0] += 1
        if ticks[0] == 2 and room_binding.armed_fixture(gs.room.name) == "main":
            gs.join("dev-main", "ROOM_TEST_NODE")

    clock, sleep = _fake_clock()
    wait_for_room_binding(gs, room_binding, timeout=1.0, tick=tick,
                          clock=clock, sleep=sleep)   # must not raise

    assert gs.room.bound == {"main": "dev-main"}


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
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
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
    """A simulator_factory that SPAWNS, once per fixture. The contract is
    Callable[[TeardownStack, str], str]: a factory that spawns a process
    registers its own teardown on the stack it is handed."""

    def __init__(self):
        self.processes = []

    def __call__(self, teardown, fixture):
        process = _SpyProcess()
        self.processes.append(process)
        teardown.push(f"simulator-{fixture}", process.shutdown)
        return f"sim-room-{fixture}-dev"


def test_teardown_stops_the_simulator_before_arco():
    """THE regression this slice exists for. control/boot.py's own
    shutdown() used to end with arco.shutdown(), which is right within this
    module's scope and wrong composed with a caller that owns o2lite client
    subprocesses: the hub died first and the clients spent their last
    moments on a dead socket."""
    order = []

    class _RecordingProcess:
        def __init__(self, fixture):
            self.fixture = fixture

        def shutdown(self):
            order.append(f"simulator-{self.fixture}")

    class _RecordingFactory:
        def __call__(self, teardown, fixture):
            teardown.push(f"simulator-{fixture}", _RecordingProcess(fixture).shutdown)
            return f"sim-room-{fixture}-dev"

    class _RecordingArco(ArcoProcess):
        def shutdown(self):
            order.append("arco")

    config = BootConfig(room_name="TEST", bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        arco_process_cls=lambda cmd: _RecordingArco(
            cmd, popen=FakePopen(), probe=lambda: True),
        simulator_factory=_RecordingFactory())

    teardown.close()

    # Both fixture simulators (registered before Arco, since
    # _bind_room_fast_path spawns them before this function's own Arco
    # readiness/Bit-load steps complete) stop before Arco, in LIFO order.
    assert order == ["simulator-accent", "simulator-main", "arco"]


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

    config = BootConfig(room_name="TEST", bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC, arco_process_cls=_ready_arco,
        simulator_factory=lambda td, fixture: "sim-room-dev")

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

    config = BootConfig(room_name="TEST", bit_name="RoomCapableBit")
    gs, room_bridge, arco, returned = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
        arco_process_cls=lambda cmd: _ready_arco(cmd),
        simulator_factory=lambda td, fixture: "sim-room-dev",
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
    config = BootConfig(room_name="TEST", bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC, arco_process_cls=_ready_arco,
             simulator_factory=factory)

    assert len(factory.processes) == 2
    assert all(p.shutdowns == 1 for p in factory.processes)


def test_boot_shuts_down_the_simulator_when_the_bit_fails_to_load():
    class _BrokenBit(RoomCapableBit):
        def __init__(self):
            raise ValueError("bad Bit")

    factory = _SpyFactory()
    config = BootConfig(room_name="TEST", bit_name="BrokenBit")

    with pytest.raises(BootFailure, match="Bit load failed"):
        boot(config, {"BrokenBit": _BrokenBit}, arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC, arco_process_cls=_ready_arco,
             simulator_factory=factory)

    assert len(factory.processes) == 2
    assert all(p.shutdowns == 1 for p in factory.processes)


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
    config = BootConfig(room_name="TEST", bit_name="InterruptingBit")

    with pytest.raises(KeyboardInterrupt):
        boot(config, {"InterruptingBit": _InterruptingBit},
             arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=factory)

    assert len(factory.processes) == 2
    assert all(p.shutdowns == 1 for p in factory.processes)
    assert fake_popen.signals      # and Arco was told to stop too


def test_boot_still_accepts_a_factory_that_spawns_nothing():
    """Every other test in this file passes `lambda: "sim-room-dev"`, which
    has no .process at all. That must stay a no-op, not an AttributeError."""
    config = BootConfig(room_name="TEST", bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC, arco_process_cls=_ready_arco,
             simulator_factory=lambda td, fixture: "sim-room-dev")


def test_boot_shuts_arco_down_even_if_the_simulator_shutdown_raises():
    """Cleanup must not mask the failure that triggered it, and must not
    let one leaked subprocess cause a second."""
    class _RaisingProcess:
        def shutdown(self):
            raise OSError("no such process")

    class _RaisingFactory:
        def __init__(self):
            self.process = None

        def __call__(self, teardown, fixture):
            self.process = _RaisingProcess()
            teardown.push("simulator", self.process.shutdown)
            return "sim-room-dev"

    fake_popen = FakePopen()
    config = BootConfig(room_name="TEST", bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
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

    config = BootConfig(room_name="TEST", bit_name="NoSuchBit")

    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
             arco_process_cls=lambda cmd: _RaisingArco(
                 cmd, popen=FakePopen(), probe=lambda: True),
             simulator_factory=lambda td, fixture: "sim-room-dev")
