"""GameServer.data: routing /game/<verb> to a Bit's verb_handlers."""

import pytest

from bits.test_bit import TestBit
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


@pytest.fixture
def running_server():
    # NOTE: joins before run(), not after as in the task brief's literal
    # listing. TestBit's "player" role is scored (bits/test_bit.py), and
    # RegistrationState.join() refuses scored roles once state == RUNNING
    # (control/registration.py) -- joining after run() left `dev` unregistered
    # and every cue-partition test below failed with "device not registered"
    # instead of exercising data(). Joining during SETUP, then calling run(),
    # satisfies the fixture's documented intent (RUNNING with one registered
    # device) using the same dev/node names the brief specifies.
    gs = GameServer({"test_bit": TestBit})
    gs.load_bit("test_bit")
    dev = "ie1"
    gs.hello(dev, "fake", "1")
    gs.join(dev, "TEST_PLAYER_NODE")
    gs.run()
    return gs, dev


def test_play_cue_reaches_on_play_cue(running_server):
    """A handler returning a PlayCue routes to on_play_cue, not on_light_cue."""
    from control.cues import PlayCue
    gs, dev = running_server
    plays, lights = [], []
    gs.on_play_cue = lambda *a: plays.append(a)
    gs.on_light_cue = lambda *a: lights.append(a)
    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args: [PlayCue(d, "click", "hard")]}

    assert gs.data(dev, "boop", [dev]) is None
    assert plays == [(dev, "click", "hard")]
    assert lights == []


def test_mixed_cues_are_partitioned(running_server):
    from control.cues import PlayCue
    gs, dev = running_server
    plays, lights = [], []
    gs.on_play_cue = lambda *a: plays.append(a)
    gs.on_light_cue = lambda *a: lights.append(a)
    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args: [(d, 0xB0, 74, 64), PlayCue(d, "chime", "")]}

    gs.data(dev, "boop", [dev])
    assert lights == [(dev, 0xB0, 74, 64)]
    assert plays == [(dev, "chime", "")]


def test_tuple_only_handler_is_unchanged(running_server):
    """Regression: Bits that predate PlayCue must behave exactly as before."""
    gs, dev = running_server
    lights = []
    gs.on_light_cue = lambda *a: lights.append(a)
    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args: [(d, 0xB0, 11, 20), (d, 0xB0, 11, 30)]}

    gs.data(dev, "boop", [dev])
    assert lights == [(dev, 0xB0, 11, 20), (dev, 0xB0, 11, 30)]


def test_play_cue_with_no_sink_is_dropped(running_server):
    """on_play_cue unset must not raise -- a Bit must never wedge Control."""
    from control.cues import PlayCue
    gs, dev = running_server
    gs.on_play_cue = None
    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args: [PlayCue(d, "click", "")]}

    assert gs.data(dev, "boop", [dev]) is None


def test_raising_play_sink_does_not_propagate(running_server):
    from control.cues import PlayCue
    gs, dev = running_server

    def boom(*_a):
        raise RuntimeError("sink exploded")

    gs.on_play_cue = boom
    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args: [PlayCue(d, "click", "")]}

    assert gs.data(dev, "boop", [dev]) is None
