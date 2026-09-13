"""control/prepare.py: the LAN prepare rule (spec section 4.2)."""
import threading

from control.prepare import (PrepareDecision, PrepareReply, PrepareRequest,
                             PrepareRequested, decide_prepare)
from control.state import State


def _decide(**over):
    base = dict(room_ready=True, state=State.IDLE, loaded_bit=None,
                bit="MetronomeBit", known=True, when="admin",
                expected_key="metro-dev", key="metro-dev")
    base.update(over)
    return decide_prepare(**base)


def test_no_room_is_a_visible_refusal_before_anything_else():
    d = _decide(room_ready=False, key="wrong")
    assert d == PrepareDecision(False, "no room loaded", "none", True)


def test_unknown_bit_is_a_silent_bad_key():
    d = _decide(known=False, when=None, expected_key=None)
    assert d == PrepareDecision(False, "bad key", "none", False)


def test_non_admin_start_condition_is_a_silent_bad_key():
    assert _decide(when="players").visible is False
    assert _decide(when="players").reason == "bad key"


def test_missing_or_wrong_key_is_silent():
    assert _decide(expected_key=None) == PrepareDecision(False, "bad key", "none", False)
    assert _decide(key=None) == PrepareDecision(False, "bad key", "none", False)
    assert _decide(key="nope") == PrepareDecision(False, "bad key", "none", False)


def test_idle_loads():
    assert _decide() == PrepareDecision(True, None, "load", True)


def test_setup_with_the_same_bit_is_a_noop():
    d = _decide(state=State.SETUP, loaded_bit="MetronomeBit")
    assert d == PrepareDecision(True, None, "noop", True)


def test_setup_with_a_different_bit_is_busy():
    d = _decide(state=State.SETUP, loaded_bit="TestBit")
    assert d == PrepareDecision(False, "busy", "none", True)


def test_every_other_state_is_busy():
    for state in (State.LOADING, State.LOADED, State.RUNNING,
                  State.COMPLETING, State.UNLOADING):
        d = _decide(state=state, loaded_bit="MetronomeBit")
        assert d == PrepareDecision(False, "busy", "none", True), state


def test_bad_key_is_checked_before_busy():
    d = _decide(state=State.RUNNING, loaded_bit="MetronomeBit", key="nope")
    assert d.visible is False


def test_request_repr_never_carries_the_key():
    req = PrepareRequest("secret-key", "MetronomeBit", "gem-1", "web:gem-1",
                         PrepareReply(threading.Event()))
    assert "secret-key" not in repr(req)
    assert "secret-key" not in str(req)


def test_requested_record_shape():
    rec = PrepareRequested("web:gem-1", "gem-1", "MetronomeBit", False, "busy")
    assert rec.bit == "MetronomeBit" and rec.reason == "busy"


def test_game_server_notifies_prepare_observers():
    from control.engine import GameServer
    from bits.test.test_bit import TestBit
    gs = GameServer({"test_bit": TestBit})
    seen = []

    class Obs:
        def on_prepare_requested(self, record):
            seen.append(record)

    gs.add_observer(Obs())
    rec = PrepareRequested("web:terrarium", "terrarium", "test_bit", True, None)
    gs.notify_prepare_requested(rec)
    assert seen == [rec]
