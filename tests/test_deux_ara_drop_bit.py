"""DeuxAraDropBit: piano, drums and synth MIDI loops dropped in and out by
single taps, the Arabesque on a double tap, tilt ducking the mix, all on the
MIDITEST Room's speaker-equipped Tower (bits/deux_ara_drop/)."""
import random

from control.bit_registry import BitRegistry
from control.cues import ROOM, LightCue, MusicCue, MusicPurge, PixelFlashCue
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.rooms import Room
from control.state import State
from control.terrarium_config import load_terrarium_config

NAME = "DeuxAraDropBit"
DEV = "ie1"
TICK = 1.0 / 44


def _registry():
    reg = BitRegistry.discover()
    assert NAME in reg.packages, reg.errors
    return reg


def _bit(seed=None):
    reg = _registry()
    cls = reg.bit_class(NAME)
    rng = random.Random(seed) if seed is not None else None
    return cls(reg.resolve_config(NAME, {}), rng=rng)


def _started(seed=None, at=10.0):
    bit = _bit(seed)
    first = bit.fires(at)
    return bit, first


def _tap(bit, at, count=1):
    return bit.verb_handlers()["tap"](DEV, [DEV, 2.5, 80.0, count], at)


def _tilt(bit, gamma, at):
    return bit.verb_handlers()["tilt"](DEV, [DEV, gamma], at)


def _lights(cues):
    return [(c.data1, c.data2) for c in cues if isinstance(c, LightCue)]


def test_manifest():
    reg = _registry()
    cfg = reg.resolve_config(NAME, {})
    assert cfg.launch.room_types == ("MIDITEST",)
    assert cfg.console.display_name == "Deux Ara Drop"
    for key in ("piano", "drums", "synth", "arabesque"):
        assert cfg.asset_path(key).is_file()
    assert "MIDITEST" in reg.bit_class(NAME).room_types


def test_first_fires_paints_thirds_red():
    _, first = _started()
    assert sorted(_lights(first)) == [(20, 0), (21, 0), (22, 0)]
    assert all(c.status == 0xB0 and c.dev == ROOM and c.when == 10.0
               for c in first if isinstance(c, LightCue))


def test_first_fires_restores_programs():
    _, first = _started()
    programs = [(c.status, c.data1) for c in first
                if isinstance(c, MusicCue) and c.status & 0xF0 == 0xC0]
    assert sorted(programs) == [(0xC0, 0), (0xC2, 90), (0xC3, 0)]
    assert not any(c.status == 0xC1 for c in first
                   if isinstance(c, MusicCue))


def test_tap_cycle():
    bit, _ = _started()
    outs = [_tap(bit, 11.0 + i) for i in range(6)]
    toggled = [next(c.tag for c in out if isinstance(c, MusicCue))
               for out in outs]
    assert toggled == ["piano", "drums", "synth", "piano", "drums", "synth"]
    assert (20, 42) in _lights(outs[0])
    assert (20, 0) in _lights(outs[3])
    assert MusicPurge(ROOM, "piano") in outs[3]

    bit, _ = _started()
    for i in range(3):
        _tap(bit, 11.0 + i)
    status = bit.status()
    assert [status[t] for t in ("piano", "drums", "synth")] == ["on"] * 3
    for i in range(3):
        _tap(bit, 14.0 + i)
    status = bit.status()
    assert [status[t] for t in ("piano", "drums", "synth")] == ["off"] * 3


def test_double_tap_toggles_arabesque_not_cycle():
    bit, _ = _started()
    _tap(bit, 11.0, count=2)
    assert bit.status()["arabesque"] == "playing"
    out = _tap(bit, 12.0)
    assert bit.status()["piano"] == "on"
    assert (20, 42) in _lights(out)
    out = _tap(bit, 13.0, count=2)
    assert MusicPurge(ROOM, "arabesque") in out
    assert bit.status()["arabesque"] == "stopped"


def test_double_tap_after_natural_end_restarts():
    bit, _ = _started()
    _tap(bit, 11.0, count=2)
    t = 11.0
    while bit.status()["arabesque"] == "playing":
        t += 0.5
        bit.fires(t)
        assert t < 11.0 + 600, "the Arabesque never ended"
    assert bit.status()["arabesque"] == "stopped"
    _tap(bit, t + 1.0, count=2)
    assert bit.status()["arabesque"] == "playing"


def test_tilt_hysteresis():
    bit, _ = _started()
    out = _tilt(bit, 50.0, 11.0)
    loops = [c for c in out
             if isinstance(c, MusicCue) and c.tag in ("piano", "drums", "synth")]
    assert len(loops) == 3
    assert all((c.status & 0xF0, c.data1, c.data2) == (0xB0, 7, 20)
               for c in loops)
    assert bit.status()["volume"] == "20%"
    assert _tilt(bit, 30.0, 12.0) == []
    assert _tilt(bit, 40.0, 13.0) == []
    assert bit.status()["volume"] == "20%"
    assert _tilt(bit, 15.0, 14.0) != []
    assert bit.status()["volume"] == "100%"
    assert _tilt(bit, -60.0, 15.0) != []
    assert bit.status()["volume"] == "20%"


def test_flickers_only_while_arabesque_plays():
    bit, _ = _started(seed=7, at=10.0)
    t = 10.0
    before = []
    for _ in range(44):
        t += TICK
        before += bit.fires(t)
    assert not any(isinstance(c, PixelFlashCue) for c in before)
    _tap(bit, t, count=2)
    after = []
    for _ in range(44):
        t += TICK
        after += bit.fires(t)
    flashes = [c for c in after if isinstance(c, PixelFlashCue)]
    assert flashes
    assert all(c.rgb == (0, 0, 0, 255) and 0 <= c.pixel < 14
               and c.dev == ROOM for c in flashes)


def test_status_and_update():
    bit = _bit()
    assert bit.status() == {"piano": "off", "drums": "off", "synth": "off",
                            "arabesque": "stopped", "volume": "100%"}
    assert bit.update(3600.0) is False


def test_loads_in_miditest_room():
    reg = _registry()
    profile = load_terrarium_config("terrarium.toml").rooms["MIDITEST"].profile
    gs = GameServer({NAME: reg.bit_class(NAME)},
                    room_binding=RoomBindingRegistry())
    gs.room = Room(name="MIDITEST", profile=profile,
                   node_id="ROOM_MIDITEST_NODE")
    gs.load_bit(NAME, config=reg.resolve_config(NAME, {}))
    assert gs.state is State.SETUP
