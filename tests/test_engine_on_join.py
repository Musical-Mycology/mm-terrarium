"""tests/test_engine_on_join.py"""
from control.engine import GameServer, State
from bits.test.test_bit import TestBit


class _JoinRecorder(TestBit):
    def __init__(self):
        super().__init__()
        self.joins = []

    def on_join(self, dev, role_name):
        self.joins.append((dev, role_name))


class _RaisingJoin(_JoinRecorder):
    def on_join(self, dev, role_name):
        super().on_join(dev, role_name)
        raise RuntimeError("bit bug")


def _setup(bit_cls):
    gs = GameServer({"TestBit": bit_cls})
    gs.load_bit("TestBit")
    return gs


def test_on_join_called_with_dev_and_role_name():
    gs = _setup(_JoinRecorder)
    result = gs.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted
    assert gs.bit.joins == [("ie1", "player")]


def test_on_join_not_called_on_denied_join():
    gs = _setup(_JoinRecorder)
    gs.run()
    result = gs.join("ie1", "TEST_PLAYER_NODE")   # scored role, RUNNING
    assert not result.granted
    assert gs.bit.joins == []


def test_raising_on_join_does_not_break_join():
    gs = _setup(_RaisingJoin)
    result = gs.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted                          # grant survives the raise
    assert gs.bit.joins == [("ie1", "player")]
