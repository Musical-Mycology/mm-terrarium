"""BeatsBit: a devtower and up to three tuneshrooms connect, then the Console's Start
button loops piano.mid from the MIDITEST Room (bits/beats/)."""
import pytest

from control.bit_registry import BitRegistry
from bits.beats.beats_bit import BEATS_PER_MEASURE, MUSICS
from control.cues import ROOM, MusicCue, PixelFlashCue
from control.role_config import (validate_role_declarations,
                                 validate_ugen_manifest)
from control.terrarium_config import load_terrarium_config

NAME = "BeatsBit"
TICK = 1.0 / 44
BEAT_S = 60.0 / 130.0
LOOP_BEATS = 16
RESPONDER_RGB = (64, 48, 32)
RESPONDER_FADE_S = 0.25
LIGHT_DELAY_S = 0.175


def _bit():
    reg = BitRegistry.discover()
    assert NAME in reg.packages, reg.errors
    return reg.bit_class(NAME)(reg.resolve_config(NAME, {}))


def _connected():
    bit = _bit()
    bit.on_join("devtower1", "tower")
    bit.on_join("ie1", "shroom")
    bit.on_join("ie2", "shroom")
    return bit


def _start(bit, at=10.0):
    return bit.actions()["start"][1](at)


def _note_ons(cues):
    return [c for c in cues if isinstance(c, MusicCue)
            and c.status & 0xF0 == 0x90 and c.data2 > 0]


def test_manifest():
    reg = BitRegistry.discover()
    cfg = reg.resolve_config(NAME, {})
    assert cfg.launch.room_types == ("MIDITEST",)
    assert cfg.console.display_name == "Beats"
    for track in (t for tracks in MUSICS.values() for t in tracks):
        assert cfg.asset_path(track.asset).is_file()
    assert cfg.asset_path("beats1").is_file()
    assert cfg.asset_path("beats2").is_file()
    assert cfg.start.when == "immediate"
    row = next(r for r in reg.list_view(include_hidden=False)
               if r["name"] == NAME)
    assert row["hidden"] is False


def test_role_table_validates():
    validate_role_declarations(_bit().role_table)


def test_console_buttons_choose_each_player_then_start():
    assert {name: entry[0] for name, entry in _bit().actions().items()} == {
        "player1": "Player 1: -", "player2": "Player 2: -",
        "player3": "Player 3: -", "music": "Music: music1",
        "start": "Start"}


def _three_seated():
    bit = _bit()
    for dev in ("ie3", "ie1", "ie2"):       # connection order, not id order
        bit.on_join(dev, "shroom")
    return bit


def _press(bit, player, at=9.0):
    return bit.actions()[f"player{player}"][1](at)


def _players(bit):
    status = bit.status()
    return [status.get(f"player{n}") for n in (1, 2, 3)]


def _blinked(cues):
    return {c.dev for c in cues if isinstance(c, PixelFlashCue)}


def test_players_follow_connection_order_until_chosen():
    bit = _three_seated()
    assert _players(bit) == ["ie3", "ie1", "ie2"]
    assert [bit.actions()[f"player{n}"][0] for n in (1, 2, 3)] == [
        "Player 1: ie3", "Player 2: ie1", "Player 3: ie2"]
    assert _blinked(bit.verb_handlers()["tap"]("ie2", [], 9.0)) == {
        "ie1", "ie2", "ie3"}


def test_a_button_moves_its_player_on_and_swaps_with_the_other():
    bit = _three_seated()
    assert _press(bit, 1) == []             # ie3 -> ie1, who was player2
    assert _players(bit) == ["ie1", "ie3", "ie2"]
    assert bit.actions()["player1"][0] == "Player 1: ie1"


def test_a_button_wraps_round_the_connection_order():
    bit = _three_seated()
    _press(bit, 3)                          # ie2, the last, -> ie3
    assert _players(bit) == ["ie2", "ie1", "ie3"]


def test_an_empty_player_takes_the_first_shroom():
    bit = _bit()
    bit.on_join("ie1", "shroom")
    bit.on_join("ie2", "shroom")
    assert _players(bit) == ["ie1", "ie2", None]
    _press(bit, 3)
    assert _players(bit) == [None, "ie2", "ie1"]
    assert bit.actions()["player1"][0] == "Player 1: -"


def test_a_button_with_no_shroom_is_refused():
    assert _press(_bit(), 1) == "no shroom connected"


def test_a_shroom_that_is_no_player_neither_taps_nor_blinks_until_chosen():
    bit = _three_seated()
    bit.on_join("ie4", "shroom")            # every player already taken
    tap = bit.verb_handlers()["tap"]
    assert tap("ie4", [], 9.0) == []
    _press(bit, 3)                          # ie2 -> ie4, who was no player
    assert _players(bit) == ["ie3", "ie1", "ie4"]
    assert tap("ie2", [], 9.0) == []
    assert _blinked(tap("ie4", [], 9.0)) == {"ie3", "ie1", "ie4"}
    assert "score ie4" in bit.status() and "score ie2" not in bit.status()


def test_a_reconnect_keeps_a_players_place():
    bit = _three_seated()
    _press(bit, 1)
    bit.on_join("ie1", "shroom")
    bit.on_join("ie3", "shroom")
    assert _players(bit) == ["ie1", "ie3", "ie2"]


def test_leaving_the_shroom_role_frees_the_player():
    bit = _three_seated()
    bit.on_join("ie1", "solo")
    assert _players(bit) == ["ie3", None, "ie2"]
    bit.on_join("ie4", "shroom")
    assert _players(bit) == ["ie3", "ie4", "ie2"]


def test_a_tap_prints_which_player_it_is_from(capsys):
    bit = _three_seated()
    _press(bit, 1)
    tap = bit.verb_handlers()["tap"]
    for dev, source in (("ie1", "player1 (ie1)"), ("ie3", "player2 (ie3)"),
                        ("ie2", "player3 (ie2)")):
        tap(dev, [], 9.0)
        assert f"tap {source}: received" in capsys.readouterr().out
    _start(bit)
    tap("ie1", [], 10.0)
    assert "tap player1 (ie1): at measure 1" in capsys.readouterr().out


