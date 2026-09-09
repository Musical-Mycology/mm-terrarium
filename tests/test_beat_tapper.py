"""tests/test_beat_tapper.py

The Testshroom's synthetic player for a call-and-response Bit: it watches
the brightness of the frames it DISPLAYS, locks onto the beat pulses, and
taps on the answer beats. Frames here are 36-channel (12 px GRB); a
"level" is painted as one byte on every channel so brightness is
level * 36.
"""
from harness.beat_tapper import BeatTapper

BEAT = 0.75
BASE, PULSE = 60, 110


def frame(level: int) -> bytes:
    return bytes([level]) * 36


def pulse_train(tapper, t_first, beats, period=BEAT, level=BASE, pulse=PULSE):
    """Feed `beats` beats from t_first: a pulse frame at each gridpoint and
    a decay frame 150 ms later. Returns the list of (beat_time, result)."""
    out = []
    for k in range(beats):
        t = t_first + k * period
        out.append((t, tapper.observe(frame(pulse), t)))
        tapper.observe(frame(level), t + 0.15)
    return out


def test_first_two_pulses_lock_and_nothing_taps_during_the_call():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)      # steady base before beat 0
    results = pulse_train(tapper, 20.0, 4)
    assert tapper.locked
    assert [r for _, r in results] == [None, None, None, None]


def test_taps_on_the_four_answer_beats_of_every_cycle():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    results = pulse_train(tapper, 20.0, 16)
    tapped = [r for _, r in results if r is not None]
    assert tapped == [4, 5, 6, 7, 12, 13, 14, 15]
    assert tapper.taps == 8


def test_the_grant_rise_from_black_is_not_beat_zero():
    """The role grant lights a black canvas moments before beat 0."""
    tapper = BeatTapper()
    tapper.observe(frame(0), 19.0)
    tapper.observe(frame(BASE), 19.2)      # grant: black -> base
    results = pulse_train(tapper, 20.0, 8)
    tapped = [r for _, r in results if r is not None]
    assert tapped == [4, 5, 6, 7]


def test_fireworks_flashes_between_beats_are_ignored():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    pulse_train(tapper, 20.0, 8)           # cycle 0, locked
    # 12 flashes every 120 ms starting just after beat 8's gridpoint,
    # riding on top of the real pulses of cycle 1.
    t8 = 20.0 + 8 * BEAT
    flash_times = [t8 + 0.05 + i * 0.12 for i in range(12)]
    events = []
    for k in range(8, 16):
        events.append((20.0 + k * BEAT, PULSE))
        events.append((20.0 + k * BEAT + 0.15, BASE))
    for ft in flash_times:
        events.append((ft, 200))
        events.append((ft + 0.08, BASE))
    events.sort()
    tapped = [tapper.observe(frame(level), t) for t, level in events]
    tapped = [r for r in tapped if r is not None]
    # A flash that lands inside a beat's window may be taken AS that beat
    # (it is within tolerance); no beat index may be tapped twice and no
    # call beat may be tapped.
    assert tapped == sorted(set(tapped))
    assert all(8 <= k < 16 and k % 8 >= 4 for k in tapped)


def test_a_dark_spell_re_indexes_by_time():
    """A failed player goes dark for a cycle and is relit on its next
    turn's downbeat: the next rise is beat 16, not beat 8."""
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    pulse_train(tapper, 20.0, 8)
    tapper.observe(frame(0), 20.0 + 8 * BEAT)        # fail: dark
    results = pulse_train(tapper, 20.0 + 16 * BEAT, 8)
    tapped = [r for _, r in results if r is not None]
    assert tapped == [20, 21, 22, 23]


def test_period_is_refined_over_the_long_baseline():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    # First interval carries 10 ms of display jitter; later pulses are exact.
    tapper.observe(frame(PULSE), 20.0)
    tapper.observe(frame(BASE), 20.15)
    tapper.observe(frame(PULSE), 20.0 + BEAT + 0.010)
    tapper.observe(frame(BASE), 20.0 + BEAT + 0.16)
    for k in range(2, 12):
        tapper.observe(frame(PULSE), 20.0 + k * BEAT)
        tapper.observe(frame(BASE), 20.0 + k * BEAT + 0.15)
    assert abs(tapper.period - BEAT) < 0.002


def test_an_implausible_first_interval_restarts_the_lock():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    tapper.observe(frame(PULSE), 12.0)     # a lone rise 8 s early
    tapper.observe(frame(BASE), 12.15)
    results = pulse_train(tapper, 20.0, 8)
    tapped = [r for _, r in results if r is not None]
    assert tapped == [4, 5, 6, 7]


def test_reset_forgets_the_lock():
    tapper = BeatTapper()
    tapper.observe(frame(BASE), 10.0)
    pulse_train(tapper, 20.0, 8)
    tapper.reset()
    assert not tapper.locked and tapper.taps == 0
