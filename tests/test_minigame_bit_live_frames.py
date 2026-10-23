"""MinigameBit's progress bar, rendered the way a live run renders it.

test_minigame_bit.py asserts on the cues the Bit reports. This asserts on
the bytes a Tower displays: a real GameServer, a real DeviceLinkAgent and
real luxaeterna LightSessions, ticked at 44 Hz on a hand-advanced clock (the
harness pattern of tests/test_metronome_bit_live_frames.py). The Tower is a
plain devtower<N> device carrying the devtower instrument and holding the
tower role, so its frame is its own 32 px player frame, the bar on all 32
LEDs two to a light, while a player's stays the 12 px frame.
"""

import pytest

pytest.importorskip("luxaeterna")

from pathlib import Path

from bits.minigame.minigame_bit import MinigameBit
from control.catalog import load_catalog
from control.engine import GameServer
from devicelink.contract import HELLO_INTERVAL_S
from devicelink.agent import DeviceLinkAgent

from tests.test_devicelink_agent import FakeServer, _Clock

ROOT = Path(__file__).resolve().parents[1]
PROGRESS_LIGHTS = 16
TOWER_PIXELS = 32
PLAYER_PIXELS = 12

HORIZON = 0.060
TICK = 1.0 / 44.0
TAP = ["ie1", 0.0, 80.0, 1]
# (client, dev, the instrument its hello declares carrying)
DEVICES = (("c1", "devtower1", "devtower"), ("c2", "ie1", "testshroom"))


class _Rig:
    """A running MinigameBit: devtower1 holding the tower role, ie1 playing."""

    def __init__(self):
        self.clk = _Clock(100.0)
        catalog = load_catalog(ROOT / "instruments")
        carried = {name: catalog.get("published", name).instrument
                   for name in ("devtower", "testshroom")}
        self.gs = GameServer({"MinigameBit": MinigameBit},
                             cue_horizon=HORIZON, clock=self.clk,
                             carried_instruments=carried)
        self.server = FakeServer()
        self.agent = DeviceLinkAgent(self.gs, self.server, clock=self.clk,
                                     horizon=HORIZON)
        self.gs.load_bit("MinigameBit")
        for client, dev, instrument in DEVICES:
            self._hello(client, dev, instrument)
        self.gs.run()
        self.agent.poll()
        assert self.gs.bit.status()["players"] == ["ie1"]
        assert self.gs.bit.status()["towers"] == ["devtower1"]
        self._seen = 0
        self._last_hello = self.clk()
        self.frames = []           # [(when, frame)] for devtower1
        self.player_frames = []    # the same for ie1

    def _hello(self, client, dev, instrument):
        self.server.arrive(client)
        self.server.deliver(client, "/game/hello", "ssss",
                            [dev, "sim", "1", instrument])
        self.agent.poll()

    def run(self, seconds):
        for _ in range(int(round(seconds / TICK))):
            self.clk.advance(TICK)
            if self.clk() - self._last_hello >= HELLO_INTERVAL_S:
                # Real devices' heartbeats; without them Control reaps the
                # silent devices after 15 s.
                for client, dev, instrument in DEVICES:
                    self.server.deliver(client, "/game/hello", "ssss",
                                        [dev, "sim", "1", instrument])
                self._last_hello = self.clk()
            self.agent.poll()
            self.gs.tick(TICK)
            for _dev, msg in self.server.sent[self._seen:]:
                if msg["address"] == "/devtower1/leds":
                    self.frames.append((msg["timestamp"],
                                        bytes(msg["args"][0])))
                elif msg["address"] == "/ie1/leds":
                    self.player_frames.append((msg["timestamp"],
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
        assert len(px) == TOWER_PIXELS
        return [max(p) for p in px]

    def lights(self):
        """Each light's brightness: its two LEDs, which always match."""
        px = self.pixels()
        pairs = [px[2 * k:2 * k + 2] for k in range(PROGRESS_LIGHTS)]
        assert all(a == b for a, b in pairs), pairs
        return [a for a, _b in pairs]


def test_the_bar_starts_dark():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    assert rig.pixels() == [0] * TOWER_PIXELS


def test_taps_fill_the_bar_bottom_first_two_leds_a_light():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    rig.tap(35)                    # 35%: lights 0..4 full, light 5 at 0.6
    rig.run(1.0)
    lights = rig.lights()
    assert lights[0] > 200
    assert lights[:5] == [lights[0]] * 5
    assert lights[5] == pytest.approx(lights[0] * 0.6, abs=3)
    assert lights[6:] == [0] * (PROGRESS_LIGHTS - 6)


def test_the_full_bar_lights_all_32_leds():
    rig = _Rig()
    rig.run(3.0)
    rig.tap(100)
    rig.run(1.0)
    px = rig.pixels()
    assert px[0] > 200 and px == [px[0]] * TOWER_PIXELS


def test_one_tap_lights_0_16_of_the_first_light():
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    rig.tap(7)                     # 7%: light 0 full
    rig.run(1.0)
    full = rig.lights()[0]
    assert full > 200
    rig2 = _Rig()
    rig2.run(3.0)
    rig2.tap(1)
    rig2.run(1.0)
    assert rig2.lights()[0] == pytest.approx(full * 0.16, abs=2)


def test_the_tower_gets_a_32_px_frame_and_a_player_keeps_12():
    rig = _Rig()
    rig.run(3.0)
    assert {len(f) for _when, f in rig.frames} == {TOWER_PIXELS * 4}
    assert {len(f) for _when, f in rig.player_frames} == {PLAYER_PIXELS * 4}


def test_a_player_never_gets_the_tower_surface():
    rig = _Rig()
    assert rig.agent.bridges["ie1"].session.cap.pixel_count == PLAYER_PIXELS
    cap = rig.agent.bridges["devtower1"].session.cap
    assert cap.pixel_count == TOWER_PIXELS
    zones = {z.name: (z.start, z.count) for z in cap.zones}
    assert all(zones[f"p{k}"] == (2 * k, 2) for k in range(PROGRESS_LIGHTS))
