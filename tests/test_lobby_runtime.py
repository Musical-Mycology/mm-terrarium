"""LobbyRuntime through fake sinks: no luxaeterna, no Arco (spec 4, 5)."""
from control.breath import BREATH_CC, breath_cc
from control.lobby import (BELL_PROGRAM, BELL_VEL, DEFAULT_LOBBY,
                           DEVICE_FLASH_TRAIN_S, FEEDBACK_ACCEPT,
                           FEEDBACK_MINIMUM, FEEDBACK_NONE, FEEDBACK_REFUSED,
                           GREEN, GREEN_HUE_CC, HUE_CC, LOBBY_DRONE_KEY,
                           LOBBY_PROGRAM, LobbyState, PULSE_PEAK,
                           PULSE_PERIOD_S, RED, WHITE, hue_drift_cc,
                           pulse_level)
from devicelink.lobby_runtime import LobbyRuntime, LobbySinks


class _Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _Sinks:
    def __init__(self, fixtures=("main", "accent"), bound=None):
        self.fixtures = list(fixtures)
        self.bound = dict(bound or {"main": "sim-main", "accent": "sim-accent"})
        self.light = []      # (fixture, status, d1, d2)
        self.audio = []      # (fixture, status, d1, d2)
        self.controls = []   # (fixture, cc, value)
        self.notes = []      # (program, key, vel, duration)
        self.overrides = []  # (t, dev, rgb, level, duration)
        self.plays = []      # (dev, name, params)
        self.events = []     # (event, dev)
        self.bases = []     # (t, dev, rgb-or-None, level)
        self.t = 0.0

    def as_sinks(self):
        return LobbySinks(
            fixture_names=lambda: list(self.fixtures),
            bound_dev=lambda name: self.bound.get(name),
            feed_light=lambda f, s, a, b: self.light.append((f, s, a, b)),
            feed_audio=lambda f, s, a, b: self.audio.append((f, s, a, b)),
            set_audio_control=lambda f, cc, v: self.controls.append((f, cc, v)),
            play_note=lambda p, k, v, d: self.notes.append((p, k, v, d)),
            set_override=lambda dev, rgb, lvl, dur: self.overrides.append(
                (self.t, dev, rgb, lvl, dur)),
            send_play=lambda dev, n, p: self.plays.append((dev, n, p)),
            announce=lambda ev, dev: self.events.append((ev, dev)),
            set_base=lambda dev, rgb, lvl: self.bases.append(
                (self.t, dev, rgb, lvl)),
        )


def _rt(clock=None, sinks=None, config=DEFAULT_LOBBY, room_program=115):
    clock = clock or _Clock()
    sinks = sinks or _Sinks()
    rt = LobbyRuntime(config, sinks.as_sinks(), clock, room_program=room_program)
    return rt, sinks, clock


def _run(rt, sinks, clock, seconds, dt=1 / 44):
    for _ in range(int(seconds / dt)):
        clock.advance(dt)
        sinks.t = clock.t
        rt.tick()


def _bases_for(sinks, dev):
    return [b for b in sinks.bases if b[1] == dev]


def test_start_sets_the_pad_and_sounds_the_drone_on_every_fixture():
    rt, sinks, clock = _rt()
    rt.start()
    for name in ("main", "accent"):
        assert (name, 0xC0, LOBBY_PROGRAM, 0) in sinks.audio
        assert (name, 0x90, LOBBY_DRONE_KEY, 80) in sinks.audio


def test_tick_feeds_drift_and_breath_to_light_and_breath_to_audio():
    rt, sinks, clock = _rt()
    rt.start()
    rt.tick()
    assert ("main", 0xB0, HUE_CC, hue_drift_cc(0.0)) in sinks.light
    assert ("main", 0xB0, BREATH_CC, breath_cc(0.0)) in sinks.light
    assert ("main", BREATH_CC, breath_cc(0.0)) in sinks.controls
    before = len(sinks.light)
    rt.tick()                                   # same clock: nothing new
    assert len(sinks.light) == before
    _run(rt, sinks, clock, 2.0)
    hues = [d2 for (f, s, d1, d2) in sinks.light if f == "main" and d1 == HUE_CC]
    assert len(set(hues)) > 3                   # the drift moves


