"""MusicCue / MusicPurge at the devicelink seam: music is Room AUDIO only.

A music cue rides the Room queue (`_room_cues`) so it keeps the Bit's
timestamp and can be purged by tag, but it must never reach a fixture's
light session -- the Tower's light is driven separately by the Bit's state.
"""

import time

import pytest

# Same guard as tests/test_devicelink_agent.py: devicelink.agent needs the
# sibling luxaeterna checkout.
pytest.importorskip("luxaeterna")

from control.audio import AudioBridge, FakePool
from devicelink.agent import DeviceLinkAgent

from tests.test_devicelink_agent import (
    FakeServer,
    _Clock,
    _fake_sessions,
    _room_ready_game_server,
)

MAIN = "sim-room-main"
T = 100.0


def _rig(monkeypatch):
    """An agent over the TEST Room (`main` bound), a real AudioBridge over a
    FakePool, and a hand-advanced clock. Returns (gs, agent, voice, clock)."""
    _fake_sessions(monkeypatch)
    gs = _room_ready_game_server()
    pool = FakePool()
    clk = _Clock(T - 10.0)
    agent = DeviceLinkAgent(gs, FakeServer(), room_audio=AudioBridge(pool),
                            clock=clk)
    assert "main" in agent._room_audio_fixtures
    # `accent` holds a voice too, so pick main's by name, not by pool order.
    return gs, agent, agent.room_audio._devices["main"].voice, clk


def _notes(voice):
    return [c for c in voice.sent if c[0] == "note_on"]


def test_music_cue_reaches_voice_at_when(monkeypatch):
    _gs, agent, voice, clk = _rig(monkeypatch)
    before = len(_notes(voice))

    agent._on_music_cue(MAIN, 0x90, 60, 100, T, "piano")
    agent._render_room()
    assert len(_notes(voice)) == before

    clk.t = T
    agent._render_room()
    assert _notes(voice)[before:] == [("note_on", 60, 100)]


def test_music_cue_does_not_touch_light(monkeypatch):
    _gs, agent, voice, clk = _rig(monkeypatch)

    agent._on_music_cue(MAIN, 0xB0, 20, 99, T, "piano")

    assert 20 not in agent.controllers()["main"]
    assert agent._light_cues.pending() == 0
    assert agent._fixtures["main"].session.fed == []
    # ... and it did not park a feed that a later render could release
    clk.t = T + 5.0
    agent._render_room()
    assert 20 not in agent.controllers()["main"]
    assert agent._fixtures["main"].session.fed == []


def test_music_purge_drops_only_that_tag(monkeypatch):
    _gs, agent, voice, clk = _rig(monkeypatch)
    before = len(_notes(voice))

    agent._on_music_cue(MAIN, 0x90, 60, 100, T, "piano")
    agent._on_music_cue(MAIN, 0x90, 36, 90, T, "drums")
    agent._on_music_purge(MAIN, "piano")

    clk.t = T
    agent._render_room()
    assert _notes(voice)[before:] == [("note_on", 36, 90)]


def test_mute_purges_music_cues(monkeypatch):
    _gs, agent, voice, clk = _rig(monkeypatch)
    before = len(_notes(voice))

    agent._on_music_cue(MAIN, 0x90, 60, 100, T, "piano")
    agent._on_mute_change(MAIN, True)

    clk.t = T
    agent._render_room()
    assert len(_notes(voice)) == before


def test_muted_fixture_drops_new_music_cues(monkeypatch):
    _gs, agent, voice, clk = _rig(monkeypatch)
    before = len(_notes(voice))
    agent._on_mute_change(MAIN, True)

    agent._on_music_cue(MAIN, 0x90, 60, 100, T, "piano")

    assert agent._room_cues.pending() == 0
    clk.t = T
    agent._render_room()
    assert len(_notes(voice)) == before


def test_music_cue_for_a_player_dev_is_dropped(monkeypatch):
    _gs, agent, _voice, _clk = _rig(monkeypatch)

    agent._on_music_cue("ie1", 0x90, 60, 100, T, "piano")

    assert agent._room_cues.pending() == 0


def test_light_cue_still_feeds_room_audio(monkeypatch):
    """Regression: the light-cue -> Room audio path is unchanged (its queue
    entries now carry tag=None)."""
    _gs, agent, voice, clk = _rig(monkeypatch)
    before = len(_notes(voice))

    agent._on_light_cue(MAIN, 0x90, 64, 80, T)
    clk.t = T
    agent._drain_light_cues()   # light feeds a horizon early; audio waits for T
    agent._render_room()

    assert _notes(voice)[before:] == [("note_on", 64, 80)]
    assert agent._fixtures["main"].session.fed == [(0x90, 64, 80)]


def test_wiring_registers_the_music_sinks(monkeypatch):
    gs, agent, _voice, _clk = _rig(monkeypatch)
    assert gs.on_music_cue == agent._on_music_cue
    assert gs.on_music_purge == agent._on_music_purge