def test_start_fixes_the_players():
    bit = _three_seated()
    _start(bit)
    assert _press(bit, 1, at=11.0) == "already started"
    assert _players(bit) == ["ie3", "ie1", "ie2"]


@pytest.mark.parametrize("dev,role,admitted", [
    ("devtower1", "tower", True),
    ("ie1", "tower", False),
    ("ie1", "shroom", True),
    ("devtower1", "shroom", False),
    ("other", "shroom", False),
    ("other", "tower", False),
])
def test_admits_sorts_devices_by_id(dev, role, admitted):
    assert _bit().admits(dev, role) is admitted


@pytest.mark.parametrize("joins", [
    [],
    [("devtower1", "tower")],
    [("devtower1", "tower"), ("ie1", "shroom")],
    [("ie1", "shroom"), ("ie2", "shroom")],
])
def test_start_plays_with_any_devices_connected(joins):
    bit = _bit()
    for dev, role in joins:
        bit.on_join(dev, role)
    assert _note_ons(_start(bit))
    assert bit.status()["state"] == "playing"


def test_silent_before_start():
    bit = _connected()
    assert bit.fires(10.0) == []
    assert bit.fires(11.0) == []


def test_start_sets_the_program_and_strikes_beat_zero():
    cues = _start(_connected())
    music = [c for c in cues if isinstance(c, MusicCue)]
    assert all(c.dev == ROOM for c in music)
    assert (cues[0].status, cues[0].data1, cues[0].when) == (0xC0, 0, 10.0)
    ons = _note_ons(cues)
    assert ons and all(c.when == 10.0 for c in ons[:3])
    assert all(c.status == 0x90 for c in ons)     # part 0


# -- the music choice ------------------------------------------------------

MUSIC2 = ("bass_kick", "hihat", "humonger_bass", "hihat_x2", "saw_pad",
          "ag_bass", "melt_kit")
# (asset, its part, the beats it loops on): files 3 and 6 run a note-off
# past their last bar line, and still loop on it.
MUSIC2_LOOPS = {"bass_kick": (1, 32), "hihat": (1, 16),
                "humonger_bass": (2, 32), "hihat_x2": (1, 16),
                "saw_pad": (3, 16), "ag_bass": (4, 16), "melt_kit": (1, 16)}


def _music(bit, at=9.0):
    return bit.actions()["music"][1](at)


def _music2(progress=0):
    """music2 chosen; `progress` set ahead of Start brings in every track it
    has reached, at Start."""
    bit = _connected()
    assert _music(bit) == []
    bit._progress = progress
    return bit


def test_the_music_button_switches_between_music1_and_music2():
    bit = _connected()
    _music(bit)
    assert bit.actions()["music"][0] == "Music: music2"
    assert bit.status()["music"] == "music2"
    _music(bit)
    assert bit.actions()["music"][0] == "Music: music1"


def test_start_fixes_the_music():
    bit = _connected()
    _start(bit)
    assert _music(bit, 11.0) == "already started"
    assert bit.status()["music"] == "music1"


def test_music1_plays_only_the_piano():
    bit = _connected()
    cues = _run(bit, _start(bit), 10.0 + 40 * BEAT_S)
    assert {c.tag for c in _note_ons(cues)} == {"piano"}


def test_track_thresholds_split_progress_evenly_from_zero():
    from bits.beats.beats_bit import track_threshold
    assert [track_threshold(k, 7) for k in range(7)] == [0, 8, 15, 22, 29,
                                                         36, 43]
    assert track_threshold(0, 1) == 0


def test_music2_starts_only_its_first_track():
    bit = _music2()
    cues = _run(bit, _start(bit), 10.0 + 40 * BEAT_S)
    assert {c.tag for c in _note_ons(cues)} == {"bass_kick"}
    assert bit.status()["tracks"] == "1/7"


def test_music2_with_full_progress_at_start_plays_all_seven():
    ons = _note_ons(_start(_music2(progress=50)))
    assert {c.tag for c in ons if c.when == 10.0} == set(MUSIC2)


def test_each_track_comes_in_when_progress_reaches_its_threshold():
    """Progress 7 holds the hi-hat back; the scoring tap that makes it 8
    brings it in on that tap's own time, and it then loops on the grid."""
    bit = _music2(progress=7)
    _start(bit)
    assert bit._tracks_in == 1
    tap_at = _judged(4.0)
    cues = _judged_tap(bit, 4.0)
    assert bit._progress == 8 and bit._tracks_in == 2
    assert bit.status()["tracks"] == "2/7"
    assert any(isinstance(c, MusicCue) and c.tag == "hihat"
               and c.when == tap_at for c in cues)
    cues = _run(bit, cues, 10.0 + 20 * BEAT_S)
    hihat = {round((c.when - 10.0) / BEAT_S, 6) for c in _note_ons(cues)
             if c.tag == "hihat"}
    assert 16.0 in hihat and all(b > 4.0 for b in hihat)


def test_tracks_come_in_in_order_one_per_threshold():
    bit = _music2(progress=42)
    _start(bit)
    assert bit._tracks_in == 6
    _judged_tap(bit, 4.0)
    assert bit._tracks_in == 7
    assert bit.status()["tracks"] == "7/7"


def test_music1_plays_its_only_track_from_start():
    bit = _connected()
    _start(bit)
    assert bit.status()["tracks"] == "1/1"


def test_music2_plays_each_track_on_its_part():
    bit = _music2(progress=50)
    cues = _run(bit, _start(bit), 10.0 + 2 * BEAT_S)
    for c in _note_ons(cues):
        assert c.status == 0x90 | MUSIC2_LOOPS[c.tag][0], c.tag


