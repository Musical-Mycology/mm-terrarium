"""tests/test_metronome_bit_finale.py"""
from bits.metronome.metronome_bit import MetronomeBit
from control.cues import FireFunction

B = MetronomeBit.BEAT_S


def _run_through(bit, tap_cycles=()):
    """Anchor at 100.0, tap all four wait beats of the given cycles as ie1,
    and drain cues to the end of the last cycle's judgment."""
    bit.cues(100.0)
    for c in tap_cycles:
        for w in range(4):
            bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1],
                        bit._t0 + (c * 8 + 4 + w) * B)
    fires, t = [], 100.0
    end = bit._t0 + (bit.CYCLES * 8 - 1) * B + 0.3
    while t < end:
        t += 0.02
        bit.update(0.02)
        fires += [c for c in bit.cues(t) if isinstance(c, FireFunction)]
    return fires, t


def _started():
    bit = MetronomeBit()
    bit.on_join("ie1", "player")
    bit.on_run_start()
    return bit


def test_finale_fires_after_any_success_and_completes_after_10s():
    bit = _started()
    fires, t = _run_through(bit, tap_cycles=(0,))
    assert any(f.name == "finale" for f in fires)
    assert not bit.update(0.02)                  # finale still running
    for _ in range(600):                          # ~12 s of ticks
        bit.update(0.02)
        t += 0.02
        bit.cues(t)
    assert bit.update(0.02)


def test_no_success_completes_immediately_without_finale():
    bit = _started()
    fires, t = _run_through(bit, tap_cycles=())
    assert not any(f.name == "finale" for f in fires)
    assert bit.update(0.02)


def test_result_reports_successes():
    bit = _started()
    _run_through(bit, tap_cycles=(0,))
    assert bit.result() == {"phrases": 4, "successes": {"ie1": 1}}


def test_status_is_wire_safe():
    import math
    bit = _started()
    bit.cues(100.0)
    status = bit.status()
    assert "turn" in status and "cycle" in status
    for v in status.values():
        if isinstance(v, float):
            assert math.isfinite(v)
