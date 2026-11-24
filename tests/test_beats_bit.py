"""BeatsBit: a devtower and up to three tuneshrooms connect, then the Console's Start
button loops music1's four tracks together from the MIDITEST Room (bits/beats/)."""
import pytest

from control.bit_registry import BitRegistry
from bits.beats.beats_bit import BEATS, BEATS_PER_MEASURE, MUSICS
from control.cues import ROOM, MusicCue, PixelFlashCue, PlayCue
from control.role_config import (validate_role_declarations,
                                 validate_ugen_manifest)
from control.terrarium_config import load_terrarium_config

NAME = "BeatsBit"
TICK = 1.0 / 44
BEAT_S = 60.0 / 130.0
LOOP_BEATS = 16
TAP_RGB = (64, 0, 0)
RESPONDER_FADE_S = 0.25
LIGHT_DELAY_S = 0.175


def _bit():
    reg = BitRegistry.discover()
    assert NAME in reg.packages, reg.errors
    return reg.bit_class(NAME)(reg.resolve_config(NAME, {}))


def _connected(beats="beats1"):
    bit = _bit()
    bit.on_join("devtower1", "tower")
    bit.on_join("ie1", "shroom")
    bit.on_join("ie2", "shroom")
    assert bit.actions()["beats"][1](9.0, beats) == []
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
    for clips in BEATS.values():
        for asset in clips.values():
            assert cfg.asset_path(asset).is_file(), asset
    assert cfg.start.when == "immediate"
    row = next(r for r in reg.list_view(include_hidden=False)
               if r["name"] == NAME)
    assert row["hidden"] is False


def test_role_table_validates():
    validate_role_declarations(_bit().role_table)


def test_console_dropdowns_choose_each_player_music_and_beats_then_start():
    bit = _bit()
    bit.on_join("ie1", "shroom")
    actions = bit.actions()
    assert {name: entry[0] for name, entry in actions.items()} == {
        "player1": "Player 1", "player2": "Player 2", "player3": "Player 3",
        "music": "Music", "beats": "Beats", "start": "Start"}
    assert {name: tuple(entry[2:]) for name, entry in actions.items()} == {
        "player1": (["ie1"], "ie1"), "player2": (["ie1"], None),
        "player3": (["ie1"], None), "music": (["music1"], "music1"),
        "beats": (["beats1", "beats2", "beats3", "beats4"], "beats1"),
        "start": ()}


def _three_seated():
    bit = _bit()
    for dev in ("ie3", "ie1", "ie2"):       # connection order, not id order
        bit.on_join(dev, "shroom")
    return bit


def _pick(bit, player, dev, at=9.0):
    return bit.actions()[f"player{player}"][1](at, dev)


def _players(bit):
    status = bit.status()
    return [status.get(f"player{n}") for n in (1, 2, 3)]


def _blinked(cues):
    return {c.dev for c in cues if isinstance(c, PixelFlashCue)}


def test_players_follow_connection_order_until_chosen():
    bit = _three_seated()
    assert _players(bit) == ["ie3", "ie1", "ie2"]
    assert [bit.actions()[f"player{n}"][3] for n in (1, 2, 3)] == [
        "ie3", "ie1", "ie2"]
    assert bit.actions()["player1"][2] == ["ie3", "ie1", "ie2"]
    assert _blinked(bit.verb_handlers()["tap"]("ie3", [], 9.0)) == {
        "ie1", "ie2", "ie3"}                # player1's tap blinks them all


def test_picking_a_players_shroom_swaps_with_the_other_player():
    bit = _three_seated()
    assert _pick(bit, 1, "ie2") == []       # ie2 was player3
    assert _players(bit) == ["ie2", "ie1", "ie3"]
    assert bit.actions()["player1"][3] == "ie2"


def test_picking_a_players_own_shroom_changes_nothing():
    bit = _three_seated()
    assert _pick(bit, 2, "ie1") == []
    assert _players(bit) == ["ie3", "ie1", "ie2"]


def test_an_empty_player_takes_the_picked_shroom():
    bit = _bit()
    bit.on_join("ie1", "shroom")
    bit.on_join("ie2", "shroom")
    assert _players(bit) == ["ie1", "ie2", None]
    _pick(bit, 3, "ie1")
    assert _players(bit) == [None, "ie2", "ie1"]
    assert bit.actions()["player1"][3] is None


def test_picking_a_shroom_not_connected_is_refused():
    assert _pick(_bit(), 1, "ie1") == "ie1 is not connected"



