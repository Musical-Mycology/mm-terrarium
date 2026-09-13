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


from pathlib import Path

from control.bit_registry import BitRegistry
from control.engine import GameServer
from control.prepare import PrepareAuthority
from control.terrarium import TerrariumState

PREP_MANIFEST = """
[bit]
name = "PrepBit"
entry = "prep_bit:PrepBit"
requires_terrarium_api = 1
[start]
when = "admin"
key = "k"
min_scored = 0
"""

PLAYERS_MANIFEST = PREP_MANIFEST.replace('name = "PrepBit"', 'name = "PlayersBit"') \
    .replace('entry = "prep_bit:PrepBit"', 'entry = "prep_bit:PlayersBit"') \
    .replace('when = "admin"', 'when = "players"')

DISABLED_MANIFEST = PREP_MANIFEST.replace('name = "PrepBit"', 'name = "OffBit"') \
    .replace('entry = "prep_bit:PrepBit"', 'entry = "prep_bit:OffBit"') \
    .replace("[bit]\n", "[bit]\nenabled = false\n")

MODULE = ("from bits.test.test_bit import TestBit\n"
          "class PrepBit(TestBit):\n    pass\n"
          "class PlayersBit(TestBit):\n    pass\n"
          "class OffBit(TestBit):\n    pass\n")


def _pkg(root, dirname, manifest):
    d = root / dirname
    d.mkdir(parents=True)
    (d / "bit.toml").write_text(manifest)
    (d / "__init__.py").write_text("")
    (d / "prep_bit.py").write_text(MODULE)


class _Terrarium:
    def __init__(self, state=TerrariumState.ROOM_READY):
        self.state = state


class _Recorder:
    def __init__(self):
        self.records = []

    def on_prepare_requested(self, record):
        self.records.append(record)


def _rig(tmp_path, terrarium=None):
    _pkg(tmp_path, "prep", PREP_MANIFEST)
    _pkg(tmp_path, "players", PLAYERS_MANIFEST)
    _pkg(tmp_path, "off", DISABLED_MANIFEST)
    registry = BitRegistry.scan([tmp_path])
    gs = GameServer(registry.lazy_class_map())
    rec = _Recorder()
    gs.add_observer(rec)
    auth = PrepareAuthority(gs, registry,
                            terrarium if terrarium is not None else _Terrarium())
    return gs, auth, rec


def _req(bit="PrepBit", key="k", dev="gem-1"):
    return PrepareRequest(key, bit, dev, f"web:{dev}", PrepareReply(threading.Event()))


def test_authority_loads_into_idle_with_the_resolved_config(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    d = auth.request(_req())
    assert d.accepted and d.action == "load"
    assert gs.state is State.SETUP and gs.bit_name == "PrepBit"
    assert gs.bit.config.start.key == "k"
    assert rec.records[-1] == PrepareRequested("web:gem-1", "gem-1", "PrepBit", True, None)


def test_authority_noops_on_the_same_bit_in_setup(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    auth.request(_req())
    bit_before = gs.bit
    d = auth.request(_req())
    assert d.accepted and d.action == "noop"
    assert gs.bit is bit_before


def test_authority_reports_busy_for_a_different_bit_in_setup(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    auth.request(_req())
    d = auth.request(_req(bit="PlayersBit", key="k"))
    # PlayersBit's start is "players", so this is a silent bad key, not busy
    assert d.visible is False
    gs2, auth2, rec2 = _rig(tmp_path / "two")
    auth2.request(_req())
    gs2.bit_name = "Other"          # a different Bit occupies SETUP
    d2 = auth2.request(_req())
    assert d2 == PrepareDecision(False, "busy", "none", True)


def test_authority_never_loads_on_a_bad_key_and_still_records(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    d = auth.request(_req(key="wrong"))
    assert d.visible is False and gs.state is State.IDLE
    assert rec.records[-1].accepted is False and rec.records[-1].reason == "bad key"
    assert "wrong" not in repr(rec.records[-1])


def test_authority_treats_an_unknown_bit_as_a_bad_key(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    d = auth.request(_req(bit="NoSuchBit"))
    assert d == PrepareDecision(False, "bad key", "none", False)
    assert gs.state is State.IDLE


def test_authority_refuses_without_a_room(tmp_path):
    gs, auth, rec = _rig(tmp_path, terrarium=_Terrarium(TerrariumState.NO_ROOM))
    d = auth.request(_req())
    assert d == PrepareDecision(False, "no room loaded", "none", True)
    assert gs.state is State.IDLE


def test_authority_without_a_terrarium_treats_the_room_as_ready(tmp_path):
    _pkg(tmp_path, "prep", PREP_MANIFEST)
    registry = BitRegistry.scan([tmp_path])
    gs = GameServer(registry.lazy_class_map())
    auth = PrepareAuthority(gs, registry)
    assert auth.request(_req()).accepted


def test_authority_turns_a_disabled_package_into_a_visible_reason(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    d = auth.request(_req(bit="OffBit"))
    assert d.accepted is False and d.visible is True
    assert "disabled" in d.reason
    assert gs.state is State.IDLE
