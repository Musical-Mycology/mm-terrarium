"""tests/test_engine_on_join.py"""
from control.engine import GameServer, State
from control.lobby import TERRARIUM_ADMIN
from bits.test.test_bit import TestBit
from tests.helpers_admit import admit


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


def test_on_join_called_with_dev_and_role_name_at_start():
    gs = _setup(_JoinRecorder)
    result = admit(gs, "ie1", "TEST_PLAYER_NODE")
    assert result.granted
    # Spec 3.6: a handshake only validates; on_join fires at request_start.
    assert gs.bit.joins == []
    gs.request_start(None, TERRARIUM_ADMIN, "test")
    assert gs.bit.joins == [("ie1", "player")]


def test_on_join_fires_in_validation_order_scored_then_jam():
    gs = _setup(_JoinRecorder)
    gs.hello("walker", "", "", None)          # never handshakes: a jammer
    assert admit(gs, "ie1", "TEST_PLAYER_NODE").granted
    gs.request_start(None, TERRARIUM_ADMIN, "test")
    # Spec 3.6 step 3: scored first, then jam, regardless of hello order.
    assert gs.bit.joins == [("ie1", "player"), ("walker", "jammer")]


def test_on_join_not_called_on_denied_handshake():
    gs = _setup(_JoinRecorder)
    gs.run()
    result = admit(gs, "ie1", "TEST_PLAYER_NODE")   # scored role, RUNNING
    assert not result.granted
    assert result.reason == "registration closed"
    # Spec 3.6: the RUNNING walk-up is a jam grant, so on_join fires for the
    # jam role only, never for the refused scored role.
    assert gs.bit.joins == [("ie1", "jammer")]


def test_raising_on_join_does_not_break_start():
    gs = _setup(_RaisingJoin)
    assert admit(gs, "ie1", "TEST_PLAYER_NODE").granted
    gs.request_start(None, TERRARIUM_ADMIN, "test")
    assert gs.state is State.RUNNING               # start survives the raise
    assert gs.bit.joins == [("ie1", "player")]
    assert "ie1" in gs.registration.assignments
