from types import SimpleNamespace

import pytest

from bits.test.test_bit import TestBit
from control.bit import Bit
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.function_view import function_view
from control.instrument import DEFAULTSHROOM, Instrument, TUNESHROOM
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from tests.instrument_fixtures import GENERIC_SURFACE
from control.roles import Role, RoleClass, RoleTable
from control.rooms import Room
from control.state import State

ROOM_PROFILE = RoomProfile(surface_id="room_test", fixtures=(
    RoomFixture(name="main", color_order="GRB",
               blocks=(RoomBlock("main", 0, 10),),
               zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),
    RoomFixture(name="accent", color_order="GRB",
               blocks=(RoomBlock("accent", 0, 5),),
               zones=(RoomZone("all", 0, 5),), instrument=GENERIC_SURFACE)))


def make_room(name="TEST"):
    return Room(name=name, profile=ROOM_PROFILE, node_id="ROOM_TEST_NODE")


class RoomCapableBit(TestBit):
    # TestBit's own room_manifests (inherited) is what the engine reads to
    # synthesize the ROOM role now -- this subclass no longer builds one
    # itself.
    room_types = {"TEST"}


def test_add_observer_notifies_multiple_observers_of_state_changes():
    from types import SimpleNamespace
    from bits.test.test_bit import TestBit
    from control.engine import GameServer
    a, b = [], []
    server = GameServer({"TestBit": TestBit})
    server.add_observer(SimpleNamespace(
        on_state_change=lambda old, new: a.append(new)))
    server.add_observer(SimpleNamespace(
        on_state_change=lambda old, new: b.append(new)))
    server.load_bit("TestBit")
    assert a == b and len(a) >= 3  # both saw the same transitions


def test_observer_exception_does_not_break_engine_or_peers():
    from types import SimpleNamespace
    from bits.test.test_bit import TestBit
    from control.engine import GameServer
    seen = []
    server = GameServer({"TestBit": TestBit})

    def boom(old, new):
        raise RuntimeError("observer blew up")

    server.add_observer(SimpleNamespace(on_state_change=boom))
    server.add_observer(SimpleNamespace(
        on_state_change=lambda old, new: seen.append(new)))
    server.load_bit("TestBit")            # must not raise
    assert len(seen) >= 3                  # peer still notified


def test_on_devices_change_fires_on_hello_join_and_unload():
    from types import SimpleNamespace
    from bits.test.test_bit import TestBit
    from control.engine import GameServer
    calls = []
    server = GameServer({"TestBit": TestBit})
    server.add_observer(SimpleNamespace(
        on_devices_change=lambda: calls.append("devices")))
    server.hello("ie1", "Shroom One", "1")        # +1
    server.load_bit("TestBit")
    server.join("ie1", "TEST_PLAYER_NODE")        # +1 (granted)
    n_before_abort = len(calls)
    server.abort()                                 # +1 (unload releases devices)
    assert len(calls) == n_before_abort + 1
    assert n_before_abort == 2


class ExplodingCompleteBit(TestBit):
    def on_complete(self) -> None:
        raise RuntimeError("boom")


class ExplodingUnloadBit(TestBit):
    def on_unload(self) -> None:
        raise RuntimeError("boom")


class RaisingRoleTableBit(Bit):
    @property
    def role_table(self) -> RoleTable:
        raise RuntimeError("role table exploded")


class BadManifestBit(Bit):
    @property
    def role_table(self) -> RoleTable:
        bad = Role(name="player", role_class=RoleClass.SHARED, capacity=None,
                   scored=True, light_manifest=["not", "a", "dict"])
        return RoleTable(roles={"player": bad}, node_map={"N": ["player"]})


WELCOME_LIGHT_MANIFEST = {
    "instruments": [
        {"instrument": "bloom", "target": "primary",
         "lanes": [{"source": "note", "dest": "trigger"}]},
    ],
}

