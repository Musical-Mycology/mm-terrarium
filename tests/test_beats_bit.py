"""BeatsBit: a devtower and two tuneshrooms connect, then the Console's Start
button loops piano.mid from the MIDITEST Room (bits/beats/)."""
import pytest

from control.bit_registry import BitRegistry
from control.cues import ROOM, MusicCue
from control.role_config import validate_role_declarations
from control.terrarium_config import load_terrarium_config

NAME = "BeatsBit"
TICK = 1.0 / 44
BEAT_S = 60.0 / 130.0
LOOP_BEATS = 16


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
    assert cfg.asset_path("piano").is_file()
    assert cfg.asset_path("beats1").is_file()
    assert cfg.asset_path("beats2").is_file()
    assert cfg.start.when == "immediate"
    row = next(r for r in reg.list_view(include_hidden=False)
               if r["name"] == NAME)
    assert row["hidden"] is False


def test_role_table_validates():
    validate_role_declarations(_bit().role_table)


def test_one_console_button_labelled_start():
    assert {name: entry[0] for name, entry in _bit().actions().items()} \
        == {"start": "Start"}


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
    assert bit.status()["piano"] == "playing"


def test_silent_before_start():
    bit = _connected()
    assert bit.fires(10.0) == []
    assert bit.fires(11.0) == []


def test_start_sets_the_program_and_strikes_beat_zero():
    cues = _start(_connected())
    assert all(c.dev == ROOM for c in cues)
    assert (cues[0].status, cues[0].data1, cues[0].when) == (0xC0, 0, 10.0)
    ons = _note_ons(cues)
    assert ons and all(c.when == 10.0 for c in ons[:3])
    assert all(c.status == 0x90 for c in ons)     # part 0


def test_start_twice_refused_and_restarts_nothing():
    bit = _connected()
    _start(bit)
    assert _start(bit, 11.0) == "already started"


def test_status_tracks_devices_and_playing():
    bit = _connected()
    assert bit.status() == {"piano": "waiting", "tower": ["devtower1"],
                            "shrooms": ["ie1", "ie2"]}
    _start(bit)
    assert bit.status()["piano"] == "playing"


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


def _printed_beats(capsys):
    return capsys.readouterr().out.splitlines()


def test_no_beats_printed_before_start(capsys):
    bit = _connected()
    bit.fires(10.0)
    assert _printed_beats(capsys) == []


def test_first_beat_prints_on_start(capsys):
    _start(_connected())
    assert _printed_beats(capsys) == ["BEAT beats1: measure 1 beat 1"]


def test_beats_print_in_measures_one_and_three_of_every_loop(capsys):
    bit = _connected()
    _start(bit)
    at = 10.0
    while at < 10.0 + 2 * LOOP_BEATS * BEAT_S - TICK:
        at += TICK
        bit.fires(at)
    def measure(asset, number, beats):
        return [f"BEAT {asset}: measure {number} beat {b}" for b in beats]
    one_pass = (measure("beats1", 1, ("1", "3", "4"))
                + measure("beats2", 3, ("1", "2", "2.5", "3", "4")))
    assert _printed_beats(capsys) == one_pass + one_pass


def test_beat_prints_on_its_tick_not_ahead_of_it(capsys):
    """The Sequencer's look-ahead sends notes early; a print can't be timed,
    so it waits for the beat. Beat 3 is the first after beat 1's Start."""
    bit = _connected()
    _start(bit)
    capsys.readouterr()
    bit.fires(10.0 + 2 * BEAT_S - 0.001)
    assert _printed_beats(capsys) == []
    bit.fires(10.0 + 2 * BEAT_S)
    assert _printed_beats(capsys) == ["BEAT beats1: measure 1 beat 3"]


def test_live_stack_seats_devices_and_plays_through_the_room():
    """A real GameServer and DeviceLinkAgent in the MIDITEST Room: hellos
    seat the devices, a third shroom goes solo, and Start's notes reach the
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
               ("c3", "ie2", "tuneshroom"), ("c4", "ie3", "tuneshroom"))
    for client, dev, instrument in devices:
        server.arrive(client)
        server.deliver(client, "/game/hello", "ssss",
                       [dev, "sim", "1", instrument])
        agent.poll()
    assert gs.bit.status()["tower"] == ["devtower1"]
    assert gs.bit.status()["shrooms"] == ["ie1", "ie2"]

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
