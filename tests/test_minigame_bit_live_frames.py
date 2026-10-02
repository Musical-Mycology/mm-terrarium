"""MinigameBit's INGAME rainbow, rendered the way a live run renders it.

test_minigame_bit.py asserts on the cues the Bit reports. This asserts on
the bytes the player device displays: a real GameServer, a real
DeviceLinkAgent and a real luxaeterna LightSession, ticked at 44 Hz on a
hand-advanced clock (the harness pattern of
tests/test_metronome_bit_live_frames.py).
"""

import pytest

pytest.importorskip("luxaeterna")

from bits.minigame.minigame_bit import (
    BLINK_COUNT,
    BLINK_INTERVAL_S,
    MINIGAME_PLAYER_NODE,
    MinigameBit,
)
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.agent import DeviceLinkAgent
from devicelink.contract import HELLO_INTERVAL_S

from tests.test_devicelink_agent import FakeServer, _Clock

TEST_PROFILE = load_terrarium_config("terrarium.toml").rooms["TEST"].profile

HORIZON = 0.060
TICK = 1.0 / 44.0
TAP = ["ie1", 0.0, 80.0, 1]


class _Rig:
    """A running MinigameBit with ie1 joined as the player."""

    def __init__(self):
        self.clk = _Clock(100.0)
        self.gs = GameServer({"MinigameBit": MinigameBit},
                             room_binding=RoomBindingRegistry(),
                             cue_horizon=HORIZON, clock=self.clk)
        self.gs.room = Room(name="TEST", profile=TEST_PROFILE,
                            node_id="ROOM_TEST_NODE")
        self.gs.load_bit("MinigameBit")
        self.server = FakeServer()
        self.agent = DeviceLinkAgent(self.gs, self.server, clock=self.clk,
                                     horizon=HORIZON)
        self.server.arrive("c1")
        self.server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
        self.agent.poll()
        self.server.deliver("c1", "/game/join", "ss",
                            ["ie1", MINIGAME_PLAYER_NODE])
        self.agent.poll()
        self.gs.run()
        self._seen = 0
        self._last_hello = self.clk()
        self.frames = []           # [(when, frame)] for ie1, in send order

    def run(self, seconds):
        for _ in range(int(round(seconds / TICK))):
            self.clk.advance(TICK)
            if self.clk() - self._last_hello >= HELLO_INTERVAL_S:
                # A real device's heartbeat; without it Control reaps the
                # silent device after 15 s, mid-round.
                self.server.deliver("c1", "/game/hello", "sss",
                                    ["ie1", "sim", "1"])
                self._last_hello = self.clk()
            self.agent.poll()
            self.gs.tick(TICK)
            for _dev, msg in self.server.sent[self._seen:]:
                if msg["address"] == "/ie1/leds":
                    self.frames.append((msg["timestamp"],
                                        bytes(msg["args"][0])))
            self._seen = len(self.server.sent)

    def tap(self):
        self.server.deliver("c1", "/game/tap", "sffi", TAP,
                            timestamp=self.clk())
        self.agent.poll()

    def shown_at(self, t):
        """The frame on the ring at time `t`: the newest `when` <= t."""
        due = [f for when, f in self.frames if when <= t]
        return due[-1] if due else None


def _pixels(frame):
    return [tuple(frame[i:i + 3]) for i in range(0, len(frame), 3)]


def _is_rainbow(frame):
    """Lit, and not one colour: a rainbow's pixels differ around the ring."""
    px = _pixels(frame)
    return sum(frame) > 0 and len(set(px)) >= 6


def test_ring_is_dark_in_pending_after_the_role_signature():
    rig = _Rig()
    rig.run(3.0)                   # clear luxaeterna's ~1.5 s role signature
    assert sum(rig.shown_at(rig.clk())) == 0


def test_ingame_shows_a_scrolling_rainbow_between_blinks():
    rig = _Rig()
    rig.run(3.0)
    rig.tap()                      # PENDING -> INGAME
    t0 = rig.clk() + HORIZON
    rig.run(4.0)
    # Mid-gap between blink 0 (t0..t0+0.5) and blink 1 (t0+2.0).
    a, b = rig.shown_at(t0 + 1.0), rig.shown_at(t0 + 1.5)
    assert _is_rainbow(a), _pixels(a)
    assert _is_rainbow(b), _pixels(b)
    assert a != b, "the rainbow did not move"


def test_blinks_still_flash_white_over_the_rainbow():
    rig = _Rig()
    rig.run(3.0)
    rig.tap()
    t0 = rig.clk() + HORIZON
    rig.run(3.0)
    blink = rig.shown_at(t0 + 2.0 + 0.25)      # inside blink 1
    assert len(set(_pixels(blink))) == 1 and sum(blink) > 0


def test_rainbow_goes_dark_when_the_round_ends():
    rig = _Rig()
    rig.run(3.0)
    rig.tap()
    rig.run(BLINK_COUNT * BLINK_INTERVAL_S + 2.0)
    assert rig.gs.bit.status()["phase"] == "END"
    assert sum(rig.shown_at(rig.clk())) == 0


def test_a_reset_tap_darkens_the_rainbow():
    rig = _Rig()
    rig.run(3.0)
    rig.tap()
    rig.run(1.0)
    assert _is_rainbow(rig.shown_at(rig.clk()))
    rig.tap()                      # INGAME -> PENDING
    rig.run(1.0)
    assert rig.gs.bit.status()["phase"] == "PENDING"
    assert sum(rig.shown_at(rig.clk())) == 0