def test_a_shroom_that_is_no_player_neither_taps_nor_blinks_until_chosen():
    bit = _three_seated()
    bit.on_join("ie4", "shroom")            # every player already taken
    tap = bit.verb_handlers()["tap"]
    assert tap("ie4", [], 9.0) == []
    _pick(bit, 3, "ie4")                    # ie2 -> ie4, who was no player
    assert _players(bit) == ["ie3", "ie1", "ie4"]
    assert tap("ie2", [], 9.0) == []
    assert "score ie4" in bit.status() and "score ie2" not in bit.status()
    _pick(bit, 1, "ie4")                    # player1 takes ie4
    assert _blinked(tap("ie4", [], 9.0)) == set(_players(bit))


def test_a_reconnect_keeps_a_players_place():
    bit = _three_seated()
    _pick(bit, 1, "ie1")
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
    _pick(bit, 1, "ie1")
    tap = bit.verb_handlers()["tap"]
    tap("ie1", [], 9.0)
    assert "tap player1 (ie1): received" in capsys.readouterr().out
    for dev in ("ie3", "ie2"):              # player2's, player3's: unprinted
        tap(dev, [], 9.0)
        assert capsys.readouterr().out == ""
    _start(bit)
    tap("ie1", [], 10.0)
    assert "tap player1 (ie1): at measure 1" in capsys.readouterr().out


def test_start_fixes_the_players():
    bit = _three_seated()
    _start(bit)
    assert _pick(bit, 1, "ie1", at=11.0) == "already started"
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
    assert (cues[0].status, cues[0].data1, cues[0].when) == (0xC0, 4, 10.0)
    ons = _note_ons(cues)
    assert ons and all(c.when == 10.0 for c in ons[:3])


# -- the music choice ------------------------------------------------------

# (asset, its part): each a 32-beat loop whose file ends on a note at beat 32.
MUSIC1_PARTS = {"sub_808": 2, "abel_bass": 3, "sine_keys": 0,
                "thicket_kit": 1}
MUSIC1_LOOP = 32


def _music(bit, at=9.0, name="music1"):
    return bit.actions()["music"][1](at, name)


def test_the_music_dropdown_offers_music1_the_only_music():
    bit = _connected()
    assert bit.actions()["music"][2:] == (["music1"], "music1")
    assert _music(bit) == []
    assert _music(bit, name="music9") == "unknown music 'music9'"


def test_start_fixes_the_music():
    bit = _connected()
    _start(bit)
    assert _music(bit, 11.0) == "already started"
    assert bit.status()["music"] == "music1"


def test_start_plays_every_music1_track_together():
    bit = _connected()
    ons = _note_ons(_start(bit))
    assert {c.tag for c in ons if c.when == 10.0} == set(MUSIC1_PARTS)
    assert bit.status()["tracks"] == "4/4"


def test_scoring_brings_in_no_further_track():
    bit = _connected()
    _start(bit)
    _judged_tap(bit, 4.0)
    assert bit._progress == 1
    assert bit.status()["tracks"] == "4/4"


def test_music1_plays_each_track_on_its_part():
    bit = _connected()
    cues = _run(bit, _start(bit), 10.0 + 2 * BEAT_S)
    for c in _note_ons(cues):
        assert c.status == 0x90 | MUSIC1_PARTS[c.tag], c.tag


def test_music1_loops_each_track_on_32_beats():
    """Every track's beat-0 note comes back each loop, in step, never on the
    36 or 40 their files' trailing beat-32 notes would round up to."""
    bit = _connected()
    end = 10.0 + 3 * MUSIC1_LOOP * BEAT_S - TICK
    cues = _run(bit, _start(bit), end)
    for tag in MUSIC1_PARTS:
        first = next(c.data1 for c in _note_ons(cues) if c.tag == tag)
        downbeats = sorted({round((c.when - 10.0) / BEAT_S, 6)
                            for c in _note_ons(cues)
                            if c.tag == tag and c.data1 == first
                            and round((c.when - 10.0) / BEAT_S, 6)
                            % MUSIC1_LOOP == 0 and c.when < end})
        assert downbeats == [0, 32, 64], tag


def test_start_restores_every_melodic_program():
    cues = _start(_connected())
    programs = {(c.status, c.data1) for c in cues
                if isinstance(c, MusicCue) and c.status & 0xF0 == 0xC0}
    assert programs == {(0xC0, 4), (0xC2, 38), (0xC3, 39)}


