"""CalibrationBit: tuneshrooms connect, then the Console's Play button plays
calibration.mid on repeat as wooden knocks at 75 BPM, until pressed again
(bits/calibration/)."""

import pytest

from control.bit import MEAN_TAP_DELAY_S
from control.bit_registry import BitRegistry
from control.cues import ROOM, MusicCue, MusicPurge, PixelFlashCue
from control.engine import GameServer
from control.role_config import validate_role_declarations
from control.terrarium_config import load_terrarium_config

NAME = "CalibrationBit"
CALIBRATION = "calibration"
BEAT_S = 60.0 / 75.0
TICK = 1 / 44
WOODBLOCK = 115
# calibration.mid: four knocks on key 48, the middle two harder, each an eighth
# of a beat long. The file says 120 BPM; the Bit plays it at 75.
KNOCKS = [(0, 96), (1, 127), (2, 127), (3, 96)]


def _bit():
    reg = BitRegistry.discover()
    assert NAME in reg.packages, reg.errors
    return reg.bit_class(NAME)(reg.resolve_config(NAME, {}))


def _play(bit, at=10.0):
    return bit.actions()["play"][1](at)


def _run(bit, cues, until, at=10.0):
    while at < until:
        at += TICK
        cues = cues + bit.fires(at)
    return cues


def _note_ons(cues):
    return [c for c in cues if isinstance(c, MusicCue)
            and c.status & 0xF0 == 0x90]


def test_manifest():
    reg = BitRegistry.discover()
    cfg = reg.resolve_config(NAME, {})
    assert cfg.launch.room_types == ("MIDITEST",)
    assert cfg.console.display_name == "Calibration"
    assert cfg.asset_path("calibration").is_file()
    assert cfg.start.when == "immediate"


def test_role_table_validates():
    validate_role_declarations(_bit().role_table)


def test_one_console_button_labelled_play_at_first():
    assert {name: entry[0] for name, entry in _bit().actions().items()} \
        == {"play": "Play"}


def test_the_room_flsyn_is_a_woodblock():
    light, ugen = _bit().room_manifests()
    assert light == {}
    assert [(i["instrument"], i["program"]) for i in ugen["instruments"]] \
        == [("flsyn", WOODBLOCK)]


@pytest.mark.parametrize("dev,role,admitted", [
    ("ie1", "shroom", True),
    ("ie12", "shroom", True),
    ("devtower1", "shroom", False),
    ("other", "shroom", False),
])
def test_admits_only_tuneshrooms(dev, role, admitted):
    assert _bit().admits(dev, role) is admitted


def test_status_tracks_the_shrooms():
    bit = _bit()
    bit.on_join("ie2", "shroom")
    bit.on_join("ie1", "shroom")
    bit.on_join("other", "solo")
    assert bit.status() == {"calibration": "ready",
                            "shrooms": ["ie1", "ie2"],
                            "latest delay": "-", "mean delay": "-", "taps": 0}


def test_silent_until_the_button():
    bit = _bit()
    assert bit.fires(10.0) == []
    assert bit.fires(11.0) == []


def test_play_sets_the_woodblock_and_knocks_at_once():
    cues = _play(_bit())
    assert all(c.dev == ROOM for c in cues)
    assert (cues[0].status, cues[0].data1, cues[0].when) \
        == (0xC0, WOODBLOCK, 10.0)
    assert _note_ons(cues)[0].when == 10.0


def test_the_knocks_fall_at_75_bpm_whatever_the_file_says():
    bit = _bit()
    ons = _note_ons(_run(bit, _play(bit), 10.0 + 3.5 * BEAT_S))
    assert [(c.data1, c.data2) for c in ons] \
        == [(48, vel) for _beat, vel in KNOCKS]
    assert [c.when for c in ons] == pytest.approx(
        [10.0 + beat * BEAT_S for beat, _vel in KNOCKS])


def test_it_repeats_every_four_beats():
    bit = _bit()
    ons = _note_ons(_run(bit, _play(bit), 10.0 + 10 * BEAT_S))
    assert [c.when for c in ons] == pytest.approx(
        [10.0 + beat * BEAT_S for beat in range(len(ons))])
    assert [c.data2 for c in ons[:8]] == [vel for _b, vel in KNOCKS] * 2


def test_the_button_reads_stop_while_looping():
    bit = _bit()
    _play(bit)
    assert bit.actions()["play"][0] == "Stop"
    assert bit.status()["calibration"] == "playing"
    _play(bit, 10.5)
    assert bit.actions()["play"][0] == "Play"
    assert bit.status()["calibration"] == "ready"