def test_full_pins_green_stops_the_drone_and_keeps_the_breath_on_light_only():
    rt, sinks, clock = _rt()
    rt.start()
    _run(rt, sinks, clock, 0.5)
    rt.set_state(LobbyState.FULL)
    assert ("main", 0x80, LOBBY_DRONE_KEY, 0) in sinks.audio
    assert ("main", 0xB0, HUE_CC, GREEN_HUE_CC) in sinks.light
    n_light, n_ctrl = len(sinks.light), len(sinks.controls)
    _run(rt, sinks, clock, 3.0)
    hues = [d2 for (f, s, d1, d2) in sinks.light[n_light:] if d1 == HUE_CC]
    assert hues == []                           # no drift while FULL
    assert any(d1 == BREATH_CC for (f, s, d1, d2) in sinks.light[n_light:])
    assert len(sinks.controls) == n_ctrl        # breath no longer reaches audio
    rt.set_state(LobbyState.WAITING)
    assert sinks.audio.count(("main", 0x90, LOBBY_DRONE_KEY, 80)) == 2


def test_stop_silences_and_restores_the_bits_program():
    rt, sinks, clock = _rt(room_program=115)
    rt.start()
    rt.stop()
    for name in ("main", "accent"):
        assert (name, 0x80, LOBBY_DRONE_KEY, 0) in sinks.audio
        assert (name, 0xC0, 115, 0) in sinks.audio
    n = len(sinks.light)
    _run(rt, sinks, clock, 1.0)
    assert len(sinks.light) == n                # stopped: nothing fed


def test_join_ceremony_flashes_bells_and_chimes_in_order():
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_scored_join("ie1")
    rt.tick()
    t0 = clock.t
    assert sinks.overrides[-1][1:] == ("ie1", GREEN, 1.0, 0.2)
    _run(rt, sinks, clock, 0.45)
    greens = [o for o in sinks.overrides if o[1] == "ie1"]
    assert len(greens) == 2 and abs(greens[1][0] - (t0 + 0.4)) < 0.03
    assert sinks.notes == []
    _run(rt, sinks, clock, 0.4)                 # past 0.8
    assert sinks.notes == [(BELL_PROGRAM, 69, BELL_VEL, 1.0)]
    assert sinks.plays == []
    _run(rt, sinks, clock, 1.0)                 # past 1.8
    assert sinks.plays == [("ie1", "chime", "key=69")]


def test_second_join_climbs_the_scale_and_waits_its_turn():
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_scored_join("ie1")
    rt.on_scored_join("ie2")                    # same instant
    _run(rt, sinks, clock, 5.0)
    assert [n[1] for n in sinks.notes] == [69, 71]
    bells = [n for n in sinks.notes]
    assert len(bells) == 2
    # second ceremony starts span (1.8) + gap (1.0) after the first
    ie2_first = [o for o in sinks.overrides if o[1] == "ie2"][0][0]
    ie1_first = [o for o in sinks.overrides if o[1] == "ie1"][0][0]
    assert abs((ie2_first - ie1_first) - 2.8) < 0.03


def test_feedback_flashes_fixtures_green_twice_red_twice_or_thrice():
    rt, sinks, clock = _rt()
    rt.start()
    rt.feedback(FEEDBACK_ACCEPT)
    _run(rt, sinks, clock, 2.0)
    assert [o[1:4] for o in sinks.overrides] == [
        ("sim-main", GREEN, 1.0), ("sim-accent", GREEN, 1.0)] * 2
    sinks.overrides.clear()
    rt.feedback(FEEDBACK_MINIMUM)
    _run(rt, sinks, clock, 2.0)
    assert [o[2] for o in sinks.overrides].count(RED) == 4      # 2 flashes x 2 fixtures
    times = sorted({round(o[0], 2) for o in sinks.overrides})
    assert abs(times[1] - times[0] - 0.5) < 0.03
    sinks.overrides.clear()
    rt.feedback(FEEDBACK_REFUSED)
    _run(rt, sinks, clock, 2.0)
    assert [o[2] for o in sinks.overrides].count(RED) == 6
    sinks.overrides.clear()
    rt.feedback(FEEDBACK_NONE)
    _run(rt, sinks, clock, 1.0)
    assert sinks.overrides == []


