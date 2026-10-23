"""MusicCue / MusicPurge: audio-only cues a Bit returns so a sequencer can
stream timed notes to the Room speaker and purge a track's queued notes.

Shares the GameServer + two-fixture Room setup with
tests/test_engine_functions.py rather than rebuilding it.
"""

from control.cues import ROOM, LightCue, MuteCue, MusicCue, MusicPurge, PlayCue
from tests.test_engine_functions import _BaseBit, _running

BOUND = {"main": "sim-room-main", "accent": "sim-room-accent"}
DEVS = {"sim-room-main", "sim-room-accent"}


def _sinks(gs):
    music, purge, light, play = [], [], [], []
    gs.on_music_cue = lambda *a: music.append(a)
    gs.on_music_purge = lambda *a: purge.append(a)
    gs.on_light_cue = lambda *a: light.append(a)
    gs.on_play_cue = lambda *a: play.append(a)
    return music, purge, light, play


def test_music_cue_to_room_reaches_music_sink_per_fixture():
    gs, _, _ = _running(bound=BOUND)
    music, _, light, _ = _sinks(gs)
    gs._dispatch_cues([MusicCue(ROOM, 0x91, 60, 100, when=5.0, tag="piano")],
                      at=100.0)
    assert {c[0] for c in music} == DEVS and len(music) == 2
    assert all(c[1:] == (0x91, 60, 100, 5.0, "piano") for c in music)
    assert light == []


def test_music_cue_without_when_inherits_at():
    gs, _, _ = _running(bound=BOUND)
    music, _, _, _ = _sinks(gs)
    gs._dispatch_cues([MusicCue(ROOM, 0x91, 60, 100)], at=123.5)
    assert [c[4] for c in music] == [123.5, 123.5]
    assert all(c[5] is None for c in music)


def test_music_purge_reaches_purge_sink():
    gs, _, _ = _running(bound=BOUND)
    _, purge, _, _ = _sinks(gs)
    gs._dispatch_cues([MusicPurge(ROOM, "piano")], at=100.0)
    assert sorted(purge) == [("sim-room-accent", "piano"),
                             ("sim-room-main", "piano")]


def test_muted_dev_drops_music_cue():
    gs, _, _ = _running(bound={"main": "sim-room-main"})
    music, _, _, _ = _sinks(gs)
    gs._dispatch_cues([MuteCue("sim-room-main")], at=100.0)
    gs._dispatch_cues([MusicCue("sim-room-main", 0x91, 60, 100)], at=100.0)
    assert music == []


def test_fires_may_return_light_and_music_cues():
    class TimedFiresBit(_BaseBit):
        def fires(self, at):
            dev = "sim-room-main"
            return [LightCue(dev, 0xB0, 74, 9, when=at),
                    MusicCue(dev, 0x91, 60, 100, when=at, tag="piano"),
                    MusicPurge(dev, "piano")]

    gs, _, _ = _running(bit_cls=TimedFiresBit, bound={"main": "sim-room-main"})
    music, purge, light, _ = _sinks(gs)
    gs.tick(0.01)
    assert len(light) == 1 and len(music) == 1 and len(purge) == 1


def test_fires_still_drops_play_cue():
    class PlayFiresBit(_BaseBit):
        def fires(self, at):
            return [PlayCue(ROOM, "click")]

    gs, _, _ = _running(bit_cls=PlayFiresBit, bound={"main": "sim-room-main"})
    music, purge, light, play = _sinks(gs)
    gs.tick(0.01)
    assert music == purge == light == play == []
