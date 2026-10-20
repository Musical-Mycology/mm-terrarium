"""MinigameBit's progress bar, rendered the way a live run renders it.

test_minigame_bit.py asserts on the cues the Bit reports. This asserts on
the bytes a Tower displays: a real GameServer, a real DeviceLinkAgent and
real luxaeterna LightSessions, ticked at 44 Hz on a hand-advanced clock (the
harness pattern of tests/test_metronome_bit_live_frames.py). The Tower is a
plain devtower<N> device holding the tower role, so its frame is the 12 px
player frame with the bar on pixels 0..3.
"""

import pytest

pytest.importorskip("luxaeterna")

from bits.minigame.minigame_bit import MinigameBit
from control.engine import GameServer
from devicelink.contract import HELLO_INTERVAL_S
from devicelink.agent import DeviceLinkAgent

from tests.test_devicelink_agent import FakeServer, _Clock

PROGRESS_LIGHTS = 4
PLAYER_PIXELS = 12

HORIZON = 0.060
TICK = 1.0 / 44.0
TAP = ["ie1", 0.0, 80.0, 1]
DEVICES = (("c1", "devtower1"), ("c2", "ie1"))


class _Rig:
    """A running MinigameBit: devtower1 holding the tower role, ie1 playing."""

    def __init__(self):
        self.clk = _Clock(100.0)
        self.gs = GameServer({"MinigameBit": MinigameBit},
                             cue_horizon=HORIZON, clock=self.clk)
        self.server = FakeServer()
        self.agent = DeviceLinkAgent(self.gs, self.server, clock=self.clk,
                                     horizon=HORIZON)
        self.gs.load_bit("MinigameBit")
        for client, dev in DEVICES:
            self._hello(client, dev)
        self.gs.run()
        self.agent.poll()
        assert self.gs.bit.status()["players"] == ["ie1"]
        assert self.gs.bit.status()["towers"] == ["devtower1"]
        self._seen = 0
        self._last_hello = self.clk()
        self.frames = []           # [(when, frame)] for devtower1

    def _hello(self, client, dev):
        self.server.arrive(client)
        self.server.deliver(client, "/game/hello", "sss", [dev, "sim", "1"])
        self.agent.poll()

    def run(self, seconds):
        for _ in range(int(round(seconds / TICK))):
            self.clk.advance(TICK)
            if self.clk() - self._last_hello >= HELLO_INTERVAL_S:
                # Real devices' heartbeats; without them Control reaps the
                # silent devices after 15 s.
                for client, dev in DEVICES:
                    self.server.deliver(client, "/game/hello", "sss",
                                        [dev, "sim", "1"])
                self._last_hello = self.clk()
            self.agent.poll()
            self.gs.tick(TICK)
            for _dev, msg in self.server.sent[self._seen:]:
                if msg["address"] == "/devtower1/leds":
                    self.frames.append((msg["timestamp"],
                                        bytes(msg["args"][0])))
            self._seen = len(self.server.sent)

    def tap(self, n=1):
        for _ in range(n):
            self.server.deliver("c2", "/game/tap", "sffi", TAP,
                                timestamp=self.clk())
            self.agent.poll()

    def pixels(self):
        """Each pixel's brightness (its brightest channel) on the newest
        frame."""
        frame = self.frames[-1][1]
        px = [frame[i:i + 4] for i in range(0, len(frame), 4)]
        assert len(px) == PLAYER_PIXELS
        return [max(p) for p in px]

    def lights(self):
        return self.pixels()[:PROGRESS_LIGHTS]


def test_the_bar_starts_dark():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    assert rig.pixels() == [0] * PLAYER_PIXELS


def test_taps_fill_the_bar_bottom_first_a_twentyfifth_of_a_light_each():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    rig.tap(35)                    # 35%: light 0 full, light 1 at 0.4
    rig.run(1.0)
    lights = rig.lights()
    assert lights[0] > 200
    assert lights[1] == pytest.approx(lights[0] * 0.4, abs=3)
    assert lights[2:] == [0] * (PROGRESS_LIGHTS - 2)
    assert rig.pixels()[PROGRESS_LIGHTS:] == \
        [0] * (PLAYER_PIXELS - PROGRESS_LIGHTS)


def test_one_tap_lights_a_twentyfifth_of_the_first_light():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    rig.tap(25)
    rig.run(1.0)
    full = rig.lights()[0]
    assert full > 200
    rig2 = _Rig()
    rig2.run(3.0)
    rig2.tap(1)
    rig2.run(1.0)
    assert rig2.lights()[0] == pytest.approx(full / 25, abs=2)


def test_a_player_never_gets_the_tower_surface():
    rig = _Rig()
    assert rig.agent.bridges["ie1"].session.cap.pixel_count == PLAYER_PIXELS
    zones = {z.name for z in rig.agent.bridges["devtower1"].session.cap.zones}
    assert {f"p{k}" for k in range(PROGRESS_LIGHTS)} <= zones