def test_unbound_fixture_gets_no_flash():
    rt, sinks, clock = _rt(sinks=_Sinks(bound={"main": "sim-main"}))
    rt.start()
    rt.feedback(FEEDBACK_ACCEPT)
    _run(rt, sinks, clock, 1.0)
    assert [o[1] for o in sinks.overrides] == ["sim-main", "sim-main"]


def test_invite_flashes_white_twice_and_repeats_every_interval():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    assert rt.is_invited("ie3")
    assert sinks.events == [("invite", "ie3")]
    _run(rt, sinks, clock, 1.0)
    whites = [o for o in sinks.overrides if o[1] == "ie3"]
    assert [o[2:] for o in whites] == [(WHITE, 1.0, 0.2), (WHITE, 1.0, 0.2)]
    for _ in range(4 * 44):
        clock.advance(1 / 44)
        sinks.t = clock.t
        rt.consider_invite("ie3")
        rt.tick()
    assert len([o for o in sinks.overrides if o[1] == "ie3"]) == 2   # not yet 5 s
    for _ in range(2 * 44):
        clock.advance(1 / 44)
        sinks.t = clock.t
        rt.consider_invite("ie3")
        rt.tick()
    assert len([o for o in sinks.overrides if o[1] == "ie3"]) == 4


def test_no_invites_while_full():
    rt, sinks, clock = _rt()
    rt.start()
    rt.set_state(LobbyState.FULL)
    rt.consider_invite("ie3")
    assert not rt.is_invited("ie3")


def test_invites_are_announced_once_and_forgotten_on_request():
    # /<dev>/handshake is the agent's, not the runtime's (spec 2026-10-01
    # sections 3.1, 3.3): the runtime owns only the flash and the announce.
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    _run(rt, sinks, clock, DEFAULT_LOBBY.invite_interval_s + 0.1)
    rt.consider_invite("ie3")
    assert sinks.events.count(("invite", "ie3")) == 1
    rt.forget("ie3")
    assert not rt.is_invited("ie3")


def test_stop_ends_invites_but_drains_queued_thunks():
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_scored_join("ie1")
    rt.stop()
    assert rt.draining()
    rt.consider_invite("ie3")       # a stopped runtime invites no one
    assert not rt.is_invited("ie3")
    assert not [o for o in sinks.overrides if o[1] == "ie3"]
    _run(rt, sinks, clock, 2.0)
    assert not rt.draining()
    assert [p[0] for p in sinks.plays] == ["ie1"]


def test_stop_then_start_feeds_the_first_breath_and_hue_again():
    """stop() has to reset the de-dupe caches: a restarted lobby whose opening frame repeats the
    values the old one last sent would come up silent and dark, because
    the caches still hold them."""
    rt, sinks, clock = _rt()
    rt.start()
    rt.tick()
    rt.stop()
    n_light, n_ctrl = len(sinks.light), len(sinks.controls)
    rt.start()                                  # same clock: t is 0.0 again
    rt.tick()
    fresh_light = sinks.light[n_light:]
    fresh_ctrl = sinks.controls[n_ctrl:]
    for name in ("main", "accent"):
        assert (name, 0xB0, HUE_CC, hue_drift_cc(0.0)) in fresh_light
        assert (name, 0xB0, BREATH_CC, breath_cc(0.0)) in fresh_light
        assert (name, BREATH_CC, breath_cc(0.0)) in fresh_ctrl


def test_ceremony_survives_stop():
    """stop() keeps the queued ceremony thunks (spec 2026-10-01): the
    chime of a validation that landed just before start still plays."""
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_scored_join("ie1")
    rt.stop()
    clock.advance(2.0)
    rt.tick()
    assert sinks.plays == [("ie1", "chime", sinks.plays[0][2])]