def test_stop_drops_queued_knocks_and_silences_the_rest():
    bit = _bit()
    _run(bit, _play(bit), 10.0 + 1.5 * BEAT_S)
    at = 10.0 + 1.5 * BEAT_S
    stop = _play(bit, at)
    assert isinstance(stop[0], MusicPurge)
    assert stop[0].tag == CALIBRATION
    assert [(c.status, c.data1, c.when) for c in stop[1:2]] == [(0x80, 48, at)]
    assert _run(bit, [], at + 10 * BEAT_S, at=at) == []


def test_play_after_stop_starts_from_the_top():
    bit = _bit()
    _run(bit, _play(bit), 10.0 + 1.5 * BEAT_S)
    _play(bit, 12.0)
    again = _play(bit, 20.0)
    ons = _note_ons(_run(bit, again, 20.0 + 3.5 * BEAT_S, at=20.0))
    assert [(c.when, c.data2) for c in ons] == [
        (pytest.approx(20.0 + beat * BEAT_S), vel) for beat, vel in KNOCKS]


HORIZON = 0.06


def _tapping():
    """Knocks looping from t = 10.0, with one shroom seated."""
    bit = _bit()
    bit.cue_horizon = HORIZON
    bit.on_join("ie1", "shroom")
    _play(bit)
    return bit


def _tap(bit, moment, dev="ie1"):
    """A tap at real time `moment`, arriving a horizon ahead as `at` does."""
    return bit.verb_handlers()["tap"](dev, [], moment + HORIZON)


@pytest.mark.parametrize("moment,delay", [
    (10.0 + BEAT_S + 0.2, 0.2),         # late on the second knock
    (10.0 + BEAT_S - 0.1, -0.1),        # early on the second knock
    (10.0 + 0.3, 0.3),                  # the first knock of all
    (10.0 - 0.02, -0.02),               # nothing before the first knock
    (10.0 + 3 * BEAT_S + 0.1, 0.1),     # the last knock of the pass
    (10.0 + 4 * BEAT_S - 0.1, -0.1),    # the next pass's first knock
    (10.0 + 41 * BEAT_S + 0.25, 0.25),  # many passes on
])
def test_a_tap_is_timed_against_the_nearest_knock(moment, delay):
    bit = _tapping()
    assert _tap(bit, moment) == []
    assert bit.mean_delay() == pytest.approx(delay)


def test_the_console_shows_the_latest_delay_and_the_signed_mean():
    bit = _tapping()
    _tap(bit, 10.0 + BEAT_S + 0.2)
    status = bit.status()
    assert status["latest delay"] == status["mean delay"] == "+200.0 ms"
    _tap(bit, 10.0 + 2 * BEAT_S + 0.3)
    _tap(bit, 10.0 + 3 * BEAT_S - 0.2)
    status = bit.status()
    assert status["latest delay"] == "-200.0 ms"
    assert status["mean delay"] == "+100.0 ms"
    assert status["taps"] == 3


def test_a_tap_prints_its_delay(capsys):
    _tap(_tapping(), 10.0 + BEAT_S + 0.2)
    assert ("tap ie1: delay +200.0 ms, mean +200.0 ms, 1 taps"
            in capsys.readouterr().out)


def test_every_shroom_counts_towards_one_mean():
    bit = _tapping()
    bit.on_join("ie2", "shroom")
    _tap(bit, 10.0 + BEAT_S + 0.1)
    _tap(bit, 10.0 + BEAT_S + 0.3, dev="ie2")
    assert bit.mean_delay() == pytest.approx(0.2)


def test_taps_only_count_from_seated_shrooms():
    bit = _tapping()
    _tap(bit, 10.0 + BEAT_S, dev="other")
    assert bit.status()["taps"] == 0


def test_taps_before_play_and_after_stop_do_not_count():
    bit = _bit()
    bit.on_join("ie1", "shroom")
    _tap(bit, 9.0)
    assert bit.status()["taps"] == 0
    _play(bit)
    _tap(bit, 10.1)
    _play(bit, 11.0)
    _tap(bit, 11.1)
    assert bit.status()["taps"] == 1


def test_stop_keeps_the_measurement_and_play_starts_a_new_one():
    bit = _tapping()
    _tap(bit, 10.1)
    _play(bit, 11.0)
    assert bit.status()["mean delay"] == "+100.0 ms"
    _play(bit, 12.0)
    assert bit.status()["latest delay"] == "-"
    assert bit.status()["mean delay"] == "-"
    assert bit.status()["taps"] == 0


