"""GameServer.data: routing /game/<verb> to a Bit's verb_handlers."""

import pytest

from control.engine import GameServer
from control.roles import Role, RoleClass, RoleTable
from control.bit import Bit


class VerbBit(Bit):
    version = "0.1"

    def __init__(self):
        self.seen = []
        self.raise_next = False
        self.refuse_next = None      # set to a str to exercise 4.0

    @property
    def role_table(self) -> RoleTable:
        player = Role(name="player", role_class=RoleClass.SHARED,
                      capacity=None, scored=False)
        return RoleTable(roles={"player": player},
                         node_map={"NODE_A": ["player"]})

    def update(self, dt: float) -> bool:
        return False

    def verb_handlers(self) -> dict:
        return {"tilt": self._on_tilt}

    def _on_tilt(self, dev, args):
        if self.raise_next:
            raise RuntimeError("boom")
        if self.refuse_next is not None:
            return self.refuse_next
        self.seen.append((dev, args))
        return [(dev, 0xB0, 74, 64)]


class NoVerbBit(Bit):
    """A Bit that genuinely declares no extra verbs -- unlike TestBit, which
    gained a `tilt` handler once verb dispatch became a tested behavior
    (Slice 2). Uses Bit's default verb_handlers() -> {}."""

    version = "0.1"

    @property
    def role_table(self) -> RoleTable:
        player = Role(name="player", role_class=RoleClass.SHARED,
                      capacity=None, scored=False)
        return RoleTable(roles={"player": player},
                         node_map={"NODE_A": ["player"]})

    def update(self, dt: float) -> bool:
        return False


def _loaded_server():
    gs = GameServer({"verb_bit": VerbBit})
    gs.load_bit("verb_bit")
    return gs


def test_data_routes_to_handler_and_emits_cue():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)

    assert gs.data("ie1", "tilt", ["ie1", 30.0]) is None
    assert gs.bit.seen == [("ie1", ["ie1", 30.0])]
    assert cues == [("ie1", 0xB0, 74, 64)]


def test_unregistered_device_is_refused():
    gs = _loaded_server()
    assert gs.data("ie9", "tilt", ["ie9", 0.0]) == "device not registered"


def test_unknown_verb_is_refused():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    assert gs.data("ie1", "wiggle", ["ie1"]) == "unknown verb 'wiggle'"


def test_no_bit_loaded_is_refused():
    gs = GameServer({"verb_bit": VerbBit})
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "no Bit running"


def test_raising_handler_is_contained():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    gs.bit.raise_next = True
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "handler error"
    assert gs.state.name == "SETUP"   # engine unharmed


def test_bit_declaring_no_verbs_is_unaffected():
    gs = GameServer({"no_verb_bit": NoVerbBit})
    gs.load_bit("no_verb_bit")
    gs.join("ie1", "NODE_A")
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "unknown verb 'tilt'"


def test_handler_returning_a_string_is_a_refusal():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)
    gs.bit.refuse_next = "no open capture for 'shake-021'"

    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "no open capture for 'shake-021'"
    # The refusal must NOT be walked character by character as cues.
    assert cues == []
    assert gs.state.name == "SETUP"


def test_an_empty_refusal_still_carries_a_reason():
    """A device must never receive /<dev>/error with a blank reason: an
    empty string is a Bit bug, and a blank error frame hides it."""
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    gs.bit.refuse_next = ""
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "handler refused"


def test_returning_cues_still_works():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)
    assert gs.data("ie1", "tilt", ["ie1", 30.0]) is None
    assert cues == [("ie1", 0xB0, 74, 64)]