def test_forget_drops_only_that_devs_queued_invite_flashes():
    """A device that validates mid-invite must see no white flash after
    its /validated: forget purges its still-queued invite flashes, and
    leaves another dev's invite flashes and the ceremony's green ones."""
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    rt.consider_invite("ie4")
    _run(rt, sinks, clock, 0.1)          # the first white flash of each
    rt.forget("ie3")
    rt.on_scored_join("ie3")
    _run(rt, sinks, clock, 2.0)
    ie3 = [o[2] for o in sinks.overrides if o[1] == "ie3"]
    assert ie3 == [WHITE, GREEN, GREEN]  # no second white after forget
    ie4 = [o[2] for o in sinks.overrides if o[1] == "ie4"]
    assert ie4 == [WHITE, WHITE]
    assert [p[:2] for p in sinks.plays] == [("ie3", "chime")]


def test_pulse_level_rises_from_dark_to_its_peak_and_back():
    assert pulse_level(-1.0) == 0.0 and pulse_level(0.0) == 0.0
    assert abs(pulse_level(PULSE_PERIOD_S / 2) - PULSE_PEAK) < 1e-9
    assert pulse_level(PULSE_PERIOD_S) < 1e-9
    assert 0.0 < pulse_level(1.0) < PULSE_PEAK
    assert DEVICE_FLASH_TRAIN_S == 0.8


def test_an_invite_holds_the_white_base_dark_through_its_flashes_then_pulses():
    rt, sinks, clock = _rt()
    rt.start()
    t0 = clock.t
    rt.consider_invite("ie3")
    _run(rt, sinks, clock, 0.75)
    dark = _bases_for(sinks, "ie3")
    assert dark and all(b[2] == WHITE and b[3] == 0.0 for b in dark)
    _run(rt, sinks, clock, 2.0)
    lit = [b for b in _bases_for(sinks, "ie3") if b[3] > 0]
    assert lit and lit[0][0] >= t0 + DEVICE_FLASH_TRAIN_S - 1e-9
    assert all(b[2] == WHITE and b[3] <= PULSE_PEAK + 1e-9 for b in lit)


def test_a_steady_base_is_sent_once():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    _run(rt, sinks, clock, 0.5)                 # still held dark
    assert len(_bases_for(sinks, "ie3")) == 1


def test_validation_turns_the_base_green_after_the_ceremony_flashes():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    rt.consider_invite("ie2")
    _run(rt, sinks, clock, 1.0)
    t0 = clock.t
    rt.forget("ie1")
    rt.on_scored_join("ie1")                    # ceremony slot at t0
    rt.forget("ie2")
    rt.on_scored_join("ie2")                    # queued: slot at t0 + 2.8
    assert _bases_for(sinks, "ie1")[-1][2:] == (None, 0.0)
    _run(rt, sinks, clock, 0.75)
    new = [b for b in _bases_for(sinks, "ie1") if b[0] > t0]
    assert new and all(b[2] == GREEN and b[3] == 0.0 for b in new)
    _run(rt, sinks, clock, 1.5)
    assert _bases_for(sinks, "ie1")[-1][2] == GREEN
    assert _bases_for(sinks, "ie1")[-1][3] > 0
    assert [b for b in _bases_for(sinks, "ie2") if b[0] > t0 and b[3] > 0] == []
    _run(rt, sinks, clock, 2.0)
    lit2 = [b for b in _bases_for(sinks, "ie2") if b[0] > t0 and b[3] > 0]
    assert lit2 and lit2[0][2] == GREEN
    assert lit2[0][0] >= t0 + 2.8 + DEVICE_FLASH_TRAIN_S - 1e-6


def test_full_clears_white_bases_and_keeps_green_ones():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    rt.consider_invite("ie2")
    _run(rt, sinks, clock, 1.0)
    rt.forget("ie2")
    rt.on_scored_join("ie2")
    _run(rt, sinks, clock, 0.1)
    rt.set_state(LobbyState.FULL)
    assert _bases_for(sinks, "ie1")[-1][2] is None
    _run(rt, sinks, clock, 2.0)
    assert _bases_for(sinks, "ie1")[-1][2] is None
    assert _bases_for(sinks, "ie2")[-1][2] == GREEN


