"""tests/test_metronome_bit_cycle_length.py

`[rhythm] beats_per_cycle` reaches MetronomeBit through its manifest or a
profile override. The Bit used to grade taps and time its judge deadline on a
hard-coded 8-beat cycle, so any other length misgraded: a 4-beat cycle's taps
were compared against gridpoints in the NEXT cycle. A cycle splits evenly into
call beats then answer (wait) beats: 4+4 at 8, 2+2 at 4.
"""
from pathlib import Path

from bits.metronome.metronome_bit import MetronomeBit
from control.bit_config import merge_overrides, parse_manifest
from control.cues import FireFunction

MANIFEST = Path(__file__).resolve().parents[1] / "bits" / "metronome" / "bit.toml"


def _bit(beats_per_cycle):
    config = parse_manifest(MANIFEST.read_text(), source=str(MANIFEST))
    config = merge_overrides(
        config, {"rhythm": {"beats_per_cycle": beats_per_cycle}},
        source="test-profile")
    bit = MetronomeBit(config)
    bit.on_join("ie1", "player")
    bit.on_run_start()
    bit.fires(100.0)                    # anchor: t0 = 100.0 + LEAD_IN_S
    return bit


def _wait_grid(bit, cycle, wait_beat):
    n = bit.BEATS_PER_CYCLE
    return bit._t0 + (cycle * n + n // 2 + wait_beat) * bit.BEAT_S


def _drain_until(bit, start, at, step=0.02):
    fires, t = [], start
    while t < at:
        t = min(t + step, at)
        fires += [c for c in bit.fires(t) if isinstance(c, FireFunction)]
    return fires


def test_four_beat_cycle_grades_its_two_answer_beats():
    bit = _bit(4)
    assert bit.BEATS_PER_CYCLE == 4
    for w in range(2):
        bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, w) + 0.01)
    assert bit._tap_errors_ms == [10.0, 10.0]
    fires = _drain_until(bit, 100.0, _wait_grid(bit, 0, 1) + 0.2)
    names = [(f.name, f.dev) for f in fires]
    assert ("fireworks_player", "ie1") in names
    assert bit._successes["ie1"] == 1


def test_four_beat_cycle_is_judged_at_its_own_last_beat():
    bit = _bit(4)
    last = bit._t0 + 3 * bit.BEAT_S
    deadline = last + bit.TOLERANCE_S + bit.JUDGE_SLACK_S
    _drain_until(bit, 100.0, deadline - 0.001)
    assert bit._judged_cycles == 0
    _drain_until(bit, deadline - 0.001, deadline + 0.001)
    assert bit._judged_cycles == 1


def test_four_beat_cycle_clicks_only_on_its_call_beats():
    bit = _bit(4)
    beats = {}
    for k in range(4):
        beats[k] = {f.name for f in bit._beat_fires(k)}
    assert "metro_downbeat" in beats[0]
    assert "metro_click" in beats[1]
    assert not {"metro_downbeat", "metro_click"} & beats[2]
    assert not {"metro_downbeat", "metro_click"} & beats[3]


def test_eight_beat_default_is_unchanged():
    bit = _bit(8)
    for w in range(4):
        bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, w))
    fires = _drain_until(bit, 100.0, _wait_grid(bit, 0, 3) + 0.2)
    assert ("fireworks_player", "ie1") in [(f.name, f.dev) for f in fires]