def test_the_room_declares_one_part_per_program_and_one_drum_kit():
    _light, ugen = _bit().room_manifests()
    validate_ugen_manifest(ugen, "BeatsBit")
    assert [i.get("program", "drums" if i.get("drums") else None)
            for i in ugen["instruments"]] == [4, "drums", 38, 39]


def test_start_twice_refused_and_restarts_nothing():
    bit = _connected()
    _start(bit)
    assert _start(bit, 11.0) == "already started"


def test_status_tracks_devices_and_playing():
    bit = _connected()
    assert bit.status() == {"music": "music1", "beats": "beats1",
                            "state": "waiting",
                            "tower": ["devtower1"],
                            "shrooms": ["ie1", "ie2"],
                            "tracks": "0/4",
                            "light delay": "+175.0 ms",
                            "player1": "ie1", "player2": "ie2",
                            "progress": "0/50",
                            "score ie1": 0.0, "score ie2": 0.0,
                            "rating ie1": "GOOD 0 BAD 0 MISS 0",
                            "rating ie2": "GOOD 0 BAD 0 MISS 0"}
    _start(bit)
    assert bit.status()["state"] == "playing"


def test_music1_loops_without_duplicate_notes():
    bit = _connected()
    cues = _start(bit)
    at = 10.0
    end = 10.0 + 2 * MUSIC1_LOOP * BEAT_S
    while at < end:
        at += TICK
        cues += bit.fires(at)
    ons = [(round((c.when - 10.0) / BEAT_S, 6), c.tag, c.data1)
           for c in _note_ons(cues)]
    ons = [(b, t, k) for b, t, k in ons if b < 2 * MUSIC1_LOOP]
    assert len(ons) == len(set(ons))
    first = sorted((b, t, k) for b, t, k in ons if b < MUSIC1_LOOP)
    second = sorted((b - MUSIC1_LOOP, t, k) for b, t, k in ons
                    if b >= MUSIC1_LOOP)
    assert first and first == second


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
        if sum(c.rgb) > level.get(r, 0):
            out.append((c.when, r))
        level[r] = sum(c.rgb)
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


def test_the_light_runs_behind_the_music_and_the_note_stays_on_the_grid():
    """Beat 2.5's note keeps its grid time; only its light is held back."""
    bit = _connected()
    cues = _run(bit, _start(bit), 10.0 + 1.5 * BEAT_S + LIGHT_DELAY_S + TICK)
    beat = 10.0 + 1.5 * BEAT_S
    assert any(c.when == pytest.approx(beat) for c in _note_ons(cues))
    assert _strikes(cues)[-1][0] == pytest.approx(beat + LIGHT_DELAY_S, abs=TICK)


def test_tower_lights_nothing_with_no_tower_connected():
    bit = _bit()
    bit.on_join("ie1", "shroom")
    cues = _run(bit, _start(bit), 10.0 + LOOP_BEATS * BEAT_S)
    assert not [c for c in cues if isinstance(c, PixelFlashCue)]


def test_each_beat_lights_the_responder_of_its_place_in_the_measure():
    """beats1's taps on 1, 2.5 and 4 of measure 1 only, on every pass of
    the loop; measures 2 to 4 are the answer, and dark."""
    bit = _connected()
    until = 10.0 + 2 * LOOP_BEATS * BEAT_S + LIGHT_DELAY_S - TICK
    struck = _strikes(_run(bit, _start(bit), until))
    one_pass = [(0, 0), (1.5, 1), (3, 3)]
    expected = one_pass + [(LOOP_BEATS + b, r) for b, r in one_pass]
    assert [r for _when, r in struck] == [r for _b, r in expected]
    for (when, _r), (beat, _) in zip(struck, expected):
        assert 0 <= when - (10.0 + beat * BEAT_S + LIGHT_DELAY_S) <= TICK + 1e-9


def test_a_beat_lights_on_its_tick_not_ahead_of_it():
    """The music's notes go out on the Sequencer's look-ahead; a beat's light
    waits for the beat. Beat 2.5 is the first after beat 1's Start."""
    bit = _connected()
    _start(bit)
    before = 10.0 + 1.5 * BEAT_S + LIGHT_DELAY_S - 0.001
    assert not [c for c in bit.fires(before) if isinstance(c, PixelFlashCue)
                and 32 + 24 <= c.pixel]
    lit = [c for c in bit.fires(10.0 + 1.5 * BEAT_S + LIGHT_DELAY_S)
           if isinstance(c, PixelFlashCue) and c.pixel >= 32 + 24]
    assert sorted(c.pixel for c in lit) == list(range(56, 80))


