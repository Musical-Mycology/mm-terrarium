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
