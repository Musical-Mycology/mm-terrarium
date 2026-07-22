from types import SimpleNamespace

import pytest

from bits.test_bit import TestBit
from control.engine import BitLoadError, GameServer, InvalidTransition
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


REGISTRY = {
    "test_bit": TestBit,
    "exploding_complete_bit": ExplodingCompleteBit,
    "exploding_unload_bit": ExplodingUnloadBit,
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
