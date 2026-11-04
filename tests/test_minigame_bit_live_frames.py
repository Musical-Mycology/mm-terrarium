"""MinigameBit's four phases, rendered the way a live run renders it.

test_minigame_bit.py asserts on the cues the Bit reports. This asserts on
the bytes a Tower displays: a real GameServer, a real DeviceLinkAgent and
real luxaeterna LightSessions, ticked at 44 Hz on a hand-advanced clock (the
harness pattern of tests/test_metronome_bit_live_frames.py). The Tower is a
plain devtower<N> device carrying the devtower instrument and holding the
tower role, so its frame is its own 128 px player frame: the bar's four
groups of eight LEDs, then four RGB responder lights of 24, while a
player's stays the 12 px frame.
"""

import pytest

pytest.importorskip("luxaeterna")

from pathlib import Path

from bits.minigame.minigame_bit import (
    BAR_PIXELS,
    BEAT_S,
    RESPONDER_PIXELS,
    RESPONDER_RGB,
    RESPONDERS,
    TOWER_PIXELS,
    MinigameBit,
)
from control.catalog import load_catalog
from control.engine import GameServer
from devicelink.contract import HELLO_INTERVAL_S
from devicelink.agent import DeviceLinkAgent

from tests.test_devicelink_agent import FakeServer, _Clock

ROOT = Path(__file__).resolve().parents[1]
PLAYER_PIXELS = 12
WHITE_RGB = (255, 255, 255)     # the bar's W white, folded into the blob
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
        on the frames sent (R, G, B blobs, the bar's W folded in): 'white'
        (all eight LEDs full white), 'dim' (all eight the same partial
        white, mid-fade), 'dark', or 'mixed'."""
        start = len(self.frames)
        self.run(seconds)
        seen = [set() for _ in range(4)]
        for _when, frame in self.frames[start:]:
            px = [tuple(frame[i:i + 3]) for i in range(0, len(frame), 3)]
            assert len(px) == TOWER_PIXELS
            for g in range(BAR_PIXELS // GROUP):
                group = px[g * GROUP:(g + 1) * GROUP]
                if all(p == WHITE_RGB for p in group):
                    seen[g].add("white")
                elif all(max(p) == 0 for p in group):
                    seen[g].add("dark")
                elif len(set(group)) == 1 and len(set(group[0])) == 1:
                    seen[g].add("dim")
                else:
                    seen[g].add("mixed")
        return seen

    def responders_lit(self, seconds):
        """Run `seconds` and report, frame by frame, which responders are
        lit (the frame's index list), asserting each lit one is the same
        partial-or-full beige on all 24 LEDs, R >= G >= B."""
        start = len(self.frames)
        self.run(seconds)
        out = []
        for _when, frame in self.frames[start:]:
            lit = []
            for r in range(RESPONDERS):
                lo = (BAR_PIXELS + r * RESPONDER_PIXELS) * 3
                px = {tuple(frame[i:i + 3])
                      for i in range(lo, lo + RESPONDER_PIXELS * 3, 3)}
                if px != {(0, 0, 0)}:
                    assert len(px) == 1, (r, px)
                    rgb = px.pop()
                    assert rgb[0] >= rgb[1] >= rgb[2], (r, rgb)
                    lit.append(r)
            out.append(lit)
        return out


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
    # Frames go out on change only: the first may be an earlier blink's
    # tail, not an onset.
    was_lit = max(rig.frames[start][1][0:3]) > 0
    for when, frame in rig.frames[start + 1:]:
        lit = max(frame[0:3]) > 0
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


def test_the_responders_light_in_order():
    rig = _Rig()
    rig.run(3.0)
    lit = rig.responders_lit(9 * BEAT_S)
    # a fade's tail overlaps the next responder's start, never two tails
    assert all(len(r) <= 2 for r in lit), lit
    order = []
    was = set()
    for r in lit:
        order += sorted(set(r) - was)
        was = set(r)
    assert len(order) >= 8
    first = order[0]
    assert order == [(first + i) % RESPONDERS for i in range(len(order))]


def test_a_responder_at_full_is_beige():
    rig = _Rig()
    rig.run(3.0)
    rig.run(4 * BEAT_S)
    peaks = {tuple(frame[i:i + 3])
             for _when, frame in rig.frames
             for i in range(BAR_PIXELS * 3, TOWER_PIXELS * 3, 3)}
    assert RESPONDER_RGB in peaks


def test_the_tap_light_prints_its_o2_send_time(capsys):
    rig = _Rig()
    rig.run(0.5)
    capsys.readouterr()
    tap_at = rig.clk() - 0.010
    rig.server.deliver("c2", "/game/tap", "sffi", ["ie1", 0.0, 80.0, 1],
                       timestamp=tap_at)
    rig.run(TICK)
    lines = [l for l in capsys.readouterr().err.splitlines()
             if l.startswith("TAP LIGHT")]
    assert len(lines) == 1, lines
    now = rig.clk()
    assert lines[0] == (f"TAP LIGHT sent at o2 t={now:.3f} s: dev=ie1 "
                        f"tap_t={tap_at:.3f} "
                        f"after_tap_ms={(now - tap_at) * 1000:.1f}")
    rig.run(0.5)                     # the fade's later frames do not print
    assert "TAP LIGHT" not in capsys.readouterr().err