WELCOME_PAIR = {
    "light": {"instrument": "bloom", "duration": 2.0},
    "audio": {"instrument": "chime", "duration": 2.0},
}


class WelcomeBit(Bit):
    version = "0.9"

    @property
    def role_table(self) -> RoleTable:
        greeter = Role(name="greeter", role_class=RoleClass.UNIQUE,
                       capacity=1, scored=True,
                       light_manifest=WELCOME_LIGHT_MANIFEST,
                       welcome=WELCOME_PAIR)
        jammer = Role(name="jammer", role_class=RoleClass.JAM,
                      capacity=None, scored=False)
        return RoleTable(
            roles={"greeter": greeter, "jammer": jammer},
            node_map={"NODE_GREET": ["greeter"], "NODE_JAM": ["jammer"]},
        )


REGISTRY = {
    "test_bit": TestBit,
    "exploding_complete_bit": ExplodingCompleteBit,
    "exploding_unload_bit": ExplodingUnloadBit,
    "raising_role_table_bit": RaisingRoleTableBit,
    "bad_manifest_bit": BadManifestBit,
    "welcome_bit": WelcomeBit,
}


def make_server() -> GameServer:
    return GameServer(bit_registry=REGISTRY)


def test_load_bit_moves_idle_to_setup():
    server = make_server()
    server.load_bit("test_bit")
    assert server.state == State.SETUP
    assert isinstance(server.bit, TestBit)


def test_load_bit_requires_idle():
    server = make_server()
    server.load_bit("test_bit")
    with pytest.raises(InvalidTransition):
        server.load_bit("test_bit")


def test_load_bit_unknown_name_raises_and_stays_idle():
    server = make_server()
    with pytest.raises(BitLoadError):
        server.load_bit("no_such_bit")
    assert server.state == State.IDLE
    assert server.bit is None


def test_run_requires_setup():
    server = make_server()
    with pytest.raises(InvalidTransition):
        server.run()


def test_join_denied_when_no_bit_loaded():
    server = make_server()
    result = server.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted is False
    assert result.reason == "no Bit accepting registrations"


def test_join_granted_blob_carries_default_carried_instruments_event_triggers():
    server = make_server()
    server.hello("ie1", "Testshroom 1", "1.0")
    server.load_bit("test_bit")
    result = server.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted
    assert result.config["triggers"] == {
        "tap": {"peak_g": 2.0, "window_ms": 200, "double_ms": 400},
        "shake": {"peak_g": 2.0, "window_ms": 200},
    }


def test_join_granted_blob_omits_triggers_for_carried_instrument_without_any():
    from control.instrument import Instrument
    server = make_server()
    server.hello("ie1", "Testshroom 1", "1.0")
    server.load_bit("test_bit")
    no_event_triggers = Instrument(
        name="quiet_widget",
        capabilities=frozenset({"light.pixels", "gesture.tilt"}),
        accepted_cues=("midi",))
    server.devices.get("ie1").carried = no_event_triggers
    result = server.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted
    assert "triggers" not in result.config


def test_requires_less_role_join_blob_still_carries_event_triggers():
    """Fix round 1: event-trigger thresholds are a property of the carried
    instrument's server-owned detection contract, independent of slot
    gating -- TestBit's jammer role has no Role.requires at all, but a
    device joining it still needs its carrier's tap/shake thresholds."""
    server = make_server()
    server.hello("ie1", "Testshroom 1", "1.0")
    server.load_bit("test_bit")
    result = server.join("ie1", "TEST_JAM_NODE")
    assert result.granted
    assert result.slot is None
    assert result.instrument is None
    assert result.config["triggers"] == {
        "tap": {"peak_g": 2.0, "window_ms": 200, "double_ms": 400},
        "shake": {"peak_g": 2.0, "window_ms": 200},
    }