def test_nothing_is_saved_before_a_tap_counts():
    bit = _bit()
    _play(bit)
    assert MEAN_TAP_DELAY_S not in bit.carried


def test_each_tap_saves_the_running_mean():
    bit = _tapping()
    _tap(bit, 10.0 + BEAT_S + 0.1)
    assert bit.carried[MEAN_TAP_DELAY_S] == pytest.approx(0.1)
    _tap(bit, 10.0 + BEAT_S + 0.3)
    assert bit.carried[MEAN_TAP_DELAY_S] == pytest.approx(0.2)


def test_play_keeps_the_saved_mean_until_the_new_measurements_first_tap():
    bit = _tapping()
    _tap(bit, 10.1)
    _play(bit, 11.0)
    _play(bit, 12.0)
    assert bit.carried[MEAN_TAP_DELAY_S] == pytest.approx(0.1)
    _tap(bit, 12.3)
    assert bit.carried[MEAN_TAP_DELAY_S] == pytest.approx(0.3)


def test_the_mean_carries_from_calibration_to_the_beats_light_delay():
    """A real GameServer: the mean outlasts CalibrationBit's unload and sets
    how far BeatsBit's first responder light is held behind Start."""
    reg = BitRegistry.discover()
    gs = GameServer({name: reg.bit_class(name)
                     for name in (NAME, "BeatsBit")}, cue_horizon=HORIZON)

    gs.load_bit(NAME, config=reg.resolve_config(NAME, {}))
    gs.run()
    gs.bit.on_join("ie1", "shroom")
    _play(gs.bit)
    _tap(gs.bit, 10.0 + BEAT_S + 0.25)
    gs.abort()
    assert gs.bit is None

    gs.load_bit("BeatsBit", config=reg.resolve_config("BeatsBit", {}))
    beats = gs.bit
    beats.on_join("devtower1", "tower")
    assert beats.status()["light delay"] == "+250.0 ms"
    beats.actions()["start"][1](10.0)
    assert not [c for c in beats.fires(10.0 + 0.249)
                if isinstance(c, PixelFlashCue)
                and c.dev == "devtower1" and c.pixel >= 32]
    assert [c for c in beats.fires(10.0 + 0.25)
            if isinstance(c, PixelFlashCue)
            and c.dev == "devtower1" and c.pixel >= 32]


def test_live_stack_seats_tuneshrooms_and_knocks_through_the_room():
    """A real GameServer and DeviceLinkAgent in the MIDITEST Room: hellos seat
    the tuneshrooms, a devtower goes solo, and Play reaches the music sink."""
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

    devices = (("c1", "ie1", "tuneshroom"), ("c2", "ie2", "tuneshroom"),
               ("c3", "ie3", "tuneshroom"), ("c4", "devtower1", "devtower"))
    for client, dev, instrument in devices:
        server.arrive(client)
        server.deliver(client, "/game/hello", "ssss",
                       [dev, "sim", "1", instrument])
        agent.poll()
    assert gs.bit.status()["shrooms"] == ["ie1", "ie2", "ie3"]

    played = []
    gs.on_music_cue = lambda dev, status, d1, d2, when, tag: played.append(
        (status, d1, d2, tag))
    assert gs.run_action("play") is None

    def run_beats(beats):
        for _ in range(int(BEAT_S * beats / TICK)):
            clk.advance(TICK)
            agent.poll()
            gs.tick(TICK)

    def knocks():
        return [d2 for s, _d1, d2, tag in played
                if s == 0x90 and tag == CALIBRATION]

    run_beats(5)
    assert (0xC0, WOODBLOCK) in [(s, d1) for s, d1, _d2, _tag in played]
    assert knocks()[:5] == [vel for _beat, vel in KNOCKS] + [KNOCKS[0][1]]

    # A real /game/tap, stamped 200 ms after a knock: the GameServer's own
    # horizon comes off, so the delay reads exactly that.
    server.deliver("c1", "/game/tap", "sffi", ["ie1", 0.0, 80.0, 1],
                   timestamp=gs.bit._seq.time_at(5.0) + 0.2)
    agent.poll()
    assert gs.bit.status()["latest delay"] == "+200.0 ms"
    assert gs.bit.status()["mean delay"] == "+200.0 ms"

    assert gs.run_action("play") is None
    heard = len(knocks())
    run_beats(5)
    assert len(knocks()) == heard
