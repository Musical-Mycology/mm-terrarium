"""tests/test_metronome_bit_grid.py"""
from bits.metronome_bit import MetronomeBit
from control.cues import ROOM, LightCue


def _started(players=("ie1",)):
    bit = MetronomeBit()
    for dev in players:
        bit.on_join(dev, "player")
    bit.on_run_start()
    return bit


def _lightcues(cues):
    return [c for c in cues if isinstance(c, LightCue)]


def test_anchor_set_on_first_cues_call():
    bit = _started()
    bit.cues(100.0)
    assert bit._t0 == 100.0 + bit.LEAD_IN_S


def test_beat_zero_emits_hard_click_at_t0():
    bit = _started()
    cues = bit.cues(100.0)
    ons = [c for c in _lightcues(cues)
           if c.dev == ROOM and c.status == 0x90 and c.data1 == bit.CLICK_KEY]
    assert ons and ons[0].data2 == bit.HARD_VEL
    assert ons[0].when == bit._t0
    offs = [c for c in _lightcues(cues) if c.status == 0x80]
    assert offs and offs[0].when > ons[0].when


def test_soft_clicks_on_beats_1_to_3_and_none_on_wait_beats():
    bit = _started()
    bit.cues(100.0)                    # anchor + beat 0
    seen = {}
    for step in range(1, 200):         # tick forward well past one cycle
        at = 100.0 + step * 0.05
        for c in _lightcues(bit.cues(at)):
            if c.dev == ROOM and c.status == 0x90 and c.data1 == bit.CLICK_KEY:
                k = round((c.when - bit._t0) / bit.BEAT_S)
                seen[k] = c.data2
    assert seen[1] == seen[2] == seen[3] == bit.SOFT_VEL
    assert 4 not in seen and 5 not in seen and 6 not in seen and 7 not in seen
    assert seen[8] == bit.HARD_VEL     # next cycle's hard click


def test_emission_is_idempotent_per_beat():
    bit = _started()
    bit.cues(100.0)
    again = [c for c in _lightcues(bit.cues(100.0))
             if c.status == 0x90 and c.data1 == bit.CLICK_KEY
             and c.when == bit._t0]
    assert again == []                 # beat 0 not re-emitted


def test_green_pulse_rides_every_beat_on_room_and_players():
    bit = _started(players=("ie1", "ie2"))
    cues = _lightcues(bit.cues(100.0))
    pulse_devs = {c.dev for c in cues if c.status == 0xB0 and c.data1 == 11
                  and c.data2 == bit.LEVEL_PULSE}
    assert pulse_devs == {ROOM, "ie1", "ie2"}
