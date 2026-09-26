from types import SimpleNamespace

import pytest

from control.arco_process import ArcoProcess, FakePopen
from control.boot_config import BootConfig
from control.device_pool import DevicePool
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.rooms import Room
from tests.instrument_fixtures import GENERIC_SURFACE
from control.state import State
from control.teardown import TeardownStack
from control.terrarium import (RoomBindingTimeout, Terrarium,
                               TerrariumState, wait_for_room_binding)
from control.terrarium_config import ArtNetOutput, RoomSpec, TerrariumConfig
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
                   runs_dir=None, run_id=None, arco_ready_timeout=None):
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
        runs_dir=runs_dir, run_id=run_id, arco_ready_timeout=arco_ready_timeout)


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


def test_recycle_room_refuses_outside_room_ready():
    terr = make_terrarium()
    reason = terr.recycle_room()
    assert reason is not None and "no_room" in reason


def test_recycle_room_replaces_arco_and_room_stack():
    terr = make_terrarium()
    assert terr.load_room("TEST") is None
    first_arco = terr.arco
    first_stack = terr.room_stack
    assert terr.recycle_room() is None
    assert terr.state is TerrariumState.ROOM_READY
    assert terr.arco is not first_arco
    assert terr.room_stack is not first_stack
    assert terr.room.name == "TEST"


def test_recycle_room_aborts_a_live_bit_first():
    terr = make_terrarium()
    assert terr.load_room("TEST") is None
    terr.gs.load_bit("RoomCapableBit")
    terr.gs.run()
    assert terr.recycle_room() is None
    assert terr.gs.state is State.IDLE


def test_recycle_room_load_failure_reports_and_lands_no_room(monkeypatch):
    terr = make_terrarium()
    assert terr.load_room("TEST") is None
    monkeypatch.setattr(terr, "load_room",
                        lambda name: "boom: injected load failure")
    reason = terr.recycle_room()
    assert reason == "boom: injected load failure"
    assert terr.state is TerrariumState.NO_ROOM


@pytest.mark.parametrize("override,expect_room_spec_default", [
    pytest.param(42.5, False, id="override_wins"),
    pytest.param(None, True, id="defaults_to_room_spec"),
])
def test_arco_ready_timeout_resolution(override, expect_room_spec_default):
    """--arco-ready-timeout was a dead flag: harness/terrarium_boot.py set
    BootConfig.arco_ready_timeout, but load_room waited on the RoomSpec's
    value. The Terrarium-level override now reaches the wait, and falls
    back to the RoomSpec's own value when no override is given."""
    seen = []

    class RecordingArco(FakeArco):
        def wait_ready(self, timeout):
            seen.append(timeout)
            super().wait_ready(timeout)

    kwargs = {"arco_process_cls": RecordingArco}
    if override is not None:
        kwargs["arco_ready_timeout"] = override
    terrarium = make_terrarium(**kwargs)
    assert terrarium.load_room("TEST") is None
    if expect_room_spec_default:
        assert seen == [terrarium.config.rooms["TEST"].arco_ready_timeout]
    else:
        assert seen == [override]


def test_loading_room_is_set_only_during_load_room():
    seen = []

    def factory(teardown, fixture):
        seen.append(terrarium.loading_room)
        return f"sim-{fixture}-dev"

    terrarium = make_terrarium(simulator_factory=factory)
    assert terrarium.loading_room is None
    assert terrarium.load_room("TEST") is None
    assert seen == ["TEST", "TEST"]        # one call per fixture, both mid-load
    assert terrarium.loading_room is None


def test_loading_room_is_cleared_after_a_failed_load():
    terrarium = make_terrarium(
        ownership_probe=lambda: "another Console owns this room")
    assert terrarium.load_room("TEST") is not None
    assert terrarium.loading_room is None


def _artnet(room, fixture):
    return ArtNetOutput(room=room, fixture=fixture, host="127.0.0.1",
                        max_amps=1.0)


# Built directly, bypassing validate_artnet_outputs: TEST_SPEC/DEMO_SPEC
# are GRB, which a parsed terrarium.toml would refuse for [[artnet]].
def _config_with_artnet(rooms, *outputs):
    return TerrariumConfig(schema=1, name="test-terrarium", bit_paths=(),
                           rooms=rooms, version="1-test",
                           artnet_outputs=tuple(outputs))


class _RecordingBinding(RoomBindingRegistry):
    def __init__(self):
        super().__init__()
        self.armed = []

    def arm(self, room_name, fixture, window_seconds):
        self.armed.append(fixture)
        super().arm(room_name, fixture, window_seconds)


def _recording_factory(calls):
    def factory(teardown, fixture):
        calls.append(fixture)
        return f"sim-{fixture}-dev"
    return factory


def test_an_artnet_covered_fixture_gets_no_simulator_and_stays_unbound():
    calls = []
    binding = _RecordingBinding()
    terrarium = make_terrarium(
        _config_with_artnet({"DEMO": DEMO_SPEC}, _artnet("DEMO", "array")),
        room_binding=binding, simulator_factory=_recording_factory(calls),
        boot_config=BootConfig(room_name="DEMO", bit_name="RoomCapableBit",
                               array_backend="simulator"))
    assert terrarium.load_room("DEMO") is None
    assert terrarium.state == TerrariumState.ROOM_READY
    assert calls == []
    assert binding.armed == []
    assert terrarium.room.bound == {}