def test_room_join_blob_carries_no_triggers():
    binding = RoomBindingRegistry()
    server = GameServer(bit_registry={"RoomCapableBit": RoomCapableBit},
                        room_binding=binding)
    server.room = make_room()
    server.load_bit("RoomCapableBit")
    binding.arm("TEST", "main", window_seconds=10.0)
    result = server.join("sim-room", "ROOM_TEST_NODE")
    assert result.granted
    assert result.config is None


def test_full_lifecycle_reaches_idle_and_releases_devices():
    server = make_server()
    released = []
    server.on_release = released.append

    server.hello("ie1", "Testshroom 1", "1.0")
    server.load_bit("test_bit")
    assert server.state == State.SETUP

    join_result = server.join("ie1", "TEST_PLAYER_NODE")
    assert join_result.granted is True

    server.run()
    assert server.state == State.RUNNING

    server.tick(1.0)
    assert server.state == State.RUNNING  # 1.0s < TestBit's 2.0s default
    server.tick(1.5)  # 2.5s elapsed total -- crosses the completion threshold

    assert server.state == State.IDLE
    assert released == ["ie1"]
    assert server.bit is None
    assert server.devices.known("ie1") is True  # pool survives unload


def test_scored_join_denied_once_running_jam_still_allowed():
    server = make_server()
    server.load_bit("test_bit")
    server.run()
    scored = server.join("ie1", "TEST_PLAYER_NODE")
    jam = server.join("ie2", "TEST_JAM_NODE")
    assert scored.granted is False
    assert jam.granted is True


def test_on_complete_exception_still_reaches_idle():
    server = make_server()
    server.load_bit("exploding_complete_bit")
    server.run()
    server.tick(3.0)
    assert server.state == State.IDLE


def test_on_unload_exception_still_reaches_idle():
    server = make_server()
    server.load_bit("exploding_unload_bit")
    server.run()
    server.tick(3.0)
    assert server.state == State.IDLE


def test_on_state_change_fires_for_every_transition():
    server = make_server()
    transitions = []
    server.add_observer(SimpleNamespace(
        on_state_change=lambda old, new: transitions.append((old, new))))

    server.load_bit("test_bit")
    server.run()
    server.tick(1.0)
    server.tick(1.5)  # crosses TestBit's 2.0s completion threshold

    assert transitions == [
        (State.IDLE, State.LOADING),
        (State.LOADING, State.LOADED),
        (State.LOADED, State.SETUP),
        (State.SETUP, State.RUNNING),
        (State.RUNNING, State.COMPLETING),
        (State.COMPLETING, State.UNLOADING),
        (State.UNLOADING, State.IDLE),
    ]


def test_on_state_change_fires_on_failed_load_bit():
    server = make_server()
    transitions = []
    server.add_observer(SimpleNamespace(
        on_state_change=lambda old, new: transitions.append((old, new))))

    with pytest.raises(BitLoadError):
        server.load_bit("no_such_bit")

    assert transitions == [
        (State.IDLE, State.LOADING),
        (State.LOADING, State.IDLE),
    ]


def test_on_registration_change_fires_only_on_granted_join():
    server = make_server()
    server.load_bit("test_bit")
    calls = []
    server.add_observer(SimpleNamespace(
        on_registration_change=lambda: calls.append(server.registration.counts())))

    denied = server.join("ie1", "NO_SUCH_NODE")
    assert denied.granted is False
    assert calls == []

    granted = server.join("ie1", "TEST_PLAYER_NODE")
    assert granted.granted is True
    assert len(calls) == 1
    counts = {name: count for name, count, _capacity in calls[0]}
    assert counts["player"] == 1


def test_abort_requires_active_bit():
    server = make_server()
    with pytest.raises(InvalidTransition):
        server.abort()


def test_abort_from_setup_unloads_and_releases_devices():
    server = make_server()
    released = []
    server.on_release = released.append
    server.hello("ie1", "Testshroom 1", "1.0")
    server.load_bit("test_bit")
    server.join("ie1", "TEST_PLAYER_NODE")

    server.abort()

    assert server.state == State.IDLE
    assert server.bit is None
    assert released == ["ie1"]


