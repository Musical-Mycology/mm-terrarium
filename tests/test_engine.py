from types import SimpleNamespace

import pytest

from bits.test_bit import TestBit
from control.bit import Bit
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.roles import Role, RoleClass, RoleTable
from control.state import State


def test_add_observer_notifies_multiple_observers_of_state_changes():
    from types import SimpleNamespace
    from bits.test_bit import TestBit
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
    from bits.test_bit import TestBit
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
    from bits.test_bit import TestBit
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


def test_full_lifecycle_reaches_idle_and_releases_devices():
    server = make_server()
    released = []
    server.on_release = released.append

    server.hello("ie1", "Tuneshroom 1", "1.0")
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
    server.hello("ie1", "Tuneshroom 1", "1.0")
    server.load_bit("test_bit")
    server.join("ie1", "TEST_PLAYER_NODE")

    server.abort()

    assert server.state == State.IDLE
    assert server.bit is None
    assert released == ["ie1"]


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
    assert TestBit().version == ""


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
