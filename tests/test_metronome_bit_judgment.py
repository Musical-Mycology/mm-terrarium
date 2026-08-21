"""tests/test_metronome_bit_judgment.py"""
from bits.metronome_bit import MetronomeBit
from control.cues import FireTrigger, ROOM

B = MetronomeBit.BEAT_S


def _started(players=("ie1",)):
    bit = MetronomeBit()
    for dev in players:
        bit.on_join(dev, "player")
    bit.on_run_start()
    bit.cues(100.0)                    # anchor: t0 = 100.0 + LEAD_IN_S
    return bit


def _wait_grid(bit, cycle, wait_beat):
    return bit._t0 + (cycle * 8 + 4 + wait_beat) * B


def _drain_until(bit, at, step=0.02):
    """Advance cues(at) to `at`, returning every FireTrigger seen."""
    fires, t = [], bit._last_drained if hasattr(bit, "_last_drained") else 100.0
    while t < at:
        t = min(t + step, at)
        fires += [c for c in bit.cues(t) if isinstance(c, FireTrigger)]
    bit._last_drained = t
    return fires


def _tap_all_four(bit, cycle, dev="ie1", err=0.0):
    for w in range(4):
        bit._on_tap(dev, [dev, 1.0, 50.0, 1], _wait_grid(bit, cycle, w) + err)


def test_all_four_in_time_taps_fire_fireworks():
    bit = _started()
    _tap_all_four(bit, 0, err=0.049)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    names = [(f.name, f.dev) for f in fires]
    assert ("fireworks_player", "ie1") in names
    assert ("fireworks_room", None) in names
    assert bit._successes["ie1"] == 1


def test_tap_51ms_off_is_off_grid_and_spoils():
    bit = _started()
    _tap_all_four(bit, 0)
    bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, 1) + 0.051)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    assert ("fail_player", "ie1") in [(f.name, f.dev) for f in fires]
    assert bit._successes.get("ie1", 0) == 0


def test_missing_beat_fails_phrase():
    bit = _started()
    for w in (0, 1, 3):                # beat 2 never tapped
        bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, w))
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    assert any(f.name == "fail_room" for f in fires)


def test_round_robin_ignores_off_turn_taps():
    bit = _started(players=("ie1", "ie2"))
    # cycle 0 is ie1's; ie2's taps must neither help nor spoil
    bit._on_tap("ie2", ["ie2", 1.0, 50.0, 1], _wait_grid(bit, 0, 0) + 0.3)
    _tap_all_four(bit, 0, dev="ie1")
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    assert ("fireworks_player", "ie1") in [(f.name, f.dev) for f in fires]
    # cycle 1 belongs to ie2
    assert bit._turn_dev(1) == "ie2"


def test_no_players_means_no_judgment():
    bit = MetronomeBit()
    bit.on_run_start()
    bit.cues(100.0)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.3)
    assert fires == []


def test_judgment_fires_exactly_once_per_cycle():
    bit = _started()
    _tap_all_four(bit, 0)
    end = _wait_grid(bit, 0, 3) + 0.2
    first = _drain_until(bit, end)
    again = _drain_until(bit, end + 0.5)
    assert sum(f.name == "fireworks_room" for f in first) == 1
    assert not any(f.name == "fireworks_room" for f in again)


def test_tap_errors_are_recorded_in_ms():
    bit = _started()
    bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, 0) + 0.02)
    assert bit._tap_errors_ms[-1] == 20.0


def test_multi_cycle_jump_judges_every_pending_cycle():
    bit = _started(players=("ie1", "ie2"))
    _tap_all_four(bit, 0, dev="ie1")
    _tap_all_four(bit, 1, dev="ie2")
    end = _wait_grid(bit, 1, 3) + 0.2
    fires = [c for c in bit.cues(end) if isinstance(c, FireTrigger)]
    names = [(f.name, f.dev) for f in fires]
    assert ("fireworks_player", "ie1") in names
    assert ("fireworks_player", "ie2") in names
    assert sum(f.name == "fireworks_room" for f in fires) == 2


def test_failed_dev_stays_dark_until_next_turn():
    bit = _started(players=("ie1", "ie2"))
    # cycle 0 is ie1's turn; never tap, so it fails the phrase.
    end = _wait_grid(bit, 0, 3) + 0.2
    fires = _drain_until(bit, end)
    assert ("fail_player", "ie1") in [(f.name, f.dev) for f in fires]
    assert "ie1" in bit._failed_devs

    # Beats 8..15 belong to ie2 (cycle 1); ie1 must get no cc:11 pulses,
    # while ROOM and ie2 keep pulsing.
    for k in range(8, 16):
        cues = bit._beat_cues(k)
        level_cues = [c for c in cues if c.status == 0xB0 and c.data1 == 11]
        assert not any(c.dev == "ie1" for c in level_cues)
        assert any(c.dev == ROOM for c in level_cues)

    # Beat 16 (cycle 2, pos 0) is ie1's turn again; recovery cues relight it.
    recovery = bit._beat_cues(16)
    assert any(c.dev == "ie1" and c.status == 0xB0 and c.data1 == 74
               and c.data2 == bit.GREEN_CC for c in recovery)
    assert any(c.dev == "ie1" and c.status == 0xB0 and c.data1 == 11
               and c.data2 == bit.LEVEL_BASE for c in recovery)
    assert "ie1" not in bit._failed_devs

    # Subsequent beats pulse ie1 again.
    later = bit._beat_cues(17)
    level_cues = [c for c in later if c.status == 0xB0 and c.data1 == 11]
    assert any(c.dev == "ie1" and c.data2 == bit.LEVEL_PULSE for c in level_cues)


def test_call_beat_tap_spoils_phrase():
    bit = _started()
    call_beat_at = bit._t0 + 0 * 8 * B + 1 * B   # cycle 0, call beat 1
    bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], call_beat_at)
    _tap_all_four(bit, 0)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    assert ("fail_player", "ie1") in [(f.name, f.dev) for f in fires]
    assert bit._successes.get("ie1", 0) == 0