def test_music2_loops_each_track_on_its_own_length():
    """Every track's beat-0 note comes back each of its loops, in step:
    across 64 beats, the 32-beat tracks twice and the 16-beat ones four
    times, never on the 36 and 20 their files would round up to."""
    bit = _music2(progress=50)
    cues = _run(bit, _start(bit), 10.0 + 64 * BEAT_S - TICK)
    for tag, (_part, loop) in MUSIC2_LOOPS.items():
        first = next(c.data1 for c in _note_ons(cues) if c.tag == tag)
        downbeats = sorted({round((c.when - 10.0) / BEAT_S, 6)
                            for c in _note_ons(cues)
                            if c.tag == tag and c.data1 == first
                            and round((c.when - 10.0) / BEAT_S, 6) % loop == 0
                            and c.when < 10.0 + 64 * BEAT_S - TICK})
        assert downbeats == [n * loop for n in range(64 // loop)], tag


def test_start_restores_every_melodic_program():
    cues = _start(_music2())
    programs = {(c.status, c.data1) for c in cues
                if isinstance(c, MusicCue) and c.status & 0xF0 == 0xC0}
    assert programs == {(0xC0, 0), (0xC2, 38), (0xC3, 90), (0xC4, 32)}


def test_the_room_declares_one_part_per_program_and_one_drum_kit():
    _light, ugen = _bit().room_manifests()
    validate_ugen_manifest(ugen, "BeatsBit")
    assert [i.get("program", "drums" if i.get("drums") else None)
            for i in ugen["instruments"]] == [0, "drums", 38, 90, 32]


def test_the_calls_still_repeat_every_four_measures_under_music2():
    """The judging cycle is 16 beats whatever the music, though music2's
    longest loop is 32: measure 5 lights what measure 1 does."""
    one = _connected()
    two = _music2()
    until = 10.0 + 5 * BEATS_PER_MEASURE * BEAT_S
    strikes = [_strikes(_run(bit, _start(bit), until)) for bit in (one, two)]
    assert strikes[0] == strikes[1]
    assert any(when > 10.0 + 4 * BEATS_PER_MEASURE * BEAT_S
               for when, _r in strikes[1])


def test_start_twice_refused_and_restarts_nothing():
    bit = _connected()
    _start(bit)
    assert _start(bit, 11.0) == "already started"


def test_status_tracks_devices_and_playing():
    bit = _connected()
    assert bit.status() == {"music": "music1", "state": "waiting",
                            "tower": ["devtower1"],
                            "shrooms": ["ie1", "ie2"],
                            "tracks": "0/1",
                            "light delay": "+175.0 ms",
                            "player1": "ie1", "player2": "ie2",
                            "progress": "0/50",
                            "score ie1": 0.0, "score ie2": 0.0}
    _start(bit)
    assert bit.status()["state"] == "playing"


def test_piano_loops_without_duplicate_notes():
    bit = _connected()
    cues = _start(bit)
    at = 10.0
    end = 10.0 + 2 * LOOP_BEATS * BEAT_S
    while at < end:
        at += TICK
        cues += bit.fires(at)
    ons = [(round((c.when - 10.0) / BEAT_S, 6), c.data1)
           for c in _note_ons(cues)]
    ons = [(b, k) for b, k in ons if b < 2 * LOOP_BEATS]
    assert len(ons) == len(set(ons))
    first = sorted((b, k) for b, k in ons if b < LOOP_BEATS)
    second = sorted((b - LOOP_BEATS, k) for b, k in ons if b >= LOOP_BEATS)
    assert first and [k for _b, k in first] == [k for _b, k in second]


def _strikes(cues):
    """Each responder lit, in order, as (when, responder): a level that rises
    on the responder's first pixel, every one of its 24 painted alike. The
    tick that reaches a beat can be just past it, so not quite at full."""
    flashes = [c for c in cues if isinstance(c, PixelFlashCue)]
    assert all(c.dev == "devtower1" for c in flashes)
    out = []
    level = {}
    for c in flashes:
        r, offset = divmod(c.pixel - 32, 24)
        if offset:
            continue
        assert sorted(f.pixel for f in flashes
                      if f.when == c.when and f.rgb == c.rgb
                      and (f.pixel - 32) // 24 == r) \
            == list(range(32 + 24 * r, 32 + 24 * (r + 1)))
        if c.rgb[0] > level.get(r, 0):
            out.append((c.when, r))
        level[r] = c.rgb[0]
    return out


def _run(bit, cues, until):
    at = 10.0
    while at < until:
        at += TICK
        cues += bit.fires(at)
    return cues


def test_no_light_before_start():
    bit = _connected()
    assert not [c for c in bit.fires(10.0) if isinstance(c, PixelFlashCue)]


def test_first_beat_lights_the_first_responder_behind_start():
    bit = _connected()
    assert _strikes(_start(bit)) == []
    assert _strikes(bit.fires(10.0 + LIGHT_DELAY_S)) == [
        (10.0 + LIGHT_DELAY_S, 0)]


def test_the_light_runs_behind_the_piano_and_the_note_stays_on_the_grid():
    """Beat 3's note keeps its grid time; only its light is held back."""
    bit = _connected()
    cues = _run(bit, _start(bit), 10.0 + 2 * BEAT_S + LIGHT_DELAY_S + TICK)
    beat = 10.0 + 2 * BEAT_S
    assert any(c.when == pytest.approx(beat) for c in _note_ons(cues))
    assert _strikes(cues)[-1][0] == pytest.approx(beat + LIGHT_DELAY_S, abs=TICK)


def test_tower_lights_nothing_with_no_tower_connected():
    bit = _bit()
    bit.on_join("ie1", "shroom")
    cues = _run(bit, _start(bit), 10.0 + LOOP_BEATS * BEAT_S)
    assert not [c for c in cues if isinstance(c, PixelFlashCue)]


def test_each_beat_lights_the_responder_of_its_place_in_the_measure():
    """beats1 on 1, 3, 4 of measure 1; beats2 on 1, 2, 2.5, 3, 4 of measure 3,
    both of them on every pass of the loop. 2 and 2.5 share the second."""
    bit = _connected()
    until = 10.0 + 2 * LOOP_BEATS * BEAT_S + LIGHT_DELAY_S - TICK
    struck = _strikes(_run(bit, _start(bit), until))
    one_pass = [(0, 0), (2, 2), (3, 3),
                (8, 0), (9, 1), (9.5, 1), (10, 2), (11, 3)]
    expected = one_pass + [(LOOP_BEATS + b, r) for b, r in one_pass]
    assert [r for _when, r in struck] == [r for _b, r in expected]
    for (when, _r), (beat, _) in zip(struck, expected):
        assert 0 <= when - (10.0 + beat * BEAT_S + LIGHT_DELAY_S) <= TICK + 1e-9


def test_a_beat_lights_on_its_tick_not_ahead_of_it():
    """The piano's notes go out on the Sequencer's look-ahead; a beat's light
    waits for the beat. Beat 3 is the first after beat 1's Start."""
    bit = _connected()
    _start(bit)
    before = 10.0 + 2 * BEAT_S + LIGHT_DELAY_S - 0.001
    assert not [c for c in bit.fires(before) if isinstance(c, PixelFlashCue)
                and 32 + 2 * 24 <= c.pixel]
    lit = [c for c in bit.fires(10.0 + 2 * BEAT_S + LIGHT_DELAY_S)
           if isinstance(c, PixelFlashCue) and c.pixel >= 32 + 2 * 24]
    assert sorted(c.pixel for c in lit) == list(range(80, 104))


def test_a_responder_fades_to_dark_and_lapses_at_the_fades_end():
    bit = _connected()
    cues = _start(bit)
    at = 10.0
    # The next beat lights another.
    while at < 10.0 + 2 * BEAT_S + LIGHT_DELAY_S - TICK:
        at += TICK
        cues += bit.fires(at)
    first = sorted((c for c in cues if isinstance(c, PixelFlashCue)
                    and c.pixel == 32), key=lambda c: c.when)
    reds = [c.rgb[0] for c in first]
    assert reds == sorted(reds, reverse=True)
    assert reds[0] >= RESPONDER_RGB[0] * (1 - TICK / RESPONDER_FADE_S) - 1
    assert len(set(reds)) > 5
    for c in first:
        assert c.when + c.duration == pytest.approx(
            10.0 + LIGHT_DELAY_S + RESPONDER_FADE_S)
        assert c.rgb[3] == 0


def test_only_the_lit_responders_pixels_are_painted():
    bit = _connected()
    cues = _run(bit, _start(bit), 10.0 + 1.5 * BEAT_S)
    flashes = [c for c in cues if isinstance(c, PixelFlashCue)]
    assert flashes and {c.pixel for c in flashes} == set(range(32, 56))


def test_a_beat_on_a_fading_responder_restarts_it_from_full():
    """Beats 2 and 2.5 of beats2 land on the second responder, a quarter of a
    second apart, inside its quarter-second fade."""
    bit = _connected()
    cues = _run(bit, _start(bit), 10.0 + 9.75 * BEAT_S + LIGHT_DELAY_S)
    assert [r for _w, r in _strikes(cues)][-2:] == [1, 1]


def _tap(bit, dev, at):
    return bit.verb_handlers()["tap"](dev, [], at)


def _flashes(cues):
    return [c for c in cues if isinstance(c, PixelFlashCue)]


def test_shroom_role_uses_tap():
    assert _bit().role_table.roles["shroom"].uses == ["tap"]


def test_a_tap_blinks_every_shroom_at_full_on_all_its_leds():
    cues = _tap(_connected(), "ie1", 10.0)
    assert {(c.dev, c.pixel) for c in cues} \
        == {(dev, px) for dev in ("ie1", "ie2") for px in range(12)}
    assert all(c.rgb == (128, 0, 255, 0) and c.when == 10.0
               and c.duration == 0.2 for c in cues)


def test_the_blink_fades_to_dark_and_lapses_at_the_fades_end():
    bit = _connected()
    _tap(bit, "ie2", 10.0)
    at = 10.0
    steps = []
    while at < 10.3:
        at += TICK
        steps += [c for c in _flashes(bit.fires(at))
                  if (c.dev, c.pixel) == ("ie1", 0)]
    blues = [c.rgb[2] for c in steps]
    assert len(set(blues)) > 5 and blues == sorted(blues, reverse=True)
    assert blues[-1] > 0
    for c in steps:
        assert c.when + c.duration == pytest.approx(10.2)
    assert bit.fires(at + TICK) == []


def test_a_tap_mid_fade_restarts_the_blink_from_full():
    bit = _connected()
    _tap(bit, "ie1", 10.0)
    bit.fires(10.1)
    again = _tap(bit, "ie2", 10.1)
    assert all(c.rgb == (128, 0, 255, 0) and c.duration == 0.2 for c in again)
    assert [c.rgb[2] for c in _flashes(bit.fires(10.1 + TICK))
            if (c.dev, c.pixel) == ("ie1", 0)][0] < 255


@pytest.mark.parametrize("dev", ["devtower1", "other"])
def test_a_tap_from_anything_but_a_shroom_blinks_nothing(dev):
    bit = _connected()
    assert _tap(bit, dev, 10.0) == []
    assert bit.fires(10.0 + TICK) == []


def test_a_tap_leaves_the_tower_dark_and_the_piano_playing():
    bit = _connected()
    _start(bit)
    assert not [c for c in _tap(bit, "ie1", 10.1) if c.dev == "devtower1"]
    assert _note_ons(_run(bit, [], 10.0 + 8 * BEAT_S))


# Answer beats, one measure after each call: beats1 on 0, 2, 3 of measure 1,
# beats2 on 0, 1, 1.5, 2, 3 of measure 3.
ANSWERS = (4, 6, 7, 12, 13, 13.5, 14, 15)
CALLS = (0, 2, 3, 8, 9, 9.5, 10, 11)
AFTER_ONE_PASS = 16.5
GRACE_BEATS = 0.020 / BEAT_S
JUDGE_DELAY_S = 0.200


def _when(beat, t0=10.0):
    return t0 + beat * BEAT_S


def _judged(beat, t0=10.0):
    """The moment judged as `beat`: JUDGE_DELAY_S behind the grid."""
    return _when(beat, t0) + JUDGE_DELAY_S


def _play(bit, taps, until_beat=AFTER_ONE_PASS, t0=10.0):
    """Start at t0, then deliver (dev, beat) taps in time order between ticks."""
    bit.actions()["start"][1](t0)
    at = t0
    pending = sorted(taps, key=lambda tap: tap[1])
    while at < _when(until_beat, t0):
        at += TICK
        while pending and _judged(pending[0][1], t0) <= at:
            dev, beat = pending.pop(0)
            bit.verb_handlers()["tap"](dev, [], _judged(beat, t0))
        bit.fires(at)
    return bit


def test_every_answer_beat_dead_on_scores_one():
    bit = _play(_connected(), [("ie1", b) for b in ANSWERS])
    assert bit.score("ie1") == pytest.approx(1.0)
    assert bit.status()["score ie1"] == 1.0


def test_no_taps_score_zero():
    assert _play(_connected(), []).score("ie1") == 0.0


@pytest.mark.parametrize("error,points", [
    (0.0, 1.0), (-0.05, 0.8), (-0.125, 0.5), (-0.2, 0.2), (-0.25, 0.0),
    (-0.26, 0.0),
    # Late, each `error` beats past the grace, which is forgiven first.
    (GRACE_BEATS, 1.0), (GRACE_BEATS + 0.05, 0.8), (GRACE_BEATS + 0.125, 0.5),
    (GRACE_BEATS + 0.2, 0.2), (GRACE_BEATS + 0.26, 0.0),
])
def test_points_fall_linearly_to_the_quarter_beat_edge(error, points):
    """Only the first answer beat is tapped; the other seven earn 0."""
    bit = _play(_connected(), [("ie1", 4 + error)])
    assert bit.score("ie1") == pytest.approx(points / len(ANSWERS))


def test_a_tap_20_ms_late_counts_as_dead_on():
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], _judged(4.0) + 0.020)
    bit._close_answers(_when(30.0))
    assert bit._earned["ie1"] == pytest.approx(1.0)


def test_the_grace_moves_the_late_edge_out_by_20_ms():
    """A quarter beat late took no points before the grace, and now takes
    some; just past the moved edge still takes the beat for nothing."""
    inside = _connected()
    inside.actions()["start"][1](10.0)
    inside.verb_handlers()["tap"]("ie1", [], _judged(4.25) + 0.010)
    assert inside._open_taps[4.0]["ie1"] > 0.0

    outside = _connected()
    outside.actions()["start"][1](10.0)
    outside.verb_handlers()["tap"]("ie1", [], _judged(4.25) + 0.030)
    assert not outside._open_taps


def test_a_tap_200_ms_after_the_beat_is_dead_on():
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], _when(4.0) + 0.200)
    assert bit._open_taps[4.0]["ie1"] == pytest.approx(1.0)


