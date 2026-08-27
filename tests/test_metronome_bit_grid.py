"""tests/test_metronome_bit_grid.py

MetronomeBit.cues(at) is gone (Bit.fires(at) is FireFunction-only now, and
the engine never called cues() to begin with). The per-beat LightCue
schedule these tests exercise -- click note pairs, the downbeat green
flash, the level pulse decay -- has no engine seam any more (see
bits/metronome/metronome_bit.py's fires(at) docstring and this task's
DONE_WITH_CONCERNS report): it is not a periodic waveform a GENERATOR
Function could declare, since each beat's cues depend on runtime state
(whose turn it is, which devs are currently failed) a GeneratorSpec cannot
read. _beat_cues(k) survives as a pure helper, tested directly here.
"""
from bits.metronome.metronome_bit import MetronomeBit
from control.cues import ROOM, LightCue


def _started(players=("ie1",)):
    bit = MetronomeBit()
    for dev in players:
        bit.on_join(dev, "player")
    bit.on_run_start()
    bit.fires(100.0)                   # anchor: t0 = 100.0 + LEAD_IN_S
    return bit


def _lightcues(cues):
    return [c for c in cues if isinstance(c, LightCue)]


def test_anchor_set_on_first_fires_call():
    bit = MetronomeBit()
    bit.on_join("ie1", "player")
    bit.on_run_start()
    bit.fires(100.0)
    assert bit._t0 == 100.0 + bit.LEAD_IN_S


def test_beat_zero_emits_hard_click_at_t0():
    bit = _started()
    cues = _lightcues(bit._beat_cues(0))
    ons = [c for c in cues
           if c.dev == ROOM and c.status == 0x90 and c.data1 == bit.CLICK_KEY]
    assert ons and ons[0].data2 == bit.HARD_VEL
    assert ons[0].when == bit._t0
    offs = [c for c in cues if c.status == 0x80]
    assert offs and offs[0].when > ons[0].when


def test_soft_clicks_on_beats_1_to_3_and_none_on_wait_beats():
    bit = _started()
    seen = {}
    for k in range(9):                 # one cycle plus the next hard click
        for c in _lightcues(bit._beat_cues(k)):
            if c.dev == ROOM and c.status == 0x90 and c.data1 == bit.CLICK_KEY:
                seen[k] = c.data2
    assert seen[1] == seen[2] == seen[3] == bit.SOFT_VEL
    assert 4 not in seen and 5 not in seen and 6 not in seen and 7 not in seen
    assert seen[8] == bit.HARD_VEL     # next cycle's hard click


def test_beat_cues_is_pure_and_repeatable():
    bit = _started()
    first = _lightcues(bit._beat_cues(0))
    again = _lightcues(bit._beat_cues(0))
    assert [(c.dev, c.status, c.data1, c.data2, c.when) for c in first] == \
           [(c.dev, c.status, c.data1, c.data2, c.when) for c in again]


def test_green_pulse_rides_every_beat_on_room_and_players():
    bit = _started(players=("ie1", "ie2"))
    cues = _lightcues(bit._beat_cues(0))
    pulse_devs = {c.dev for c in cues if c.status == 0xB0 and c.data1 == 11
                  and c.data2 == bit.LEVEL_PULSE}
    assert pulse_devs == {ROOM, "ie1", "ie2"}
