"""GameServer.data: routing /game/<verb> to a Bit's verb_handlers."""

import time

import pytest

from bits.test_bit import TestBit
from control.cues import LightCue
from control.engine import GameServer
from control.roles import Role, RoleClass, RoleTable
from control.bit import Bit


def _is_recent_monotonic(t: float) -> bool:
    """True when `t` looks like a value GameServer's default clock (real
    time.monotonic, unlike the fixed clocks _loaded_server/_joined's peers
    use) would have just produced. `at` is no longer None for an untimed
    cue -- see test_untimed_cue_is_stamped_with_at for the fixed-clock,
    exact-value version of this same assertion."""
    return isinstance(t, float) and abs(t - time.monotonic()) < 5.0


class VerbBit(Bit):
    version = "0.1"

    def __init__(self):
        self.seen = []
        self.seen_at = []
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

    next_cue = None            # set by a test to override the default cue
    next_cues = None           # set by a test to return a whole cue list

    def _on_tilt(self, dev, args, at):
        if self.raise_next:
            raise RuntimeError("boom")
        if self.refuse_next is not None:
            return self.refuse_next
        self.seen.append((dev, args))
        self.seen_at.append(at)
        if self.next_cues is not None:
            return self.next_cues
        if self.next_cue is not None:
            return [self.next_cue]
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


def test_plain_tuple_cue_carries_no_time():
    """A Bit returning the historic 4-tuple still works. It used to mean
    when=None, "apply on arrival"; it now means "apply at the computed at",
    so the sink sees a real timestamp rather than None."""
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)

    assert gs.data("ie1", "tilt", ["ie1", 30.0]) is None
    assert len(cues) == 1
    assert cues[0][:4] == ("ie1", 0xB0, 74, 64)
    assert _is_recent_monotonic(cues[0][4])


def test_light_cue_carries_its_time():
    """A Bit opting into timing returns LightCue, and `when` reaches the
    sink unchanged."""
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)
    gs.bit.next_cue = LightCue("ie1", 0xB0, 74, 99, when=1234.5)

    assert gs.data("ie1", "tilt", ["ie1", 0.0]) is None
    assert cues == [("ie1", 0xB0, 74, 99, 1234.5)]


def test_a_malformed_cue_does_not_escape_data():
    """data() promises it never raises: a Bit must not be able to wedge
    Control. A 3-element cue is a Bit bug, and it must be contained the
    same way a raising sink already is."""
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)
    gs.bit.next_cue = ("ie1", 0xB0, 74)        # 3 elements, not 4

    assert gs.data("ie1", "tilt", ["ie1", 0.0]) is None
    assert cues == []


def test_one_malformed_cue_does_not_stop_the_others():
    """Containment is per-cue, not per-batch: a later good cue in the same
    list must still reach its sink."""
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)
    gs.bit.next_cues = [("ie1", 0xB0, 74), ("ie1", 0xB0, 74, 99)]

    assert gs.data("ie1", "tilt", ["ie1", 0.0]) is None
    assert len(cues) == 1
    assert cues[0][:4] == ("ie1", 0xB0, 74, 99)
    assert _is_recent_monotonic(cues[0][4])


def test_data_routes_to_handler_and_emits_cue():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)

    assert gs.data("ie1", "tilt", ["ie1", 30.0]) is None
    assert gs.bit.seen == [("ie1", ["ie1", 30.0])]
    assert len(cues) == 1
    assert cues[0][:4] == ("ie1", 0xB0, 74, 64)
    assert _is_recent_monotonic(cues[0][4])


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
        "boop": lambda d, args, at: [PlayCue(d, "click", "hard")]}

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
        "boop": lambda d, args, at: [(d, 0xB0, 74, 64), PlayCue(d, "chime", "")]}

    gs.data(dev, "boop", [dev])
    assert len(lights) == 1
    assert lights[0][:4] == (dev, 0xB0, 74, 64)
    assert _is_recent_monotonic(lights[0][4])
    assert plays == [(dev, "chime", "")]


def test_tuple_only_handler_is_unchanged(running_server):
    """Regression: Bits that predate PlayCue must behave exactly as before."""
    gs, dev = running_server
    lights = []
    gs.on_light_cue = lambda *a: lights.append(a)
    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args, at: [(d, 0xB0, 11, 20), (d, 0xB0, 11, 30)]}

    gs.data(dev, "boop", [dev])
    assert len(lights) == 2
    assert lights[0][:4] == (dev, 0xB0, 11, 20)
    assert lights[1][:4] == (dev, 0xB0, 11, 30)
    assert _is_recent_monotonic(lights[0][4])
    assert _is_recent_monotonic(lights[1][4])


def test_play_cue_with_no_sink_is_dropped(running_server):
    """on_play_cue unset must not raise -- a Bit must never wedge Control."""
    from control.cues import PlayCue
    gs, dev = running_server
    gs.on_play_cue = None
    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args, at: [PlayCue(d, "click", "")]}

    assert gs.data(dev, "boop", [dev]) is None