def test_a_tap_on_the_grid_beat_is_now_too_early_to_take_it(capsys):
    """200 ms is wider than the window's early half at 130 BPM."""
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], _when(4.0))
    assert not bit._open_taps
    assert "at measure 2 beat 1.000, took no beat" in capsys.readouterr().out


def test_the_window_opens_a_quarter_beat_before_the_slid_moment():
    inside = _connected()
    inside.actions()["start"][1](10.0)
    inside.verb_handlers()["tap"]("ie1", [], _judged(3.75) + 0.005)
    assert inside._open_taps[4.0]["ie1"] > 0.0

    outside = _connected()
    outside.actions()["start"][1](10.0)
    outside.verb_handlers()["tap"]("ie1", [], _judged(3.75) - 0.005)
    assert not outside._open_taps


def test_the_grace_does_not_widen_the_early_edge():
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], _judged(3.75) - 0.010)
    assert not bit._open_taps


def test_the_grace_pushes_the_beats_closing_out_too():
    """A tap at the moved edge keeps its whole slack to arrive in."""
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit._close_answers(_judged(4.25) + 0.060)
    assert bit._answers_closed == 0
    bit._close_answers(_judged(4.25) + 0.071)
    assert bit._answers_closed == 1


def test_the_window_is_in_beats_so_it_follows_the_tempo():
    """A quarter of a beat is about 115 ms at 130 BPM and 250 ms at 60: the
    same 0.2 s late is out of the window at one and inside it at the other."""
    bit = _connected()
    bit._seq.bpm = 60.0
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], 10.0 + 4 + 0.2 + JUDGE_DELAY_S)
    bit._close_answers(10.0 + 100.0)
    assert bit._earned["ie1"] == pytest.approx(0.28)  # 0.18 late, after grace

    fast = _connected()
    fast.actions()["start"][1](10.0)
    fast.verb_handlers()["tap"]("ie1", [], 10.0 + 4 * BEAT_S + 0.2 + JUDGE_DELAY_S)
    fast._close_answers(10.0 + 100.0)
    assert "ie1" not in fast._earned


