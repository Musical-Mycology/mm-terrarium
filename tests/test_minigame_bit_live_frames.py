"""MinigameBit's four phases, rendered the way a live run renders it.

test_minigame_bit.py asserts on the cues the Bit reports. This asserts on
the bytes a Tower displays: a real GameServer, a real DeviceLinkAgent and
real luxaeterna LightSessions, ticked at 44 Hz on a hand-advanced clock (the
harness pattern of tests/test_metronome_bit_live_frames.py). The Tower is a
plain devtower<N> device carrying the devtower instrument and holding the
tower role, so its frame is its own 32 px player frame, four groups of
eight LEDs, while a player's stays the 12 px frame.
"""

import pytest

pytest.importorskip("luxaeterna")

from pathlib import Path

from bits.minigame.minigame_bit import BEAT_S, WHITE, MinigameBit
from control.catalog import load_catalog
from control.engine import GameServer
from devicelink.contract import HELLO_INTERVAL_S
from devicelink.agent import DeviceLinkAgent

from tests.test_devicelink_agent import FakeServer, _Clock

ROOT = Path(__file__).resolve().parents[1]
TOWER_PIXELS = 32
PLAYER_PIXELS = 12
GROUP = 8

HORIZON = 0.060
TICK = 1.0 / 44.0
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

    def groups_over(self, seconds):
        """Run `seconds` and report, per group, the set of its states seen
        on the frames sent: 'white' (all eight LEDs full white), 'dim'
        (all eight the same partial white, mid-fade), 'dark', or 'mixed'."""
        start = len(self.frames)
        self.run(seconds)
        seen = [set() for _ in range(4)]
        for _when, frame in self.frames[start:]:
            px = [tuple(frame[i:i + 4]) for i in range(0, len(frame), 4)]
            assert len(px) == TOWER_PIXELS
            for g in range(4):
                group = px[g * GROUP:(g + 1) * GROUP]
                if all(p == WHITE for p in group):
                    seen[g].add("white")
                elif all(max(p) == 0 for p in group):
                    seen[g].add("dark")
                elif len(set(group)) == 1 and group[0][:3] == (0, 0, 0):
                    seen[g].add("dim")
                else:
                    seen[g].add("mixed")
        return seen


@pytest.mark.parametrize("phase", [1, 2, 3, 4])
def test_each_phase_blinks_one_group_over_solid_ones(phase):
    rig = _Rig()
    rig.run(3.0)                   # clear the ~1.5 s role signature
    assert rig.gs.run_action(f"phase{phase}") is None
    rig.run(BEAT_S)                # let the switch settle
    seen = rig.groups_over(4 * BEAT_S)
    for g in range(4):
        if g < phase - 1:
            assert seen[g] == {"white"}, (g, seen)
        elif g == phase - 1:
            assert seen[g] == {"white", "dim", "dark"}, (g, seen)
        else:
            assert seen[g] == {"dark"}, (g, seen)


def test_the_blink_is_on_the_130_bpm_beat():
    rig = _Rig()
    rig.run(3.0)
    start = len(rig.frames)
    rig.run(8 * BEAT_S)
    ons = []
    was_lit = False
    for when, frame in rig.frames[start:]:
        lit = max(frame[0:4]) > 0
        if lit and not was_lit:
            ons.append(when)
        was_lit = lit
    gaps = [b - a for a, b in zip(ons, ons[1:])]
    assert len(gaps) >= 6
    assert all(g == pytest.approx(BEAT_S, abs=TICK + 0.001) for g in gaps)


def test_going_back_to_phase_one_darkens_the_upper_groups():
    rig = _Rig()
    rig.run(3.0)
    rig.gs.run_action("phase4")
    rig.run(1.0)
    rig.gs.run_action("phase1")
    rig.run(0.1)                   # well inside one beat
    seen = rig.groups_over(4 * BEAT_S)
    assert seen[1:] == [{"dark"}] * 3


def test_the_tower_gets_a_32_px_frame_and_a_player_keeps_12():
    rig = _Rig()
    rig.run(3.0)
    assert {len(f) for _when, f in rig.frames} == {TOWER_PIXELS * 4}
    assert {len(f) for _when, f in rig.player_frames} == {PLAYER_PIXELS * 4}


def test_a_player_never_gets_the_tower_surface():
    rig = _Rig()
    assert rig.agent.bridges["ie1"].session.cap.pixel_count == PLAYER_PIXELS
    assert rig.agent.bridges["devtower1"].session.cap.pixel_count == \
        TOWER_PIXELS
