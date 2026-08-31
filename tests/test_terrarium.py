from types import SimpleNamespace

import pytest

from control.arco_process import ArcoProcess, FakePopen
from control.boot_config import BootConfig
from control.device_pool import DevicePool
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from tests.instrument_fixtures import GENERIC_SURFACE
from control.state import State
from control.teardown import TeardownStack
from control.terrarium import Terrarium, TerrariumState
from control.terrarium_config import RoomSpec, TerrariumConfig
from tests.test_engine import RoomCapableBit

TEST_PROFILE = RoomProfile(surface_id="room_test", fixtures=(
    RoomFixture(name="main", color_order="GRB",
               blocks=(RoomBlock("main", 0, 10),),
               zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),
    RoomFixture(name="accent", color_order="GRB",
               blocks=(RoomBlock("accent", 0, 10),),
               zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),
))
TEST_SPEC = RoomSpec(name="TEST", description="", backends=("devicelink",),
                     node_id="ROOM_TEST_NODE", profile=TEST_PROFILE)

DEMO_PROFILE = RoomProfile(surface_id="room_demo", fixtures=(
    RoomFixture(name="array", color_order="GRB",
               blocks=(RoomBlock("array", 0, 10),),
               zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),
))
DEMO_SPEC = RoomSpec(name="DEMO", description="",
                     backends=("devicelink", "array"),
                     node_id="ROOM_DEMO_NODE", profile=DEMO_PROFILE)


def make_config(rooms=None):
    rooms = rooms if rooms is not None else {"TEST": TEST_SPEC}
    return TerrariumConfig(schema=1, name="test-terrarium", bit_paths=(),
                           rooms=rooms, version="1-test")


def make_gs():
    return GameServer({"RoomCapableBit": RoomCapableBit})


def _ready_arco(command, popen=None):
    return ArcoProcess(command, popen=popen or FakePopen(), probe=lambda: True)


class FakeArco:
    """Records start/shutdown order for the unwind test. wait_ready can be
    told to raise, simulating an Arco that started but never came up."""
    instances = []

    def __init__(self, command, *, ready=True):
        self.command = command
        self.ready = ready
        self.events = []
        FakeArco.instances.append(self)

    def start(self):
        self.events.append("start")

    def wait_ready(self, timeout):
        self.events.append("wait_ready")
        if not self.ready:
            raise TimeoutError("never ready")

    def shutdown(self):
        self.events.append("shutdown")


def make_terrarium(config=None, *, gs=None, room_binding=None,
                   arco_process_cls=None, simulator_factory=None,
                   sweep=None, ownership_probe=None,
                   binding_store_path=None, boot_config=None,
                   runs_dir=None, run_id=None):
    config = config if config is not None else make_config()
    gs = gs if gs is not None else make_gs()
    room_binding = room_binding if room_binding is not None else RoomBindingRegistry()
    boot_config = boot_config if boot_config is not None else BootConfig(
        room_name="TEST", bit_name="RoomCapableBit")
    arco_process_cls = arco_process_cls if arco_process_cls is not None else _ready_arco
    simulator_factory = simulator_factory if simulator_factory is not None else (
        lambda td, fixture: f"sim-{fixture}-dev")
    return Terrarium(
        config, gs, room_binding, boot_config=boot_config,
        arco_command=["arco-server"], arco_process_cls=arco_process_cls,
        simulator_factory=simulator_factory, sweep=sweep,
        ownership_probe=ownership_probe, binding_store_path=binding_store_path,
        runs_dir=runs_dir, run_id=run_id)


def test_boots_in_no_room_and_refuses_load_bit_gating():
    terrarium = make_terrarium()
    assert terrarium.state == TerrariumState.NO_ROOM
    assert terrarium.room is None
    assert terrarium.gs.room is None


def test_load_room_happy_path_reaches_room_ready_and_sets_gs_room():
    terrarium = make_terrarium()
    reason = terrarium.load_room("TEST")
    assert reason is None
    assert terrarium.state == TerrariumState.ROOM_READY
    assert terrarium.room is not None
    assert terrarium.room.name == "TEST"
    assert terrarium.gs.room is terrarium.room
    assert terrarium.room.bound == {"main": "sim-main-dev",
                                    "accent": "sim-accent-dev"}