@pytest.mark.parametrize("beat", CALLS + (1, 5, 8, 10))
def test_a_tap_away_from_any_answer_beat_earns_nothing(beat):
    """Tapping along with the responders' call is not the answer."""
    bit = _play(_connected(), [("ie1", beat)])
    assert bit.score("ie1") == 0.0


def test_each_player_scores_for_themselves():
    bit = _play(_connected(), [("ie1", b) for b in ANSWERS]
                + [("ie2", 3.875)])
    assert bit.score("ie1") == pytest.approx(1.0)
    assert bit.score("ie2") == pytest.approx(0.5 / len(ANSWERS))


def test_a_second_tap_cannot_improve_or_repeat_a_beat():
    bit = _play(_connected(), [("ie1", 3.875), ("ie1", 4.0), ("ie1", 4.1)])
    assert bit.score("ie1") == pytest.approx(0.5 / len(ANSWERS))


def test_close_beats_half_a_beat_apart_are_scored_separately():
    bit = _play(_connected(), [("ie1", 13.0), ("ie1", 13.5)])
    assert bit.score("ie1") == pytest.approx(2 / len(ANSWERS))


def test_the_answer_repeats_on_every_pass_of_the_loop():
    answers = [("ie1", b + p * LOOP_BEATS) for p in (0, 1) for b in ANSWERS]
    bit = _play(_connected(), answers, until_beat=LOOP_BEATS + AFTER_ONE_PASS)
    assert bit.score("ie1") == pytest.approx(1.0)
    assert bit._answers_closed == 2 * len(ANSWERS)


def test_score_is_the_mean_over_closed_beats_only():
    """Mid-way through the first answer measure, only the closed beats count."""
    bit = _play(_connected(), [("ie1", 4.0)], until_beat=5.0)
    assert bit._answers_closed == 1
    assert bit.score("ie1") == pytest.approx(1.0)
    at = _when(5.0)
    while at < _when(8.0):
        at += TICK
        bit.fires(at)
    assert bit._answers_closed == 3
    assert bit.score("ie1") == pytest.approx(1 / 3)


def test_an_open_beats_tap_does_not_count_until_it_closes():
    bit = _play(_connected(), [("ie1", 4.0)], until_beat=4.6)
    assert bit._answers_closed == 0
    assert bit.score("ie1") == 0.0