def test_a_raising_on_release_does_not_strand_later_devices_or_wedge_unload():
    # Any transport can wire up on_release, not just DeviceLinkAgent -- this
    # guards _unload's release loop directly, independent of any transport's
    # own error handling. It must fail if that loop's try/except regresses.
    server = make_server()
    seen = []

    def raise_on_first(dev):
        seen.append(dev)
        if len(seen) == 1:
            raise RuntimeError("transport blew up releasing the first device")

    server.on_release = raise_on_first
    server.hello("ie1", "Testshroom 1", "1.0")
    server.hello("ie2", "Testshroom 2", "1.0")
    server.load_bit("test_bit")
    server.join("ie1", "TEST_PLAYER_NODE")
    server.join("ie2", "TEST_JAM_NODE")

    server.abort()  # must not raise, must not wedge in UNLOADING

    assert server.state == State.IDLE
    assert set(seen) == {"ie1", "ie2"}
    assert server.registration is None


def test_abort_runs_on_complete_before_unloading():
    server = make_server()
    server.load_bit("test_bit")
    server.run()
    bit = server.bit

    server.abort()

    assert bit._completed is True
    assert server.state == State.IDLE


def test_abort_survives_on_complete_exception():
    server = make_server()
    server.load_bit("exploding_complete_bit")

    server.abort()  # must not raise

    assert server.state == State.IDLE


def test_load_bit_records_bit_name_and_clears_it_on_unload():
    server = make_server()
    assert server.bit_name is None
    server.load_bit("test_bit")
    assert server.bit_name == "test_bit"
    server.abort()
    assert server.bit_name is None


def test_bit_version_defaults_to_empty_string():
    # TestBit itself now declares a version (light-manifest v2 adoption); use
    # a Bit subclass that doesn't override it to exercise the base default.
    assert RaisingRoleTableBit().version == ""


def test_load_bit_raising_role_table_fails_cleanly_to_idle():
    server = make_server()
    with pytest.raises(BitLoadError):
        server.load_bit("raising_role_table_bit")
    assert server.state == State.IDLE
    assert server.bit is None
    assert server.bit_name is None
    assert server.registration is None
    # regression: the engine must not be wedged -- a good load still works
    server.load_bit("test_bit")
    assert server.state == State.SETUP


def test_load_bit_invalid_manifest_fails_cleanly_to_idle():
    server = make_server()
    with pytest.raises(BitLoadError, match=r"role 'player' light_manifest"):
        server.load_bit("bad_manifest_bit")
    assert server.state == State.IDLE
    assert server.bit is None
    assert server.bit_name is None
    assert server.registration is None


def test_granted_join_carries_composed_config_blob():
    server = make_server()
    server.load_bit("welcome_bit")
    result = server.join("ie1", "NODE_GREET")
    assert result.granted is True
    assert result.config == {
        "role": "greeter",
        "class": "UNIQUE",
        "scored": True,
        "light_manifest": {
            "instruments": WELCOME_LIGHT_MANIFEST["instruments"],
            "bit_name": "welcome_bit",
            "bit_version": "0.9",
            "role": "greeter",
            "welcome": WELCOME_PAIR["light"],
        },
        "uses": [],
        "samples": [],
        "triggers": {
            "tap": {"peak_g": 2.0, "window_ms": 200, "double_ms": 400},
            "shake": {"peak_g": 2.0, "window_ms": 200},
        },
        "instrument": {
            "name": "tuneshroom",
            "capabilities": sorted(TUNESHROOM.capabilities),
            "pixels": 12,
            "ambient": {"light": TUNESHROOM.light_manifest,
                        "ugen": TUNESHROOM.ugen_manifest},
            "functions": [function_view(f) for f in TUNESHROOM.functions],
        },
    }