def test_an_all_artnet_room_loads_without_the_simulator_flag():
    """The array_backend=None path validate_rooms admits on [[artnet]]
    coverage alone used to time out in wait_for_room_binding."""
    terrarium = make_terrarium(
        _config_with_artnet({"DEMO": DEMO_SPEC}, _artnet("DEMO", "array")),
        boot_config=BootConfig(room_name="DEMO", bit_name="RoomCapableBit"))
    terrarium.simulator_factory = None
    assert terrarium.load_room("DEMO") is None
    assert terrarium.room.bound == {}


def test_a_mixed_room_spawns_a_simulator_only_for_its_uncovered_fixture():
    calls = []
    terrarium = make_terrarium(
        _config_with_artnet({"TEST": TEST_SPEC}, _artnet("TEST", "accent")),
        simulator_factory=_recording_factory(calls))
    assert terrarium.load_room("TEST") is None
    assert calls == ["main"]
    assert terrarium.room.bound == {"main": "sim-main-dev"}


def test_artnet_coverage_for_another_room_changes_nothing():
    calls = []
    terrarium = make_terrarium(
        _config_with_artnet({"TEST": TEST_SPEC, "DEMO": DEMO_SPEC},
                            _artnet("DEMO", "array")),
        simulator_factory=_recording_factory(calls))
    assert terrarium.load_room("TEST") is None
    assert calls == ["main", "accent"]


def test_a_recorded_binding_for_a_covered_fixture_is_not_reconnected():
    binding = RoomBindingRegistry()
    binding.bind("DEMO", "array", "old-dev")
    terrarium = make_terrarium(
        _config_with_artnet({"DEMO": DEMO_SPEC}, _artnet("DEMO", "array")),
        room_binding=binding,
        boot_config=BootConfig(room_name="DEMO", bit_name="RoomCapableBit"))
    terrarium.simulator_factory = None
    terrarium.known_device_connected = lambda dev: True
    assert terrarium.load_room("DEMO") is None
    assert terrarium.room.bound == {}


def _waiting_gs(spec, bound=None):
    gs = make_gs()
    gs.room = Room(name=spec.name, profile=spec.profile, node_id=spec.node_id,
                   bound=dict(bound or {}))
    return gs


class _StepClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_never_arms_a_skipped_fixture():
    binding = _RecordingBinding()
    clock = _StepClock()
    gs = _waiting_gs(TEST_SPEC)
    wait_for_room_binding(gs, binding, 0.2, tick=lambda: None, clock=clock,
                          sleep=clock.sleep, skip=frozenset({"main"}))
    assert binding.armed == ["accent"]


def test_wait_with_a_skipped_fixture_does_not_raise_when_nothing_binds():
    clock = _StepClock()
    gs = _waiting_gs(TEST_SPEC)
    wait_for_room_binding(gs, RoomBindingRegistry(), 0.2, tick=lambda: None,
                          clock=clock, sleep=clock.sleep,
                          skip=frozenset({"accent"}))
    assert gs.room.bound == {}


def test_wait_returns_at_once_when_every_fixture_is_skipped():
    binding = _RecordingBinding()
    gs = _waiting_gs(DEMO_SPEC)
    wait_for_room_binding(gs, binding, 0.2, tick=lambda: None,
                          skip=frozenset({"array"}))
    assert binding.armed == []


def test_wait_without_skip_still_raises_when_nothing_binds():
    clock = _StepClock()
    gs = _waiting_gs(TEST_SPEC)
    with pytest.raises(RoomBindingTimeout):
        wait_for_room_binding(gs, RoomBindingRegistry(), 0.2,
                              tick=lambda: None, clock=clock,
                              sleep=clock.sleep)


VENUE_PROFILE = RoomProfile(surface_id="room_venue", fixtures=(
    RoomFixture(name="bars", color_order="RGBW",
               blocks=(RoomBlock("m1", 0, 10),),
               zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),
    RoomFixture(name="fiber", color_order="RGBW",
               blocks=(RoomBlock("e1", 0, 3),),
               zones=(RoomZone("all", 0, 3),), instrument=GENERIC_SURFACE),
))
VENUE_SPEC = RoomSpec(name="VENUE", description="",
                      backends=("devicelink", "array"),
                      node_id="ROOM_VENUE_NODE", profile=VENUE_PROFILE)


def _venue_config(*covered):
    """A config holding only VENUE, with an [[artnet]] output for each
    fixture name in `covered`."""
    return TerrariumConfig(
        schema=1, name="test-terrarium", bit_paths=(),
        rooms={"VENUE": VENUE_SPEC}, version="1-test",
        artnet_outputs=tuple(
            ArtNetOutput(room="VENUE", fixture=f, host="127.0.0.1", max_amps=1.0)
            for f in covered))


def test_an_uncovered_fixture_with_nothing_to_bind_still_times_out():
    """A VENUE config with no [[artnet]] output on either fixture and no
    simulator factory has nothing that can drive or bind a fixture, so the
    load still fails with "no device joined" rather than loading empty."""
    terrarium = make_terrarium(
        config=_venue_config(),
        boot_config=BootConfig(room_name="VENUE", bit_name="RoomCapableBit",
                               array_backend="simulator",
                               room_setup_timeout=0.2))
    terrarium.simulator_factory = None
    reason = terrarium.load_room("VENUE")
    assert reason is not None and "no device joined" in reason
    assert terrarium.state == TerrariumState.NO_ROOM
