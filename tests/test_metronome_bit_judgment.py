"""tests/test_metronome_bit_judgment.py"""
from bits.metronome.metronome_bit import MetronomeBit
from control.cues import FireFunction, ROOM

B = MetronomeBit.BEAT_S


def _started(players=("ie1",)):
    bit = MetronomeBit()
    for dev in players:
        bit.on_join(dev, "player")
    bit.on_run_start()
    bit.fires(100.0)                    # anchor: t0 = 100.0 + LEAD_IN_S
    return bit


def _wait_grid(bit, cycle, wait_beat):
    return bit._t0 + (cycle * 8 + 4 + wait_beat) * B


def _drain_until(bit, at, step=0.02):
    """Advance fires(at) to `at`, returning every FireFunction seen."""
    fires, t = [], bit._last_drained if hasattr(bit, "_last_drained") else 100.0
    while t < at:
        t = min(t + step, at)
        fires += [c for c in bit.fires(t) if isinstance(c, FireFunction)]
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


_JUDGMENT_NAMES = {"fireworks_player", "fireworks_room",
                   "fail_player", "fail_room", "finale"}


def test_no_players_means_no_judgment():
    """No players means no turn dev, so no cycle is ever judged -- the
    beat-grid schedule (metro_downbeat/metro_click/metro_pulse_room) still
    fires regardless of players, exactly as the old cues(at) still emitted
    ROOM-only LightCues with no one joined; only judgment fires are absent."""
    bit = MetronomeBit()
    bit.on_run_start()
    bit.fires(100.0)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.3)
    assert [f for f in fires if f.name in _JUDGMENT_NAMES] == []


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
    fires = [c for c in bit.fires(end) if isinstance(c, FireFunction)]
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

    # Beats 8..15 belong to ie2 (cycle 1); ie1 must get no metro_pulse_player
    # FireFunction, while ROOM keeps pulsing via metro_pulse_room.
    for k in range(8, 16):
        beat_fires = bit._beat_fires(k)
        pulse_players = [f for f in beat_fires if f.name == "metro_pulse_player"]
        assert not any(f.dev == "ie1" for f in pulse_players)
        assert any(f.name == "metro_pulse_room" for f in beat_fires)

    # Beat 16 (cycle 2, pos 0) is ie1's turn again; metro_recovery relights it.
    recovery = bit._beat_fires(16)
    assert any(f.name == "metro_recovery" and f.dev == "ie1" for f in recovery)
    assert "ie1" not in bit._failed_devs

    # Subsequent beats pulse ie1 again.
    later = bit._beat_fires(17)
    assert any(f.name == "metro_pulse_player" and f.dev == "ie1" for f in later)


def test_call_beat_tap_spoils_phrase():
    bit = _started()
    call_beat_at = bit._t0 + 0 * 8 * B + 1 * B   # cycle 0, call beat 1
    bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], call_beat_at)
    _tap_all_four(bit, 0)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    assert ("fail_player", "ie1") in [(f.name, f.dev) for f in fires]
    assert bit._successes.get("ie1", 0) == 0