def test_denied_join_carries_no_config():
    server = make_server()
    server.load_bit("welcome_bit")
    server.join("ie1", "NODE_GREET")
    denied = server.join("ie2", "NODE_GREET")  # capacity 1
    assert denied.granted is False
    assert denied.config is None


def test_role_switch_composes_the_new_roles_config():
    server = make_server()
    server.load_bit("welcome_bit")
    server.join("ie1", "NODE_GREET")
    switch = server.join("ie1", "NODE_JAM")
    assert switch.granted is True
    assert switch.config["role"] == "jammer"
    assert switch.config["scored"] is False
    # jammer declares nothing: bare provenance, no welcome key
    assert switch.config["light_manifest"] == {
        "bit_name": "welcome_bit", "bit_version": "0.9", "role": "jammer"}


def test_join_with_no_bit_loaded_carries_no_config():
    server = make_server()
    result = server.join("ie1", "NODE_GREET")
    assert result.granted is False
    assert result.config is None


def test_room_node_join_denied_while_unarmed():
    server = GameServer({"RoomCapableBit": RoomCapableBit},
                        room_binding=RoomBindingRegistry())
    server.room = make_room()
    server.load_bit("RoomCapableBit")
    result = server.join("ie9", "ROOM_TEST_NODE")
    assert result.granted is False
    assert result.reason == "no such node"


def test_room_node_join_binds_device_once_armed():
    binding = RoomBindingRegistry()
    server = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    server.room = make_room()
    server.load_bit("RoomCapableBit")
    binding.arm("TEST", "main", window_seconds=10.0)

    result = server.join("ie9", "ROOM_TEST_NODE")

    assert result.granted is True
    assert result.role_class == RoleClass.ROOM
    assert result.config is None
    assert server.room.bound == {"main": "ie9"}
    assert binding.bound_device("TEST", "main") == "ie9"


def test_room_join_does_not_disturb_player_joins():
    binding = RoomBindingRegistry()
    server = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    server.room = make_room()
    server.load_bit("RoomCapableBit")

    result = server.join("ie1", "TEST_PLAYER_NODE")

    assert result.granted is True
    assert result.role_class == RoleClass.SHARED
    assert result.config is not None    # normal player composition, unchanged


def test_join_without_room_configured_ignores_room_gating():
    # A GameServer with no room_binding/room set (the pre-Room-concept
    # construction path) must keep working exactly as before.
    server = GameServer({"TestBit": TestBit})
    server.load_bit("TestBit")
    result = server.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted is True


def test_a_declared_generator_is_dispatched_once_per_running_tick():
    """update(dt) answers 'am I done'; a declared GENERATOR function answers
    'what should happen' without any device doing something. Without this a
    Bit could not animate the Room on its own -- Bit.cues(at) used to be
    that hook; a declared generator, engine-run every RUNNING tick, replaces
    it (see control/generator_runner.py)."""
    from control.bit import Bit
    from control.cues import ROOM
    from control.functions import (Function, FunctionKind, FunctionTable,
                                    GeneratorSpec)
    from control.roles import Role, RoleClass, RoleTable

    class AmbientBit(Bit):
        version = "0.1"
        @property
        def role_table(self):
            player = Role(name="player", role_class=RoleClass.SHARED,
                          capacity=None, scored=False)
            return RoleTable(roles={"player": player},
                             node_map={"NODE_A": ["player"]})
        @property
        def function_table(self):
            return FunctionTable(functions={
                "drift": Function(
                    name="drift", description="Ambient drift",
                    kind=FunctionKind.GENERATOR,
                    generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                                            waveform="triangle", period=12.0,
                                            lo=0, hi=254)),
            })

    bit = AmbientBit()
    gs = GameServer({"ab": lambda: bit}, cue_horizon=0.06,
                    clock=lambda: 1000.0)
    gs.room = make_room()
    gs.room.bound = {"main": "sim-room"}
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    gs.load_bit("ab")
    gs.run()
    gs.tick(3.0)   # elapsed=3.0 of a 12s triangle from lo=0 hi=254 -> 127

    assert seen == [("sim-room", 0xB0, 74, 127, pytest.approx(1000.06))]


