"""tests/test_metronome_bit_grid.py

Unit coverage for MetronomeBit's beat-grid schedule as restored through
FireFunction.at (control/cues.py): fires(at) walks the beat grid forward
exactly as the old cues(at) did, but now emits FireFunction(name, dev,
at=beat_grid_time) instead of raw LightCue tuples -- each one a SCRIPTED
Function declared in function_table (metro_downbeat, metro_click,
metro_pulse_room, metro_pulse_player, metro_recovery). Engine-level
coverage of the resulting on_light_cue bytes lives in
tests/test_metronome_bit_engine.py; this file tests fires(at)'s own
FireFunction output (names/devs/ats) and its per-beat idempotency.
"""
from bits.metronome.metronome_bit import MetronomeBit
from control.cues import FireFunction, ROOM

B = MetronomeBit.BEAT_S


def _started(players=("ie1",)):
    bit = MetronomeBit()
    for dev in players:
        bit.on_join(dev, "player")
    bit.on_run_start()
    return bit


def _names(fires):
    return [(f.name, f.dev, f.at) for f in fires]


def test_anchor_set_on_first_fires_call():
    bit = _started()
    bit.fires(100.0)
    assert bit._t0 == 100.0 + bit.LEAD_IN_S


def test_beat_zero_fires_downbeat_and_pulses_at_its_own_grid_time():
    bit = _started()
    fires = bit.fires(100.0)
    grid0 = bit._t0
    names = _names(fires)
    assert ("metro_downbeat", None, grid0) in names
    assert ("metro_pulse_room", None, grid0) in names
    assert ("metro_pulse_player", "ie1", grid0) in names


def test_soft_clicks_on_beats_1_to_3_and_none_on_wait_beats():
    bit = _started()
    seen = {}
    t = 100.0
    for _ in range(200):               # tick forward well past one cycle
        t += 0.05
        for f in bit.fires(t):
            if f.name in ("metro_downbeat", "metro_click"):
                k = round((f.at - bit._t0) / bit.BEAT_S)
                seen[k] = f.name
    assert seen[0] == "metro_downbeat"
    assert seen[1] == seen[2] == seen[3] == "metro_click"
    assert 4 not in seen and 5 not in seen and 6 not in seen and 7 not in seen
    assert seen[8] == "metro_downbeat"     # next cycle's downbeat


def test_a_beat_fires_exactly_once():
    bit = _started()
    bit.fires(100.0)
    grid0 = bit._t0
    again = [f for f in bit.fires(100.0) if f.at == grid0]
    assert again == []                 # beat 0 not re-emitted


def test_green_pulse_rides_every_beat_on_room_and_players():
    bit = _started(players=("ie1", "ie2"))
    fires = bit.fires(100.0)
    grid0 = bit._t0
    pulse_devs = set()
    for f in fires:
        if f.at != grid0:
            continue
        if f.name == "metro_pulse_room":
            pulse_devs.add(ROOM)
        elif f.name == "metro_pulse_player":
            pulse_devs.add(f.dev)
    assert pulse_devs == {ROOM, "ie1", "ie2"}