def test_a_tap_delayed_on_the_wire_still_lands_inside_the_slack():
    """Stamped in the window but delivered just after it, inside the slack."""
    bit = _connected()
    bit.actions()["start"][1](10.0)
    at = 10.0
    while at < _judged(4.25) + 0.03:
        at += TICK
        bit.fires(at)
    assert bit._answers_closed == 0
    bit.verb_handlers()["tap"]("ie1", [], _judged(3.875))
    bit._close_answers(at + 1.0)
    assert bit._earned["ie1"] == pytest.approx(0.5)


def test_a_tap_arriving_after_its_beat_closed_earns_nothing():
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit.fires(_when(5.0))
    assert bit._answers_closed == 1
    bit.verb_handlers()["tap"]("ie1", [], _judged(4.0))
    bit._close_answers(_when(30.0))
    assert "ie1" not in bit._earned


def test_tap_timing_takes_the_cue_horizon_off():
    """`at` is presentation time, a horizon after the tap's own moment. A tap
    dead on the beat arrives one horizon late and still earns full points."""
    bit = _connected()
    bit.cue_horizon = 0.06
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], _judged(4.0) + 0.06)
    bit._close_answers(_when(30.0))
    assert bit._earned["ie1"] == pytest.approx(1.0)


def test_taps_before_start_are_not_judged():
    bit = _connected()
    bit.verb_handlers()["tap"]("ie1", [], 5.0)
    _play(bit, [])
    assert bit.score("ie1") == 0.0


def test_a_scored_tap_still_blinks_the_shrooms():
    bit = _connected()
    bit.actions()["start"][1](10.0)
    assert _flashes(bit.verb_handlers()["tap"]("ie1", [], _when(4.0)))


def test_live_stack_a_tap_on_the_beat_scores_through_the_real_stack():
    """A real /game/tap, stamped on an answer beat, reaches the score with the
    GameServer's own cue horizon taken off."""
    pytest.importorskip("luxaeterna")
    from pathlib import Path

    from control.catalog import load_catalog
    from control.engine import GameServer
    from control.room_binding import RoomBindingRegistry
    from control.rooms import Room
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer, _Clock

    root = Path(__file__).resolve().parents[1]
    reg = BitRegistry.discover()
    clk = _Clock(100.0)
    catalog = load_catalog(root / "instruments")
    gs = GameServer({NAME: reg.bit_class(NAME)}, cue_horizon=0.06, clock=clk,
                    carried_instruments={
                        "tuneshroom": catalog.get("published",
                                                  "tuneshroom").instrument},
                    room_binding=RoomBindingRegistry())
    gs.room = Room(name="MIDITEST",
                   profile=load_terrarium_config("terrarium.toml")
                   .rooms["MIDITEST"].profile,
                   node_id="ROOM_MIDITEST_NODE")
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, horizon=0.06)
    gs.load_bit(NAME, config=reg.resolve_config(NAME, {}))
    gs.run()
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "ssss",
                   ["ie1", "sim", "1", "tuneshroom"])
    agent.poll()

    def step():
        clk.advance(TICK)
        agent.poll()
        gs.tick(TICK)

    assert gs.run_action("start") is None
    grid = gs.bit._seq
    while clk() < grid.time_at(4.0) + JUDGE_DELAY_S:
        step()
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 0.0, 80.0, 1],
                   timestamp=grid.time_at(4.0) + JUDGE_DELAY_S)
    agent.poll()
    while gs.bit._answers_closed < 1:
        step()
    assert gs.bit.score("ie1") == pytest.approx(1.0)


def test_live_stack_seats_devices_and_plays_through_the_room():
    """A real GameServer and DeviceLinkAgent in the MIDITEST Room: hellos
    seat the devices, a fourth shroom goes solo, and Start's notes reach the
    Room's music sink."""
    pytest.importorskip("luxaeterna")
    from pathlib import Path

    from control.catalog import load_catalog
    from control.engine import GameServer
    from control.room_binding import RoomBindingRegistry
    from control.rooms import Room
    from control.state import State
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer, _Clock

    root = Path(__file__).resolve().parents[1]
    reg = BitRegistry.discover()
    clk = _Clock(100.0)
    catalog = load_catalog(root / "instruments")
    carried = {name: catalog.get("published", name).instrument
               for name in ("devtower", "tuneshroom")}
    gs = GameServer({NAME: reg.bit_class(NAME)}, cue_horizon=0.06, clock=clk,
                    carried_instruments=carried,
                    room_binding=RoomBindingRegistry())
    gs.room = Room(name="MIDITEST",
                   profile=load_terrarium_config("terrarium.toml")
                   .rooms["MIDITEST"].profile,
                   node_id="ROOM_MIDITEST_NODE")
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, horizon=0.06)
    gs.load_bit(NAME, config=reg.resolve_config(NAME, {}))
    gs.run()
    assert gs.state is State.RUNNING

    devices =(("c1", "devtower1", "devtower"), ("c2", "ie1", "tuneshroom"),
               ("c3", "ie2", "tuneshroom"), ("c4", "ie3", "tuneshroom"),
               ("c5", "ie4", "tuneshroom"))
    for client, dev, instrument in devices:
        server.arrive(client)
        server.deliver(client, "/game/hello", "ssss",
                       [dev, "sim", "1", instrument])
        agent.poll()
    assert gs.bit.status()["tower"] == ["devtower1"]
    assert gs.bit.status()["shrooms"] == ["ie1", "ie2", "ie3"]
    assert gs.run_action("player1") is None
    assert [gs.bit.status()[f"player{n}"] for n in (1, 2, 3)] == [
        "ie2", "ie1", "ie3"]

    played = []
    gs.on_music_cue = lambda dev, status, d1, d2, when, tag: played.append(
        (status, d1, d2, tag))
    assert gs.run_action("start") is None
    for _ in range(int(BEAT_S * 8 / TICK)):
        clk.advance(TICK)
        agent.poll()
        gs.tick(TICK)
    assert any(s == 0xC0 for s, _d1, _d2, _tag in played)
    assert sum(1 for s, _d1, d2, tag in played
               if s == 0x90 and d2 > 0 and tag == "piano") > 3