def test_bit_fires_are_not_dispatched_on_the_completing_tick():
    """A Bit that just signalled done is tearing down; dispatching a fire for
    it would put light on a device the engine is about to release."""
    from control.bit import Bit
    from control.roles import Role, RoleClass, RoleTable

    class DoneBit(Bit):
        version = "0.1"
        def __init__(self):
            self.fires_calls = 0
        @property
        def role_table(self):
            player = Role(name="player", role_class=RoleClass.SHARED,
                          capacity=None, scored=False)
            return RoleTable(roles={"player": player},
                             node_map={"NODE_A": ["player"]})
        def update(self, dt):
            return True
        def fires(self, at):
            self.fires_calls += 1
            return []

    bit = DoneBit()
    gs = GameServer({"db": lambda: bit})
    gs.load_bit("db")
    gs.run()
    gs.tick(0.02)
    assert bit.fires_calls == 0


def test_raising_bit_fires_does_not_wedge_the_tick():
    """Same guarantee every other Bit hook has: a misbehaving Bit must never
    stop Control reaching COMPLETING."""
    from control.bit import Bit
    from control.roles import Role, RoleClass, RoleTable

    class BadBit(Bit):
        version = "0.1"
        @property
        def role_table(self):
            player = Role(name="player", role_class=RoleClass.SHARED,
                          capacity=None, scored=False)
            return RoleTable(roles={"player": player},
                             node_map={"NODE_A": ["player"]})
        def fires(self, at):
            raise RuntimeError("boom")

    gs = GameServer({"bb": BadBit})
    gs.load_bit("bb")
    gs.run()
    gs.tick(0.02)                    # must not raise
    assert gs.state == State.RUNNING


def test_reap_stale_removes_a_silent_unjoined_device():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.hello("ie1", "sim", "1")
    changes = []
    gs.add_observer(SimpleNamespace(
        on_devices_change=lambda: changes.append("devices"),
        on_registration_change=lambda: changes.append("registration")))

    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)

    assert reaped == ["ie1"]
    assert [d.dev for d in gs.devices.all()] == []
    assert changes == ["devices"]   # no registration_change: it never joined


def test_reap_stale_leaves_a_fresh_device_alone():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.hello("ie1", "sim", "1")
    clk.t = 3.0
    reaped = gs.reap_stale(timeout=50.0)
    assert reaped == []
    assert gs.devices.known("ie1") is True


def test_reap_stale_frees_a_scored_roles_slot_immediately():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.load_bit("test_bit")
    gs.hello("ie1", "sim", "1")
    gs.join("ie1", "TEST_PLAYER_NODE")
    released = []
    gs.on_release = released.append
    counts_before = dict((n, c) for n, c, _ in gs.registration.counts())
    assert counts_before["player"] == 1

    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)

    assert reaped == ["ie1"]
    counts_after = dict((n, c) for n, c, _ in gs.registration.counts())
    assert counts_after["player"] == 0
    assert released == ["ie1"]
    assert gs.devices.known("ie1") is False


def test_reap_stale_batches_observer_notifications_once():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.load_bit("test_bit")
    for dev in ("ie1", "ie2"):
        gs.hello(dev, "sim", "1")
        gs.join(dev, "TEST_PLAYER_NODE")
    calls = []
    gs.add_observer(SimpleNamespace(
        on_devices_change=lambda: calls.append("devices"),
        on_registration_change=lambda: calls.append("registration")))

    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)

    assert sorted(reaped) == ["ie1", "ie2"]
    assert calls == ["devices", "registration"] or calls == ["registration", "devices"]
    assert len(calls) == 2   # not 2 per device