def test_stop_clears_every_base():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    rt.on_scored_join("ie2")
    _run(rt, sinks, clock, 0.1)
    rt.stop()
    assert _bases_for(sinks, "ie1")[-1][2] is None
    assert _bases_for(sinks, "ie2")[-1][2] is None
    n = len(sinks.bases)
    _run(rt, sinks, clock, 1.0)
    assert len(sinks.bases) == n


def test_a_deny_purges_the_white_flash_flashes_red_twice_and_holds_the_pulse_dark():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    _run(rt, sinks, clock, 0.3)                 # second white flash still queued
    t0 = clock.t
    rt.on_deny("ie1")
    _run(rt, sinks, clock, 0.75)
    after = [o for o in sinks.overrides if o[1] == "ie1" and o[0] >= t0]
    assert [o[2:] for o in after] == [(RED, 1.0, 0.2), (RED, 1.0, 0.2)]
    assert abs(after[1][0] - after[0][0] - 0.4) < 0.03
    assert [b for b in _bases_for(sinks, "ie1") if b[3] > 0] == []
    _run(rt, sinks, clock, 1.0)
    lit = [b for b in _bases_for(sinks, "ie1") if b[3] > 0]
    assert lit and lit[0][2] == WHITE
    assert lit[0][0] >= t0 + DEVICE_FLASH_TRAIN_S - 1e-9


def test_a_deny_with_no_status_flashes_red_and_sets_no_base():
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_deny("ie9")
    _run(rt, sinks, clock, 2.0)
    assert [o[2] for o in sinks.overrides if o[1] == "ie9"] == [RED, RED]
    assert _bases_for(sinks, "ie9") == []


def test_a_deny_leaves_the_device_invited():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie1")
    rt.on_deny("ie1")
    assert rt.is_invited("ie1")


def test_a_steady_lit_base_is_not_resent_at_the_same_clock():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    _run(rt, sinks, clock, DEVICE_FLASH_TRAIN_S + PULSE_PERIOD_S / 4)
    lit = [b for b in _bases_for(sinks, "ie3") if b[3] > 0]
    assert lit                                  # the pulse is up
    n = len(sinks.bases)
    rt.tick()                                   # same clock, same level
    assert len(sinks.bases) == n


def test_stop_drops_a_denys_queued_second_red_flash():
    """A start 0.1 s after a deny: the second red flash would land after
    the device holds its role and paint over its sys:loaded welcome."""
    rt, sinks, clock = _rt()
    rt.start()
    rt.on_deny("ie1")
    _run(rt, sinks, clock, 0.1)                 # the first red flash
    rt.stop()
    _run(rt, sinks, clock, 1.0)
    assert [o[2] for o in sinks.overrides if o[1] == "ie1"] == [RED]
    assert not rt.draining()


def test_stop_drops_an_invite_trains_queued_second_white_flash():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    _run(rt, sinks, clock, 0.1)                 # the first white flash
    rt.stop()
    _run(rt, sinks, clock, 1.0)
    assert [o[2] for o in sinks.overrides if o[1] == "ie3"] == [WHITE]
    assert not rt.draining()


def test_stop_keeps_the_ceremony_while_dropping_device_flashes():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    rt.on_deny("ie4")
    rt.on_scored_join("ie1")
    _run(rt, sinks, clock, 0.1)
    rt.stop()
    assert rt.draining()
    _run(rt, sinks, clock, 2.0)
    assert [o[2] for o in sinks.overrides if o[1] == "ie3"] == [WHITE]
    assert [o[2] for o in sinks.overrides if o[1] == "ie4"] == [RED]
    assert [o[2] for o in sinks.overrides if o[1] == "ie1"] == [GREEN, GREEN]
    assert [(n[0], n[2]) for n in sinks.notes] == [(BELL_PROGRAM, BELL_VEL)]
    assert [p[:2] for p in sinks.plays] == [("ie1", "chime")]
    assert not rt.draining()