def test_live_stack_starts_with_no_devices_connected():
    """Start needs no device at all: the Room's music sink gets the notes."""
    from control.engine import GameServer
    from control.room_binding import RoomBindingRegistry
    from control.rooms import Room

    reg = BitRegistry.discover()
    gs = GameServer({NAME: reg.bit_class(NAME)},
                    room_binding=RoomBindingRegistry())
    gs.room = Room(name="MIDITEST",
                   profile=load_terrarium_config("terrarium.toml")
                   .rooms["MIDITEST"].profile,
                   node_id="ROOM_MIDITEST_NODE")
    gs.load_bit(NAME, config=reg.resolve_config(NAME, {}))
    gs.run()
    played = []
    gs.on_music_cue = lambda dev, status, d1, d2, when, tag: played.append(
        (dev, status, tag))
    assert gs.run_action("start") is None
    assert any(s == 0x90 and tag == "piano" for _dev, s, tag in played)


def test_live_stack_paints_the_responders_on_the_towers_frame():
    """The beats reach devtower1's own 128 px frame through a real GameServer
    and DeviceLinkAgent: the responder of each beat lit, the bar left dark."""
    pytest.importorskip("luxaeterna")
    from pathlib import Path

    from control.catalog import load_catalog
    from control.engine import GameServer
    from control.room_binding import RoomBindingRegistry
    from control.rooms import Room
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer, _Clock

    root = Path(__file__).resolve().parents[1]
    reg = BitRegistry.discover()
    clk = _Clock(100.0)
    catalog = load_catalog(root / "instruments")
    gs = GameServer({NAME: reg.bit_class(NAME)}, cue_horizon=0.06, clock=clk,
                    carried_instruments={
                        "devtower": catalog.get("published",
                                                "devtower").instrument},
                    room_binding=RoomBindingRegistry())
    gs.room = Room(name="MIDITEST",
                   profile=load_terrarium_config("terrarium.toml")
                   .rooms["MIDITEST"].profile,
                   node_id="ROOM_MIDITEST_NODE")
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, horizon=0.06)
    gs.load_bit(NAME, config=reg.resolve_config(NAME, {}))
    gs.run()
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "ssss",
                   ["devtower1", "sim", "1", "devtower"])
    agent.poll()
    assert gs.bit.status()["tower"] == ["devtower1"]
    for _ in range(int(3.0 / TICK)):    # a new session's status glow settles
        clk.advance(TICK)
        agent.poll()
        gs.tick(TICK)
    seen = len(server.sent)
    assert gs.run_action("start") is None

    lit_order = []
    was = set()
    for _ in range(int(BEAT_S * 12 / TICK)):
        clk.advance(TICK)
        agent.poll()
        gs.tick(TICK)
        for _dev, msg in server.sent[seen:]:
            if msg["address"] != "/devtower1/leds":
                continue
            frame = bytes(msg["args"][0])
            assert len(frame) == 128 * 3
            assert not any(frame[:32 * 3])
            lit = set()
            for r in range(4):
                lo = (32 + r * 24) * 3
                pixels = {frame[i:i + 3]
                          for i in range(lo, lo + 24 * 3, 3)}
                assert len(pixels) == 1, (r, pixels)
                if pixels != {bytes(3)}:
                    lit.add(r)
            lit_order += sorted(lit - was)
            was = lit
        seen = len(server.sent)
    # beats 0, 2, 3 of the first measure, 8, 9, (9.5), 10, 11 of the third;
    # 9.5 restarts the second responder, already lit.
    assert lit_order == [0, 2, 3, 0, 1, 2, 3]


def test_live_stack_a_tap_blinks_both_tuneshrooms_frames():
    """A real /game/tap from ie1 reaches both tuneshrooms' 12 px frames, with
    or without Start: purple, fading out, then dark."""
    pytest.importorskip("luxaeterna")
    from pathlib import Path

    from control.catalog import load_catalog
    from control.engine import GameServer
    from control.room_binding import RoomBindingRegistry
    from control.rooms import Room
    from devicelink.agent import DeviceLinkAgent
    from tests.test_devicelink_agent import FakeServer, _Clock

    root = Path(__file__).resolve().parents[1]
    reg = BitRegistry.discover()
    clk = _Clock(100.0)
    catalog = load_catalog(root / "instruments")
    gs = GameServer({NAME: reg.bit_class(NAME)}, cue_horizon=0.06, clock=clk,
                    carried_instruments={
                        "tuneshroom": catalog.get("published",
                                                  "tuneshroom").instrument},
                    room_binding=RoomBindingRegistry())
    gs.room = Room(name="MIDITEST",
                   profile=load_terrarium_config("terrarium.toml")
                   .rooms["MIDITEST"].profile,
                   node_id="ROOM_MIDITEST_NODE")
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, horizon=0.06)
    gs.load_bit(NAME, config=reg.resolve_config(NAME, {}))
    gs.run()
    for client, dev in (("c1", "ie1"), ("c2", "ie2")):
        server.arrive(client)
        server.deliver(client, "/game/hello", "ssss",
                       [dev, "sim", "1", "tuneshroom"])
        agent.poll()
    assert gs.bit.status()["shrooms"] == ["ie1", "ie2"]

    def run(seconds):
        for _ in range(int(seconds / TICK)):
            clk.advance(TICK)
            agent.poll()
            gs.tick(TICK)

    run(3.0)                            # a new session's status glow settles
    seen = len(server.sent)
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 0.0, 80.0, 1],
                   timestamp=clk())
    run(0.5)
    frames = {"ie1": [], "ie2": []}
    for _dev, msg in server.sent[seen:]:
        for dev, out in frames.items():
            if msg["address"] == f"/{dev}/leds":
                out.append(bytes(msg["args"][0]))
    for dev, out in frames.items():
        assert out, dev
        assert all(len(f) == 12 * 3 for f in out)
        assert all(len({f[i:i + 3] for i in range(0, 36, 3)}) == 1 for f in out)
        assert out[0][:3] == bytes((128, 0, 255))
        assert out[-1] == bytes(36)


def _answer_lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines()
            if line.startswith(("answer ", "====== Judging"))]