def test_reap_stale_never_reaps_a_room_bound_device():
    from control.room_binding import RoomBindingRegistry
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    binding = RoomBindingRegistry()
    gs = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding,
                    clock=lambda: clk.t)
    gs.room = make_room()
    gs.load_bit("RoomCapableBit")
    gs.hello("sim-room", "room", "1")
    binding.arm("TEST", "main", window_seconds=10.0)
    gs.join("sim-room", "ROOM_TEST_NODE")
    assert gs.room.bound == {"main": "sim-room"}

    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)

    assert reaped == []
    assert gs.devices.known("sim-room") is True
    assert gs.room.bound == {"main": "sim-room"}
    assert "sim-room" in gs.registration.assignments


def test_reap_stale_on_release_exception_does_not_stop_the_rest():
    from types import SimpleNamespace
    clk = SimpleNamespace(t=0.0)
    gs = GameServer({"test_bit": TestBit}, clock=lambda: clk.t)
    gs.load_bit("test_bit")
    for dev in ("ie1", "ie2"):
        gs.hello(dev, "sim", "1")
        gs.join(dev, "TEST_PLAYER_NODE")

    def boom(dev):
        raise RuntimeError("transport exploded")

    gs.on_release = boom
    clk.t = 100.0
    reaped = gs.reap_stale(timeout=5.0)   # must not raise
    assert sorted(reaped) == ["ie1", "ie2"]
    assert gs.devices.known("ie1") is False
    assert gs.devices.known("ie2") is False


# --- Task 2: hello resolves the carried instrument -------------------------


def test_hello_default_carried_is_defaultshroom():
    server = GameServer({})
    server.hello("ie1", "Shroom One", "1")
    assert server.devices.get("ie1").carried is DEFAULTSHROOM


def test_hello_resolves_a_config_declared_instrument_name():
    glowharp = Instrument(name="glowharp",
                           capabilities=frozenset({"light.pixels"}))
    server = GameServer({}, carried_instruments={"glowharp": glowharp})
    server.hello("ie1", "Shroom One", "1", instrument="glowharp")
    assert server.devices.get("ie1").carried is glowharp


def test_hello_resolves_the_tuneshroom_constant_by_name():
    server = GameServer({})
    server.hello("ie1", "Shroom One", "1", instrument="tuneshroom")
    assert server.devices.get("ie1").carried is TUNESHROOM


def test_hello_with_unknown_instrument_name_falls_back_to_defaultshroom_and_warns():
    warnings = []

    class Obs:
        def on_device_warning(self, message):
            warnings.append(message)

    server = GameServer({})
    server.add_observer(Obs())
    server.hello("ie1", "Shroom One", "1", instrument="nope")
    assert server.devices.get("ie1").carried is DEFAULTSHROOM
    assert len(warnings) == 1
    assert "ie1" in warnings[0]
    assert "nope" in warnings[0]
    assert "defaultshroom" in warnings[0]


def test_hello_heartbeat_with_no_instrument_preserves_carried():
    glowharp = Instrument(name="glowharp",
                           capabilities=frozenset({"light.pixels"}))
    server = GameServer({}, carried_instruments={"glowharp": glowharp})
    server.hello("ie1", "Shroom One", "1", instrument="glowharp")
    server.hello("ie1", "Shroom One", "1.1")   # heartbeat re-hello
    assert server.devices.get("ie1").carried is glowharp


def test_carried_instruments_merges_defaults_with_config_catalog():
    glowharp = Instrument(name="glowharp",
                           capabilities=frozenset({"light.pixels"}))
    server = GameServer({}, carried_instruments={"glowharp": glowharp})
    assert server.carried_instruments["tuneshroom"] is TUNESHROOM
    assert server.carried_instruments["defaultshroom"] is DEFAULTSHROOM
    assert server.carried_instruments["glowharp"] is glowharp


