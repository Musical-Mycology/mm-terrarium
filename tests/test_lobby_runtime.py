"""LobbyRuntime through fake sinks: no luxaeterna, no Arco (spec 4, 5)."""
from control.breath import BREATH_CC, breath_cc
from control.lobby import (BELL_PROGRAM, BELL_VEL, DEFAULT_LOBBY, FEEDBACK_ACCEPT,
                           FEEDBACK_MINIMUM, FEEDBACK_NONE, FEEDBACK_REFUSED,
                           GREEN, GREEN_HUE_CC, HUE_CC, LOBBY_DRONE_KEY,
                           LOBBY_PROGRAM, LobbyState, RED, WHITE, hue_drift_cc)
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
        self.joins = []      # (dev, client)
        self.events = []     # (event, dev)
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
            request_join=lambda dev, client: self.joins.append((dev, client)),
            announce=lambda ev, dev: self.events.append((ev, dev)),
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
    assert rt.join_count == 1


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


def test_feedback_flashes_fixtures_green_once_red_twice_or_thrice():
    rt, sinks, clock = _rt()
    rt.start()
    rt.feedback(FEEDBACK_ACCEPT)
    _run(rt, sinks, clock, 2.0)
    assert [o[1:4] for o in sinks.overrides] == [
        ("sim-main", GREEN, 1.0), ("sim-accent", GREEN, 1.0)]
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
    assert [o[1] for o in sinks.overrides] == ["sim-main"]


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


def test_double_tap_from_an_invited_device_requests_the_join():
    rt, sinks, clock = _rt()
    rt.start()
    rt.consider_invite("ie3")
    assert rt.observe_tap("ie3", 1, 50.0, "c3") is False
    assert rt.observe_tap("ie3", 1, 51.0, "c3") is True
    assert sinks.joins == [("ie3", "c3")]
    assert ("handshake", "ie3") in sinks.events
    assert rt.observe_tap("ie9", 2, 60.0, "c9") is False     # never invited
    rt.forget("ie3")
    assert not rt.is_invited("ie3")