def test_load_room_refused_outside_no_room():
    terrarium = make_terrarium()
    assert terrarium.load_room("TEST") is None
    reason = terrarium.load_room("TEST")
    assert reason is not None
    assert "room_ready" in reason


def test_load_unknown_room_name_is_a_located_refusal():
    terrarium = make_terrarium()
    reason = terrarium.load_room("NOPE")
    assert reason is not None
    assert "TEST" in reason
    assert terrarium.state == TerrariumState.NO_ROOM


def test_load_room_missing_backend_fails_before_spawning_anything():
    spawned = []

    def spy_arco(command):
        spawned.append(command)
        return _ready_arco(command)

    config = make_config({"DEMO": DEMO_SPEC})
    boot_config = BootConfig(room_name="DEMO", bit_name="RoomCapableBit",
                             array_backend=None)
    terrarium = make_terrarium(config=config, arco_process_cls=spy_arco,
                               boot_config=boot_config)
    reason = terrarium.load_room("DEMO")
    assert reason is not None
    assert "array backend" in reason
    assert spawned == []
    assert terrarium.state == TerrariumState.NO_ROOM


def test_ownership_probe_conflict_refuses_and_spawns_nothing():
    spawned = []

    def spy_arco(command):
        spawned.append(command)
        return _ready_arco(command)

    terrarium = make_terrarium(arco_process_cls=spy_arco,
                               ownership_probe=lambda: "another Console owns this room")
    reason = terrarium.load_room("TEST")
    assert reason == "another Console owns this room"
    assert spawned == []
    assert terrarium.state == TerrariumState.NO_ROOM


def test_mid_load_failure_unwinds_room_stack_and_returns_to_no_room():
    FakeArco.instances = []
    terrarium = make_terrarium(
        arco_process_cls=lambda cmd: FakeArco(cmd, ready=False))
    reason = terrarium.load_room("TEST")
    assert reason is not None
    assert FakeArco.instances[0].events == ["start", "wait_ready", "shutdown"]
    assert terrarium.state == TerrariumState.NO_ROOM
    assert terrarium.gs.room is None
    assert terrarium.room is None
    assert terrarium.room_stack is None

    # A second load_room succeeds with a fresh stack.
    terrarium.arco_process_cls = _ready_arco
    reason = terrarium.load_room("TEST")
    assert reason is None
    assert terrarium.state == TerrariumState.ROOM_READY


def test_unload_room_requires_bit_idle_unless_force():
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    terrarium.gs.load_bit("RoomCapableBit")
    assert terrarium.gs.state != State.IDLE

    reason = terrarium.unload_room()
    assert reason is not None
    assert terrarium.state == TerrariumState.ROOM_READY

    reason = terrarium.unload_room(force=True)
    assert reason is None
    assert terrarium.state == TerrariumState.NO_ROOM
    assert terrarium.gs.state == State.IDLE


def test_unload_room_closes_stack_saves_bindings_and_clears_device_pool(tmp_path):
    path = str(tmp_path / "bindings.json")
    room_binding = RoomBindingRegistry()
    terrarium = make_terrarium(room_binding=room_binding,
                               binding_store_path=path)
    terrarium.load_room("TEST")
    terrarium.gs.devices.hello("dev1", "some-device", "1.0")
    assert len(terrarium.gs.devices) == 1

    reason = terrarium.unload_room()
    assert reason is None
    assert terrarium.state == TerrariumState.NO_ROOM
    assert terrarium.gs.room is None
    assert len(terrarium.gs.devices) == 0

    reloaded = RoomBindingRegistry()
    reloaded.load(path)
    assert reloaded.bound_device("TEST", "main") == "sim-main-dev"
    assert reloaded.bound_device("TEST", "accent") == "sim-accent-dev"


def test_unload_room_notifies_on_devices_change_with_empty_pool():
    events = []
    terrarium = make_terrarium()
    terrarium.load_room("TEST")
    terrarium.gs.devices.hello("dev1", "some-device", "1.0")
    terrarium.gs.add_observer(SimpleNamespace(
        on_devices_change=lambda: events.append(len(terrarium.gs.devices))))

    reason = terrarium.unload_room()
    assert reason is None
    assert events == [0]