def test_a_responder_fades_to_dark_and_lapses_at_the_fades_end():
    bit = _connected()
    cues = _start(bit)
    at = 10.0
    # The next beat lights another.
    while at < 10.0 + 1.5 * BEAT_S + LIGHT_DELAY_S - TICK:
        at += TICK
        cues += bit.fires(at)
    first = sorted((c for c in cues if isinstance(c, PixelFlashCue)
                    and c.pixel == 32), key=lambda c: c.when)
    reds = [c.rgb[0] for c in first]
    assert reds == sorted(reds, reverse=True)
    assert reds[0] >= TAP_RGB[0] * (1 - TICK / RESPONDER_FADE_S) - 1
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


def _first_colour(bit, r, until_beat):
    """Responder r's first colour painted on the way to `until_beat`."""
    cues = _run(bit, _start(bit), 10.0 + until_beat * BEAT_S + LIGHT_DELAY_S)
    return next(c.rgb for c in cues if isinstance(c, PixelFlashCue)
                and c.pixel == 32 + 24 * r)


@pytest.mark.parametrize("beats, lit", [
    ("beats1", (1, 0, 0)),           # a tap: red
    ("beats2", (1, 1, 0)),           # a tap and a swing: red + green
    ("beats3", (1, 1, 1)),           # a tap, a swing and a squeeze
])
def test_a_responder_lights_its_beats_gestures_mixed(beats, lit):
    """Beat 1 of the call asks for every gesture its beats has, each at the
    same level; the tick can be just past the beat, so not quite at 64."""
    rgb = _first_colour(_connected(beats), 0, 0.1)
    level = max(rgb[:3])
    assert 64 - 3 <= level <= 64
    assert rgb == tuple(level * on for on in lit) + (0,)


def test_a_beat_fading_under_another_gesture_mixes_with_it():
    """A squeeze's fade half done, then a swing on the same responder: the
    green comes in full and the blue carries on fading, not restarted."""
    bit = _connected()
    bit._responders[(0, "squeeze")] = 10.0
    bit._responders[(0, "swing")] = 10.0 + RESPONDER_FADE_S / 2
    rgb = [c.rgb for c in bit._responder_steps(10.0 + RESPONDER_FADE_S / 2)
           if c.pixel == 32][0]
    assert rgb[:3] == (0, 64, 32)
    later = [c for c in bit._responder_steps(10.0 + RESPONDER_FADE_S)
             if c.pixel == 32]
    assert later[0].rgb[:3] == (0, 32, 0)       # the squeeze's done


def test_a_beat_on_a_fading_responder_restarts_it_from_full():
    """Beats 2 and 2.5 of beats4's taps land on the second responder, a
    quarter of a second apart, inside its quarter-second fade."""
    bit = _connected("beats4")
    cues = _run(bit, _start(bit), 10.0 + 1.75 * BEAT_S + LIGHT_DELAY_S)
    assert [r for _w, r in _strikes(cues)][-2:] == [1, 1]


def _tap(bit, dev, at):
    return bit.verb_handlers()["tap"](dev, [], at)


def _flashes(cues):
    return [c for c in cues if isinstance(c, PixelFlashCue)]


def test_shroom_role_uses_tap():
    assert _bit().role_table.roles["shroom"].uses == ["tap", "swing",
                                                      "squeeze"]


def test_shroom_role_declares_the_guzheng_and_chime_samples():
    assert _bit().role_table.roles["shroom"].samples == ["guzheng", "chime"]


def test_a_tap_blinks_every_shroom_at_full_on_all_its_leds():
    cues = _flashes(_tap(_connected(), "ie1", 10.0))
    assert {(c.dev, c.pixel) for c in cues} \
        == {(dev, px) for dev in ("ie1", "ie2") for px in range(12)}
    assert all(c.rgb == (128, 0, 255, 0) and c.when == 10.0
               and c.duration == 0.2 for c in cues)


def test_the_blink_fades_to_dark_and_lapses_at_the_fades_end():
    bit = _connected()
    _tap(bit, "ie1", 10.0)
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
    again = _flashes(_tap(bit, "ie1", 10.1))
    assert all(c.rgb == (128, 0, 255, 0) and c.duration == 0.2 for c in again)
    assert [c.rgb[2] for c in _flashes(bit.fires(10.1 + TICK))
            if (c.dev, c.pixel) == ("ie1", 0)][0] < 255