def test_raising_play_sink_does_not_propagate(running_server):
    from control.cues import PlayCue
    gs, dev = running_server

    def boom(*_a):
        raise RuntimeError("sink exploded")

    gs.on_play_cue = boom
    gs.bit.verb_handlers = lambda: {
        "boop": lambda d, args, at: [PlayCue(d, "click", "")]}

    assert gs.data(dev, "boop", [dev]) is None


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
    assert len(cues) == 1
    assert cues[0][:4] == ("ie1", 0xB0, 74, 64)
    assert _is_recent_monotonic(cues[0][4])


def _joined(bit, cue_horizon=0.06, clock=lambda: 1000.0):
    gs = GameServer({"vb": lambda: bit}, cue_horizon=cue_horizon, clock=clock)
    gs.load_bit("vb")
    gs.join("ie1", "NODE_A")
    return gs


def test_handler_receives_at_computed_from_the_device_stamp():
    """T = gesture_time + cue_horizon, and the DEVICE's reading of the clock
    is the origin -- Design Rule 4, timestamps at the source. Jitter on the
    way up must not become jitter in the output."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0], gesture_time=999.5)
    assert bit.seen_at == [pytest.approx(999.56)]


def test_unstamped_gesture_falls_back_to_controls_clock():
    """The websocket transport never stamps: devicelink/protocol.py's _event
    defaults timestamp=0.0. That path must still produce a usable `at`."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0], gesture_time=0.0)
    assert bit.seen_at == [pytest.approx(1000.06)]


def test_negative_stamp_falls_back_to_controls_clock():
    """o2lite.time_get() returns -1 before clock sync. A cue scheduled
    against -1 is garbage."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0], gesture_time=-1.0)
    assert bit.seen_at == [pytest.approx(1000.06)]


def test_implausibly_future_stamp_is_refused_and_counted():
    """A device whose clock is wrong could otherwise park a cue hours out and
    hold a queue entry through teardown."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0], gesture_time=99999.0)
    assert bit.seen_at == [pytest.approx(1000.06)]
    assert gs.rejected_stamps == 1


def test_no_gesture_time_argument_still_works():
    """Callers that predate timing (harness drivers, console-driven calls)
    must keep working; they get Control's clock as the origin."""
    bit = VerbBit()
    gs = _joined(bit)
    gs.data("ie1", "tilt", ["ie1", 10.0])
    assert bit.seen_at == [pytest.approx(1000.06)]


def _room_bound(bit, cue_horizon=0.06, clock=lambda: 1000.0, bound="sim-room"):
    from control.rooms import Room, RoomType
    gs = GameServer({"vb": lambda: bit}, cue_horizon=cue_horizon, clock=clock)
    gs.room = Room(room_type=RoomType.TEST)
    gs.room.bound = {"main": bound}
    gs.load_bit("vb")
    gs.join("ie1", "NODE_A")
    return gs


def test_room_target_resolves_to_the_bound_dev():
    """A Bit names the Room by a constant, never by the runtime id an
    admin-armed tap happened to bind -- that is what keeps a Bit
    offline-testable while still being able to drive the Room."""
    from control.cues import ROOM
    bit = VerbBit()
    bit.next_cues = [(ROOM, 0xB0, 74, 99)]
    gs = _room_bound(bit)
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    gs.data("ie1", "tilt", ["ie1", 0.0], gesture_time=999.5)
    assert seen == [("sim-room", 0xB0, 74, 99, pytest.approx(999.56))]


def test_room_target_with_no_room_bound_is_dropped_not_raised():
    from control.cues import ROOM
    bit = VerbBit()
    bit.next_cues = [(ROOM, 0xB0, 74, 99)]
    gs = _joined(bit)                      # no gs.room at all
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) is None
    assert seen == []


def test_untimed_cue_is_stamped_with_at():
    """A plain 4-tuple used to mean 'apply on arrival'. It now means 'apply
    at the time Control computed for this gesture', which is what makes one
    gesture produce one shared T without every Bit remembering to say so."""
    bit = VerbBit()
    gs = _joined(bit)
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    gs.data("ie1", "tilt", ["ie1", 0.0], gesture_time=999.5)
    assert seen == [("ie1", 0xB0, 74, 64, pytest.approx(999.56))]


def test_explicit_light_cue_time_wins_over_at():
    """A Bit that names its own time is expressing a derived offset (an echo
    at at+0.5); Control must not overwrite it."""
    bit = VerbBit()
    bit.next_cues = [LightCue("ie1", 0xB0, 74, 5, when=12345.0)]
    gs = _joined(bit)
    seen = []
    gs.on_light_cue = lambda *a: seen.append(a)
    gs.data("ie1", "tilt", ["ie1", 0.0], gesture_time=999.5)
    assert seen == [("ie1", 0xB0, 74, 5, 12345.0)]


def test_play_cue_can_target_the_room_too():
    from control.cues import PlayCue, ROOM
    bit = VerbBit()
    bit.next_cues = [PlayCue(ROOM, "click", "")]
    gs = _room_bound(bit)
    seen = []
    gs.on_play_cue = lambda *a: seen.append(a)
    gs.data("ie1", "tilt", ["ie1", 0.0])
    assert seen == [("sim-room", "click", "")]