def test_progress_stages_are_observed_in_order():
    stages = []
    terrarium = make_terrarium(sweep=lambda: None)
    terrarium.add_observer(SimpleNamespace(
        on_room_load_progress=lambda stage: stages.append(stage)))
    terrarium.load_room("TEST")
    assert stages == ["validating", "sweeping", "spawning arco",
                      "binding fixtures", "room ready"]


def test_terrarium_state_changes_are_observed():
    states = []
    terrarium = make_terrarium()
    terrarium.add_observer(SimpleNamespace(
        on_terrarium_state_change=lambda old, new: states.append((old, new))))
    terrarium.load_room("TEST")
    assert states == [(TerrariumState.NO_ROOM, TerrariumState.ROOM_LOADING),
                      (TerrariumState.ROOM_LOADING, TerrariumState.ROOM_READY)]


def test_second_ctrl_c_style_failure_in_one_step_does_not_abandon_the_rest():
    """A room-stack step raising BaseException still lets later-pushed
    (earlier-unwound) steps run -- TeardownStack.close() already guarantees
    this; assert through Terrarium anyway."""
    order = []

    def spawning_factory(td, fixture):
        if fixture == "main":
            def raising_shutdown():
                order.append("main-shutdown-attempted")
                raise KeyboardInterrupt
            td.push("sim-main", raising_shutdown)
        else:
            td.push("sim-accent", lambda: order.append("accent-shutdown"))
        return f"sim-{fixture}-dev"

    terrarium = make_terrarium(simulator_factory=spawning_factory)
    reason = terrarium.load_room("TEST")
    assert reason is None
    stack = terrarium.room_stack
    failures = stack.close()
    # sim-accent (pushed second) unwinds first; sim-main's raise is
    # captured, not propagated, and accent's step still ran.
    assert "accent-shutdown" in order
    assert "main-shutdown-attempted" in order
    assert any(name == "sim-main" for name, _ in failures)


def test_a_raising_state_observer_does_not_break_load_room_or_peers():
    """Terrarium._notify mirrors GameServer._notify's per-observer guard:
    a raising observer is logged and never interrupts the remaining
    observers or the load_room sequence itself."""
    seen = []
    terrarium = make_terrarium()
    terrarium.add_observer(SimpleNamespace(
        on_terrarium_state_change=lambda old, new: (_ for _ in ()).throw(
            RuntimeError("observer blew up"))))
    terrarium.add_observer(SimpleNamespace(
        on_terrarium_state_change=lambda old, new: seen.append(new)))

    reason = terrarium.load_room("TEST")

    assert reason is None
    assert terrarium.state == TerrariumState.ROOM_READY
    assert seen[-1] == TerrariumState.ROOM_READY


def test_a_raising_progress_observer_does_not_break_load_room_or_peers():
    seen = []
    terrarium = make_terrarium()
    terrarium.add_observer(SimpleNamespace(
        on_room_load_progress=lambda stage: (_ for _ in ()).throw(
            RuntimeError("observer blew up"))))
    terrarium.add_observer(SimpleNamespace(
        on_room_load_progress=lambda stage: seen.append(stage)))

    reason = terrarium.load_room("TEST")

    assert reason is None
    assert terrarium.state == TerrariumState.ROOM_READY
    assert seen == ["validating", "spawning arco", "binding fixtures",
                    "room ready"]


def test_construction_with_runs_dir_records_a_supervisor_entry(tmp_path):
    """The wiring this Task adds: given runs_dir/run_id, Terrarium.__init__
    itself (not load_room) writes a "supervisor" SpawnRecord for this
    process's own pid, before any load_room ever runs -- this is what lets
    sweep_stale (control/run_record.py) tell "another live run's dir" apart
    from "a crashed prior run's dir" (controller ruling 2026-08-27, design
    spec section 5)."""
    import os

    from control.run_record import RunRecorder

    make_terrarium(runs_dir=str(tmp_path), run_id="run-1")

    records = RunRecorder.load_all(str(tmp_path))
    assert len(records) == 1
    assert records[0].pid == os.getpid()
    assert records[0].role == "supervisor"
