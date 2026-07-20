import pytest

from bits.test_bit import TestBit
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.state import State


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