def _swing(bit, dev, at):
    return bit.verb_handlers()["swing"](dev, [dev, 2.0, 1], at)


def test_only_player1s_tap_blinks():
    bit = _connected()                      # player1 ie1, player2 ie2
    assert _flashes(_tap(bit, "ie2", 10.0)) == []
    assert bit.fires(10.0 + TICK) == []
    assert _blinked(_tap(bit, "ie1", 10.5)) == {"ie1", "ie2"}


def test_only_player2s_swing_blinks_every_player_yellow():
    bit = _connected()
    assert _swing(bit, "ie1", 10.0) == []
    assert bit.fires(10.0 + TICK) == []
    cues = _swing(bit, "ie2", 10.5)
    assert {(c.dev, c.pixel) for c in cues} \
        == {(dev, px) for dev in ("ie1", "ie2") for px in range(12)}
    assert all(c.rgb == (255, 200, 0, 0) and c.when == 10.5
               and c.duration == 0.2 for c in cues)


@pytest.mark.parametrize("started", [False, True])
def test_a_players_squeeze_is_printed_and_plays_chime_on_its_shroom(started,
                                                                    capsys):
    bit = _connected()
    if started:
        _start(bit)
    capsys.readouterr()
    squeeze = bit.verb_handlers()["squeeze"]
    assert squeeze("ie2", ["ie2", 10.0], 10.5) == [
        PlayCue("ie2", "chime", "key=75")]
    assert "squeeze player2 (ie2)" in capsys.readouterr().out
    assert squeeze("ie9", ["ie9", 10.0], 10.5) == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("dev", ["ie1", "ie9"])  # a player, and not one
def test_a_tilt_is_accepted_quietly(dev, capsys):
    bit = _connected()
    capsys.readouterr()
    assert bit.verb_handlers()["tilt"](dev, [dev, 12.5, 10.0], 10.5) == []
    assert capsys.readouterr().out == ""


def test_player2s_tap_still_scores_without_blinking():
    bit = _connected()
    _start(bit)
    cues = _judged_tap(bit, 4.0, dev="ie2")
    assert not _flashes([c for c in cues if c.dev != "devtower1"])
    assert bit._progress == 1               # judged: a dead-on tap scores


@pytest.mark.parametrize("dev", ["devtower1", "other"])
def test_a_tap_from_anything_but_a_shroom_blinks_nothing(dev):
    bit = _connected()
    assert _tap(bit, dev, 10.0) == []
    assert bit.fires(10.0 + TICK) == []


def test_a_tap_leaves_the_tower_dark_and_the_music_playing():
    bit = _connected()
    _start(bit)
    assert not [c for c in _tap(bit, "ie1", 10.1) if c.dev == "devtower1"]
    assert _note_ons(_run(bit, [], 10.0 + 8 * BEAT_S))


# beats1's taps on 0, 1.5 and 3: the call in measure 1, answered in each of
# measures 2, 3 and 4.
ANSWERS = (4, 5.5, 7, 8, 9.5, 11, 12, 13.5, 15)
CALLS = (0, 1.5, 3)
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
    """Only the first answer beat is tapped; the others earn 0."""
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
    some, GOOD; just past the moved edge is BAD, the beat taken for nothing."""
    inside = _connected()
    inside.actions()["start"][1](10.0)
    inside.verb_handlers()["tap"]("ie1", [], _judged(4.25) + 0.010)
    assert inside._open_taps[(4.0, "tap")]["ie1"] > 0.0
    assert inside._open_ratings[(4.0, "tap")]["ie1"] == "GOOD"

    outside = _connected()
    outside.actions()["start"][1](10.0)
    outside.verb_handlers()["tap"]("ie1", [], _judged(4.25) + 0.030)
    assert outside._open_taps[(4.0, "tap")]["ie1"] == 0.0
    assert outside._open_ratings[(4.0, "tap")]["ie1"] == "BAD"


def test_a_tap_200_ms_after_the_beat_is_dead_on():
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], _when(4.0) + 0.200)
    assert bit._open_taps[(4.0, "tap")]["ie1"] == pytest.approx(1.0)


def test_a_tap_on_the_grid_beat_is_now_too_early_to_be_good(capsys):
    """200 ms (0.43 beat at 130 BPM) is wider than the GOOD window's early
    half, but inside the BAD window's."""
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], _when(4.0))
    assert bit._open_taps[(4.0, "tap")]["ie1"] == 0.0
    assert bit._open_ratings[(4.0, "tap")]["ie1"] == "BAD"
    assert "measure 2 beat 1, error -0.433 beats, BAD" in capsys.readouterr().out


