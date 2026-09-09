"""tests/test_metronome_bit_engine.py

Engine-level coverage for MetronomeBit's beat-grid schedule, restored
through FireFunction.at (control/cues.py) after the controller ruling that
the old cues(at) per-beat LightCue schedule (click, downbeat flash,
turn-recovery flash, level pulse-then-decay) must come back through the new
Function/fires(at) architecture rather than being dropped. Loads a real
MetronomeBit into a real GameServer with an advancing fake clock and checks
GameServer.on_light_cue receives the same bytes the old _beat_cues(k)
produced, stamped at that beat's own grid time -- not the tick's `at`.
"""
from types import SimpleNamespace

from bits.metronome.metronome_bit import MetronomeBit
from control.engine import GameServer
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from tests.instrument_fixtures import GENERIC_SURFACE

B = MetronomeBit.BEAT_S


class _Room:
    def __init__(self, bound):
        self.name = "DEMO"
        self.node_id = "ROOM_DEMO_NODE"
        self.profile = RoomProfile(surface_id="room_demo", fixtures=(
            RoomFixture(name="main", color_order="GRB",
                       blocks=(RoomBlock("main", 0, 10),),
                       zones=(RoomZone("all", 0, 10),),
                       instrument=GENERIC_SURFACE),
        ))
        self.bound = bound


def _running(players=("ie1",), t0=100.0):
    clk = SimpleNamespace(t=t0)
    gs = GameServer({"metro": MetronomeBit}, clock=lambda: clk.t)
    gs.room = _Room({"main": "sim-room-main"})
    light = []
    gs.on_light_cue = lambda *a: light.append(a)
    gs.load_bit("metro")
    for dev in players:
        gs.hello(dev, "sim", "1")
        gs.join(dev, "METRO_PLAYER_NODE")
    gs.run()
    return gs, clk, light


def _tick(gs, clk, seconds, step=0.05):
    """Advance the fake clock and drive gs.tick in small ticks, the same
    cadence a real RUNNING loop uses -- the beat walk's own `<= at + BEAT_S`
    lookahead is what makes each beat land exactly once regardless of how
    finely ticks are sliced, so a small fixed step avoids floating-point
    drift against exact multiples of BEAT_S."""
    end = clk.t + seconds
    while clk.t < end - 1e-9:
        clk.t += step
        gs.tick(step)


def test_downbeat_fires_hard_click_and_green_flash_at_beat_grid_time():
    gs, clk, light = _running()
    _tick(gs, clk, B)               # one beat: anchors t0, fires beat 0
    grid0 = gs.bit._t0
    assert grid0 is not None and grid0 > 100.0

    clicks_on = [c for c in light
                 if c[0] == "sim-room-main" and c[1] == 0x90
                 and c[2] == gs.bit.CLICK_KEY]
    assert clicks_on and clicks_on[0][3] == gs.bit.HARD_VEL
    assert clicks_on[0][4] == grid0

    clicks_off = [c for c in light if c[1] == 0x80 and c[2] == gs.bit.CLICK_KEY]
    assert clicks_off and clicks_off[0][4] == grid0 + 0.1

    green = [c for c in light
             if c[0] == "sim-room-main" and c[1] == 0xB0 and c[2] == 74
             and c[3] == gs.bit.GREEN_CC]
    assert green and green[0][4] == grid0


def test_pulse_decay_rides_room_and_player_at_beat_grid_time():
    gs, clk, light = _running()
    _tick(gs, clk, B)
    grid0 = gs.bit._t0

    pulses = [c for c in light if c[1] == 0xB0 and c[2] == 11
              and c[3] == gs.bit.LEVEL_PULSE]
    decays = [c for c in light if c[1] == 0xB0 and c[2] == 11
              and c[3] == gs.bit.LEVEL_BASE]
    pulse_devs = {c[0] for c in pulses if c[4] == grid0}
    decay_devs = {c[0] for c in decays if c[4] == grid0 + 0.15}
    assert pulse_devs == {"sim-room-main", "ie1"}
    assert decay_devs == {"sim-room-main", "ie1"}


def test_soft_click_on_call_beats_1_to_3_at_their_own_grid_time():
    gs, clk, light = _running()
    _tick(gs, clk, 4 * B)            # beats 0..3
    grid0 = gs.bit._t0

    softs = sorted(
        (c[4] for c in light
         if c[0] == "sim-room-main" and c[1] == 0x90
         and c[2] == gs.bit.CLICK_KEY and c[3] == gs.bit.SOFT_VEL))
    assert softs == [grid0 + 1 * B, grid0 + 2 * B, grid0 + 3 * B]


def test_a_beat_fires_exactly_once_even_across_extra_ticks():
    gs, clk, light = _running()
    _tick(gs, clk, B)                # fires beat 0
    before = len(light)
    gs.tick(0.001)                   # no new beat gridpoint crossed
    gs.tick(0.001)
    assert len(light) == before


def test_a_tap_made_when_a_wait_beat_is_presented_is_judged_on_time():
    """A physically perfect tap: stamped at the instant Control PRESENTS a
    wait beat. GameServer.data() adds cue_horizon to that stamp before the
    handler sees it, so without Bit.cue_horizon the Bit read every perfect
    tap as +horizon (60 ms, outside its 50 ms window). Reproduced live
    2026-09-08; see the spec section 1.3."""
    clk = SimpleNamespace(t=100.0)
    gs = GameServer({"metro": MetronomeBit}, clock=lambda: clk.t,
                    cue_horizon=0.060)
    gs.room = _Room({"main": "sim-room-main"})
    gs.on_light_cue = lambda *a: None
    gs.load_bit("metro")
    gs.hello("ie1", "sim", "1")
    gs.join("ie1", "METRO_PLAYER_NODE")
    gs.run()
    bit = gs.bit
    while bit._t0 is None:
        _tick(gs, clk, 0.01, step=0.01)
    presented = bit._grid(4)            # cycle 0, wait beat 0
    while clk.t < presented - 1e-9:
        _tick(gs, clk, 0.01, step=0.01)
    clk.t = presented
    assert gs.data("ie1", "tap", ["ie1", 1.0, 50.0, 1],
                   gesture_time=presented) is None
    assert bit._tap_errors_ms == [0.0]