def test_judging_prints_each_correct_answer_beat_at_its_o2_time(capsys):
    bit = _play(_connected(), [], until_beat=AFTER_ONE_PASS)
    assert _answer_lines(capsys) == [
        line for measure, beats in ((2, (4, 6, 7)),
                                    (4, (12, 13, 13.5, 14, 15)))
        for line in (f"====== Judging {measure} Start ======", *(
            f"answer measure {measure} beat {b % 4 + 1:g}: "
            f"o2 t={bit._seq.time_at(b) + JUDGE_DELAY_S:.3f} s" for b in beats))]


def test_an_answer_beat_prints_only_once_its_time_arrives(capsys):
    """Lines come beat by beat, not all together when judging starts."""
    bit = _play(_connected(), [], until_beat=6.5)
    lines = _answer_lines(capsys)
    assert [line.split(":")[0] for line in lines] == [
        "====== Judging 2 Start ======",
        "answer measure 2 beat 1",
        "answer measure 2 beat 3"]
    bit.fires(_judged(7.0) + TICK)
    assert _answer_lines(capsys) == [
        f"answer measure 2 beat 4: o2 t={_judged(7.0):.3f} s"]


def test_an_answer_beats_print_waits_out_the_cue_horizon(capsys):
    """`at` is a horizon ahead of the real time the beat is printed at."""
    bit = _connected()
    bit.cue_horizon = 0.06
    bit.actions()["start"][1](10.0)
    bit.fires(_judged(4.0) + 0.05)
    assert not [line for line in _answer_lines(capsys)
                if line.startswith("answer")]
    bit.fires(_judged(4.0) + 0.07)
    assert _answer_lines(capsys)[-1] == (
        f"answer measure 2 beat 1: o2 t={_judged(4.0):.3f} s")


def test_answer_beats_keep_printing_on_later_passes(capsys):
    _play(_connected(), [], until_beat=LOOP_BEATS + 8.0)
    lines = _answer_lines(capsys)
    assert [line.split(":")[0] for line in lines
            if "measure 6" in line] == [
        "answer measure 6 beat 1", "answer measure 6 beat 3",
        "answer measure 6 beat 4"]


def _bar_paint(cues, dev="devtower1"):
    """pixel -> W level, for the bar pixels painted on `dev`."""
    return {c.pixel: c.rgb[3] for c in cues
            if isinstance(c, PixelFlashCue) and c.dev == dev and c.pixel < 32}


def _judged_tap(bit, beat, dev="ie1", error=0.0):
    return bit.verb_handlers()["tap"](dev, [], _judged(beat + error))


def test_a_tap_earning_over_a_tenth_adds_one_to_progress():
    bit = _connected()
    _start(bit)
    _judged_tap(bit, 4.0)
    assert bit._progress == 1
    assert bit.status()["progress"] == "1/50"


@pytest.mark.parametrize("points,adds", [(0.11, 1), (0.09, 0)])
def test_progress_needs_more_than_a_tenth_of_a_point(points, adds):
    bit = _connected()
    _start(bit)
    _judged_tap(bit, 4.0, error=-(1 - points) * 0.25)
    assert bit._open_taps[4.0]["ie1"] == pytest.approx(points)
    assert bit._progress == adds


def test_a_tap_that_takes_no_beat_adds_no_progress():
    bit = _connected()
    _start(bit)
    _judged_tap(bit, 1.0)
    assert bit._progress == 0


def test_a_second_tap_on_a_taken_beat_adds_no_progress():
    bit = _connected()
    _start(bit)
    _judged_tap(bit, 4.0)
    _judged_tap(bit, 4.0, error=0.05)
    assert bit._progress == 1


def test_both_players_add_to_one_shared_progress():
    bit = _connected()
    _start(bit)
    _judged_tap(bit, 4.0, dev="ie1")
    _judged_tap(bit, 4.0, dev="ie2")
    assert bit._progress == 2


def test_a_tap_before_start_adds_no_progress():
    bit = _connected()
    bit.verb_handlers()["tap"]("ie1", [], _judged(4.0))
    assert bit._progress == 0


def test_progress_stops_at_fifty_and_paints_no_more():
    bit = _connected()
    _start(bit)
    bit._progress = 49
    assert _bar_paint(_judged_tap(bit, 4.0))
    assert bit._progress == 50
    assert not _bar_paint(_judged_tap(bit, 6.0))
    assert bit._progress == 50


@pytest.mark.parametrize("progress,lit", [
    (0, {}),
    (1, {0: 82, 1: 82}),
    (25, {px: 255 for px in range(16)}),
    (50, {px: 255 for px in range(32)}),
])
def test_the_bar_fills_from_the_bottom_in_two_led_lights(progress, lit):
    from bits.beats.beats_bit import bar_light_value
    painted = {px: bar_light_value(px // 2, progress) for px in range(32)}
    assert {px: v for px, v in painted.items() if v} == lit


def test_a_scoring_tap_paints_the_bar_at_once_and_blinks_too():
    bit = _connected()
    _start(bit)
    cues = _judged_tap(bit, 4.0)
    assert _bar_paint(cues) == {0: 82, 1: 82}
    assert {c.dev for c in cues} == {"devtower1", "ie1", "ie2"}
    assert {c.when for c in cues if c.dev == "devtower1"} == {_judged(4.0)}


def test_a_tap_that_scores_nothing_does_not_paint_the_bar():
    bit = _connected()
    _start(bit)
    assert not _bar_paint(_judged_tap(bit, 1.0))


def test_fires_renews_the_bar_before_its_paint_lapses():
    bit = _connected()
    _start(bit)
    bit._progress = 25
    at, renewals = 10.0, []
    while at < 13.0:
        at += TICK
        if _bar_paint(bit.fires(at)):
            renewals.append(at)
    assert len(renewals) >= 5
    gaps = [b - a for a, b in zip(renewals, renewals[1:])]
    assert max(gaps) < 1.0        # BAR_HOLD_S: the paint never goes out


def test_a_tower_joining_late_gets_the_bar_on_the_next_renewal():
    bit = _bit()
    bit.on_join("ie1", "shroom")
    _start(bit)
    _judged_tap(bit, 4.0)
    bit.fires(_judged(4.0))
    bit.on_join("devtower1", "tower")
    assert _bar_paint(bit.fires(_judged(4.0) + 0.6)) == {0: 82, 1: 82}