def test_the_window_opens_a_quarter_beat_before_the_slid_moment():
    inside = _connected()
    inside.actions()["start"][1](10.0)
    inside.verb_handlers()["tap"]("ie1", [], _judged(3.75) + 0.005)
    assert inside._open_taps[(4.0, "tap")]["ie1"] > 0.0
    assert inside._open_ratings[(4.0, "tap")]["ie1"] == "GOOD"

    outside = _connected()
    outside.actions()["start"][1](10.0)
    outside.verb_handlers()["tap"]("ie1", [], _judged(3.75) - 0.005)
    assert outside._open_ratings[(4.0, "tap")]["ie1"] == "BAD"


def test_the_grace_does_not_widen_the_early_edge():
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit.verb_handlers()["tap"]("ie1", [], _judged(3.75) - 0.010)
    assert bit._open_taps[(4.0, "tap")]["ie1"] == 0.0
    assert bit._open_ratings[(4.0, "tap")]["ie1"] == "BAD"


def test_the_grace_pushes_the_beats_closing_out_too():
    """A tap at the moved BAD edge keeps its whole slack to arrive in."""
    bit = _connected()
    bit.actions()["start"][1](10.0)
    bit._close_answers(_judged(4.5) + 0.060)
    assert bit._answers_closed == 0
    bit._close_answers(_judged(4.5) + 0.071)
    assert bit._answers_closed == 1


@pytest.mark.parametrize("error,rating", [
    (0.0, "GOOD"), (-0.24, "GOOD"), (-0.26, "BAD"), (-0.49, "BAD"),
    (GRACE_BEATS + 0.24, "GOOD"), (GRACE_BEATS + 0.26, "BAD"),
    (GRACE_BEATS + 0.49, "BAD"),
])
def test_a_tap_inside_the_half_beat_is_good_or_bad(error, rating):
    """The first answer beat tapped; the others are MISS."""
    bit = _play(_connected(), [("ie1", 4 + error)])
    expected = {"GOOD": 0, "BAD": 0, "MISS": len(ANSWERS) - 1}
    expected[rating] += 1
    assert bit.ratings("ie1") == expected


@pytest.mark.parametrize("error", [-0.51, GRACE_BEATS + 0.51])
def test_a_tap_past_the_half_beat_takes_no_beat(error, capsys):
    bit = _play(_connected(), [("ie1", 4 + error)])
    assert "took no beat" in capsys.readouterr().out
    assert bit.ratings("ie1") == {"GOOD": 0, "BAD": 0, "MISS": len(ANSWERS)}


def test_every_beat_with_no_tap_is_a_miss_for_every_player(capsys):
    bit = _play(_connected(), [])
    assert bit.ratings("ie1") == {"GOOD": 0, "BAD": 0, "MISS": len(ANSWERS)}
    assert bit.ratings("ie2") == {"GOOD": 0, "BAD": 0, "MISS": len(ANSWERS)}
    assert "tap player1 (ie1): measure 2 beat 1, MISS" in capsys.readouterr().out


def test_each_beat_is_rated_once_per_player_as_it_closes():
    taps = ([("ie1", b) for b in ANSWERS[:4]]          # GOOD
            + [("ie1", b - 0.4) for b in ANSWERS[4:6]]  # BAD
            + [("ie2", ANSWERS[0])])
    bit = _play(_connected(), taps)
    assert bit.ratings("ie1") == {"GOOD": 4, "BAD": 2, "MISS": 3}
    assert bit.ratings("ie2") == {"GOOD": 1, "BAD": 0, "MISS": 8}
    assert bit.status()["rating ie1"] == "GOOD 4 BAD 2 MISS 3"


def test_a_bad_tap_earns_nothing_and_adds_no_progress():
    bit = _play(_connected(), [("ie1", 4 - 0.4)])
    assert bit.score("ie1") == 0.0
    assert bit._progress == 0


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
    assert fast._earned["ie1"] == 0.0
    assert fast.ratings("ie1")["BAD"] == 1


@pytest.mark.parametrize("beat", CALLS + (1, 2))
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
    """beats4's taps answer on 2 and 2.5 of measure 4."""
    bit = _play(_connected("beats4"), [("ie1", 13.0), ("ie1", 13.5)])
    assert bit.ratings("ie1")["GOOD"] == 2
    assert bit.score("ie1") == pytest.approx(2 / len(bit._answers))


