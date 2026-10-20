"""MinigameBit's progress bar, rendered the way a live run renders it.

test_minigame_bit.py asserts on the cues the Bit reports. This asserts on
the bytes the bound Tower displays: a real GameServer, a real
DeviceLinkAgent and real luxaeterna LightSessions, ticked at 44 Hz on a
hand-advanced clock (the harness pattern of
tests/test_metronome_bit_live_frames.py).
"""

import pytest

pytest.importorskip("luxaeterna")

from bits.minigame.minigame_bit import MinigameBit
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.contract import HELLO_INTERVAL_S
from devicelink.agent import DeviceLinkAgent

from tests.test_devicelink_agent import FakeServer, _Clock

TOWER_BAR = load_terrarium_config("terrarium.toml").rooms["TOWER_BAR"]
PROGRESS_LIGHTS = 10

HORIZON = 0.060
TICK = 1.0 / 44.0
TAP = ["ie1", 0.0, 80.0, 1]


class _Rig:
    """A running MinigameBit: the Tower bound to its fixture, ie1 playing."""

    def __init__(self):
        self.clk = _Clock(100.0)
        binding = RoomBindingRegistry(clock=self.clk)
        self.gs = GameServer({"MinigameBit": MinigameBit},
                             room_binding=binding,
                             cue_horizon=HORIZON, clock=self.clk)
        self.gs.room = Room(name="TOWER_BAR", profile=TOWER_BAR.profile,
                            node_id=TOWER_BAR.node_id)
        self.server = FakeServer()
        self.agent = DeviceLinkAgent(self.gs, self.server, clock=self.clk,
                                     horizon=HORIZON)
        self.gs.load_bit("MinigameBit")
        self._hello("c1", "tower")
        binding.arm("TOWER_BAR", "tower", window_seconds=10.0)
        self.server.deliver("c1", "/game/handshake", "sss",
                            ["tower", self.gs.round_id or "",
                             TOWER_BAR.node_id])
        self.agent.poll()
        assert self.gs.room.bound["tower"] == "tower"
        self._hello("c2", "ie1")
        self.gs.run()
        self.agent.poll()
        assert self.gs.bit.status()["players"] == ["ie1"]
        self._seen = 0
        self._last_hello = self.clk()
        self.frames = []           # [(when, frame)] for the Tower

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
                for client, dev in (("c1", "tower"), ("c2", "ie1")):
                    self.server.deliver(client, "/game/hello", "sss",
                                        [dev, "sim", "1"])
                self._last_hello = self.clk()
            self.agent.poll()
            self.gs.tick(TICK)
            for _dev, msg in self.server.sent[self._seen:]:
                if msg["address"] == "/tower/leds":
                    self.frames.append((msg["timestamp"],
                                        bytes(msg["args"][0])))
            self._seen = len(self.server.sent)

    def tap(self, n=1):
        for _ in range(n):
            self.server.deliver("c2", "/game/tap", "sffi", TAP,
                                timestamp=self.clk())
            self.agent.poll()

    def lights(self):
        """Each light's brightness (its brightest channel) on the newest
        frame."""
        frame = self.frames[-1][1]
        px = [frame[i:i + 4] for i in range(0, len(frame), 4)]
        assert len(px) == PROGRESS_LIGHTS
        return [max(p) for p in px]


def test_the_bar_starts_dark():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    assert rig.lights() == [0] * PROGRESS_LIGHTS


def test_taps_fill_the_bar_bottom_first_a_tenth_of_a_light_each():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    rig.tap(15)                    # 15%: light 0 full, light 1 half
    rig.run(1.0)
    lights = rig.lights()
    assert lights[0] > 200
    assert lights[1] == pytest.approx(lights[0] / 2, abs=3)
    assert lights[2:] == [0] * (PROGRESS_LIGHTS - 2)


def test_one_tap_lights_a_tenth_of_the_first_light():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    rig.tap(10)
    rig.run(1.0)
    full = rig.lights()[0]
    assert full > 200
    rig2 = _Rig()
    rig2.run(3.0)
    rig2.tap(1)
    rig2.run(1.0)
    assert rig2.lights()[0] == pytest.approx(full / 10, abs=2)