# --- warning dedup: unknown/non-carriable declared name --------------------


class _Observer:
    def __init__(self):
        self.warnings = []

    def on_device_warning(self, message):
        self.warnings.append(message)


def test_hello_repeated_same_unknown_name_warns_once():
    server = GameServer({})
    obs = _Observer()
    server.add_observer(obs)
    server.hello("ie1", "Shroom One", "1", instrument="nope")
    server.hello("ie1", "Shroom One", "1.1", instrument="nope")
    server.hello("ie1", "Shroom One", "1.2", instrument="nope")
    assert len(obs.warnings) == 1


def test_hello_different_unknown_name_warns_again():
    server = GameServer({})
    obs = _Observer()
    server.add_observer(obs)
    server.hello("ie1", "Shroom One", "1", instrument="nope")
    server.hello("ie1", "Shroom One", "1.1", instrument="other-nope")
    assert len(obs.warnings) == 2


def test_hello_unknown_then_resolved_then_same_unknown_warns_again():
    # Simulate a catalog entry that starts absent (unresolved -- warns),
    # gets added (resolves -- clears the dedup entry), then disappears
    # again (regresses to the same unresolved name -- must warn again).
    server = GameServer({})
    obs = _Observer()
    server.add_observer(obs)
    server.hello("ie1", "Shroom One", "1", instrument="nope")
    assert len(obs.warnings) == 1
    server.carried_instruments["nope"] = Instrument(
        name="nope", capabilities=frozenset({"light.pixels"}))
    server.hello("ie1", "Shroom One", "1.1", instrument="nope")
    assert server.devices.get("ie1").carried.name == "nope"
    del server.carried_instruments["nope"]
    server.hello("ie1", "Shroom One", "1.2", instrument="nope")
    assert len(obs.warnings) == 2


def test_reap_stale_clears_warned_instrument_dedup_for_removed_dev():
    server = GameServer({})
    obs = _Observer()
    server.add_observer(obs)
    server.hello("ie1", "Shroom One", "1", instrument="nope")
    assert len(obs.warnings) == 1
    server.reap_stale(timeout=0.0)
    server.hello("ie1", "Shroom One", "1.1", instrument="nope")
    assert len(obs.warnings) == 2


# --- non-carriable resolved instrument (Finding 2) --------------------------


def test_hello_with_fixture_only_instrument_falls_back_to_defaultshroom_and_warns():
    venue_array = Instrument(name="venue_array",
                              capabilities=frozenset({"light.surface"}))
    server = GameServer({}, carried_instruments={"venue_array": venue_array})
    obs = _Observer()
    server.add_observer(obs)
    server.hello("ie1", "Shroom One", "1", instrument="venue_array")
    assert server.devices.get("ie1").carried is DEFAULTSHROOM
    assert len(obs.warnings) == 1
    assert "ie1" in obs.warnings[0]
    assert "venue_array" in obs.warnings[0]
    assert "defaultshroom" in obs.warnings[0]


def test_hello_with_fixture_only_instrument_warns_once_across_heartbeats():
    venue_array = Instrument(name="venue_array",
                              capabilities=frozenset({"light.surface"}))
    server = GameServer({}, carried_instruments={"venue_array": venue_array})
    obs = _Observer()
    server.add_observer(obs)
    server.hello("ie1", "Shroom One", "1", instrument="venue_array")
    server.hello("ie1", "Shroom One", "1.1", instrument="venue_array")
    assert len(obs.warnings) == 1


def test_hello_with_pixels_config_instrument_still_resolves():
    glowharp = Instrument(name="glowharp",
                           capabilities=frozenset({"light.pixels"}))
    server = GameServer({}, carried_instruments={"glowharp": glowharp})
    obs = _Observer()
    server.add_observer(obs)
    server.hello("ie1", "Shroom One", "1", instrument="glowharp")
    assert server.devices.get("ie1").carried is glowharp
    assert obs.warnings == []