def test_the_answer_repeats_on_every_pass_of_the_loop():
    answers = [("ie1", b + p * LOOP_BEATS) for p in (0, 1) for b in ANSWERS]
    bit = _play(_connected(), answers, until_beat=LOOP_BEATS + AFTER_ONE_PASS)
    assert bit.score("ie1") == pytest.approx(1.0)
    assert bit._answers_closed == 2 * len(ANSWERS)


def test_score_is_the_mean_over_closed_beats_only():
    """Mid-way through the first answer measure, only the closed beats count."""
    bit = _play(_connected(), [("ie1", 4.0)], until_beat=5.2)
    assert bit._answers_closed == 1
    assert bit.score("ie1") == pytest.approx(1.0)
    at = _when(5.2)
    while at < _when(8.2):
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
    bit.fires(_when(5.2))
    assert bit._answers_closed == 1
    bit.verb_handlers()["tap"]("ie1", [], _judged(4.0))
    bit._close_answers(_when(30.0))
    assert "ie1" not in bit._earned


@pytest.mark.parametrize("started", [False, True])
def test_a_tap_plays_guzheng_on_its_own_shroom_and_nothing_in_the_room(started):
    bit = _connected()
    if started:
        _start(bit)
    for dev in ("ie1", "ie2"):
        # In the call, and in the answer: GOOD on beat 2.5 of measure 2 once
        # started, so the sine keys' note there, 72.
        for beat, key in ((1.0, 69), (5.3, 72 if started else 69)):
            cues = _tap(bit, dev, _judged(beat))
            assert [c for c in cues if isinstance(c, PlayCue)] == [
                PlayCue(dev, "guzheng", f"key={key}")]
            assert not [c for c in cues if isinstance(c, MusicCue)]


@pytest.mark.parametrize("error, key", [
    (0.0, "key=60"),        # GOOD: the sine keys' C4 on beat 4
    (-0.4, "key=49"),       # BAD
])
def test_a_judged_tap_plays_the_sine_keys_note_when_good_and_49_when_bad(
        error, key):
    bit = _connected()
    _start(bit)
    cues = _tap(bit, "ie2", _judged(4.0 + error))
    assert bit._open_ratings[(4.0, "tap")]["ie2"] == ("GOOD" if error == 0.0 else "BAD")
    assert [c for c in cues if isinstance(c, PlayCue)] == [
        PlayCue("ie2", "guzheng", key)]


def test_the_beats_dropdown_picks_any_beats_until_start():
    bit = _connected()
    for name in ("beats3", "beats1", "beats4", "beats2"):
        assert bit.actions()["beats"][1](9.0, name) == []
        assert bit.actions()["beats"][3] == name
        assert bit.status()["beats"] == name
    assert bit.actions()["beats"][1](9.0, "beats9") == "unknown beats 'beats9'"
    _start(bit)
    assert bit.actions()["beats"][1](11.0, "beats1") == "already started"
    assert bit.status()["beats"] == "beats2"


def test_each_beats_asks_only_for_its_own_gestures():
    gestures = {name: {g for _b, g in _connected(name)._answers}
                for name in BEATS}
    assert gestures == {"beats1": {"tap"}, "beats2": {"tap", "swing"},
                        "beats3": {"tap", "swing", "squeeze"},
                        "beats4": {"tap", "swing", "squeeze"}}


@pytest.mark.parametrize("verb, sample", [("swing", "guzheng"),
                                          ("squeeze", "chime")])
@pytest.mark.parametrize("error, key", [(0.0, "key=60"), (-0.4, "key=49")])
def test_a_judged_swing_or_squeeze_plays_the_sine_keys_note_or_49(
        verb, sample, error, key):
    """beats3 asks for a tap, a swing and a squeeze on beat 1 of measure 2,
    each judged on its own."""
    bit = _connected("beats3")
    _start(bit)
    cues = bit.verb_handlers()[verb]("ie1", [], _judged(4.0 + error))
    assert bit._open_ratings[(4.0, verb)]["ie1"] == (
        "GOOD" if error == 0.0 else "BAD")
    assert (4.0, "tap") not in bit._open_ratings
    assert [c for c in cues if isinstance(c, PlayCue)] == [
        PlayCue("ie1", sample, key)]


def test_a_gesture_the_beats_does_not_ask_for_is_not_judged():
    """beats1 asks only for taps: a swing is silent, a squeeze its chime."""
    bit = _connected()
    _start(bit)
    h = bit.verb_handlers()
    assert [c for c in h["swing"]("ie1", [], _judged(4.0))
            if isinstance(c, PlayCue)] == []
    assert [c for c in h["squeeze"]("ie1", [], _judged(4.0))
            if isinstance(c, PlayCue)] == [PlayCue("ie1", "chime", "key=75")]
    assert not bit._open_ratings


def test_every_gesture_of_a_beat_is_rated_for_every_player():
    """beats3, ie1 doing all three on beat 1 of measure 2 and nothing else."""
    bit = _connected("beats3")
    _start(bit)
    for verb in ("tap", "swing", "squeeze"):
        bit.verb_handlers()[verb]("ie1", [], _judged(4.0))
    bit._close_answers(_when(30.0))
    n = bit._answers_closed
    assert bit.ratings("ie1") == {"GOOD": 3, "BAD": 0, "MISS": n - 3}
    assert bit.ratings("ie2") == {"GOOD": 0, "BAD": 0, "MISS": n}
    assert bit.score("ie1") == pytest.approx(3 / n)


@pytest.mark.parametrize("beat, key", [
    (4.0, 60), (5.5, 72), (7.0, 60),        # measure 2: the C arpeggio
    (8.0, 59), (9.5, 71), (11.0, 59),       # measure 3: down to B
    (16.0, 53), (17.0, 69),                 # measure 5: F
    (4.25, 60),                             # between notes: the last begun
    (32.0 + 5.5, 72),                       # the next pass of its 32-beat loop
])
def test_a_good_gesture_plays_the_sine_keys_note_at_its_beat(beat, key):
    assert _connected().good_key(beat) == key


def test_good_keys_follow_the_sine_keys_through_a_whole_answer():
    """Every answer beat of a pass tapped dead on: each plays its own note."""
    bit = _connected()
    _start(bit)
    keys = []
    for b in ANSWERS:
        cues = _tap(bit, "ie1", _judged(b))
        keys += [c.params for c in cues if isinstance(c, PlayCue)]
    assert keys == [f"key={bit.good_key(b)}" for b in ANSWERS]
    assert keys[:3] == ["key=60", "key=72", "key=60"]


def test_a_tap_from_an_unseated_device_plays_nothing():
    assert _tap(_connected(), "ie9", 3.0) == []


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
    assert gs.run_action("player1") == "choose one of ie1, ie2, ie3"
    assert gs.run_action("player1", "ie9") == "unknown choice 'ie9'"
    assert gs.run_action("player1", "ie2") is None
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
               if s == 0x90 and d2 > 0 and tag == "sine_keys") > 3


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
    assert any(s == 0x90 and tag == "sine_keys" for _dev, s, tag in played)


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
        line for measure, beats in ((2, (4, 5.5, 7)), (3, (8, 9.5, 11)),
                                    (4, (12, 13.5, 15)))
        for line in (f"====== Judging {measure} Start ======", *(
            f"answer tap measure {measure} beat {b % 4 + 1:g}: "
            f"o2 t={bit._seq.time_at(b) + JUDGE_DELAY_S:.3f} s" for b in beats))]


def test_an_answer_beat_prints_only_once_its_time_arrives(capsys):
    """Lines come beat by beat, not all together when judging starts."""
    bit = _play(_connected(), [], until_beat=6.5)
    lines = _answer_lines(capsys)
    assert [line.split(":")[0] for line in lines] == [
        "====== Judging 2 Start ======",
        "answer tap measure 2 beat 1",
        "answer tap measure 2 beat 2.5"]
    bit.fires(_judged(7.0) + TICK)
    assert _answer_lines(capsys) == [
        f"answer tap measure 2 beat 4: o2 t={_judged(7.0):.3f} s"]


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
        f"answer tap measure 2 beat 1: o2 t={_judged(4.0):.3f} s")


def test_answer_beats_keep_printing_on_later_passes(capsys):
    _play(_connected(), [], until_beat=LOOP_BEATS + 8.0)
    lines = _answer_lines(capsys)
    assert [line.split(":")[0] for line in lines
            if "measure 6" in line] == [
        "answer tap measure 6 beat 1", "answer tap measure 6 beat 2.5",
        "answer tap measure 6 beat 4"]


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
    assert bit._open_taps[(4.0, "tap")]["ie1"] == pytest.approx(points)
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
