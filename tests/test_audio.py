"""Control-side audio fan-out. Pure and offline: no pyarco, no Arco server,
no network. Every decision AudioBridge makes is asserted against FakeVoice."""

from __future__ import annotations

import pytest

from control.audio import AudioBridge, FakePool
from control.roles import Role, RoleClass

PLAYER_UGENS = {
    "instruments": [
        {"instrument": "flsyn", "program": 89,
         "drone": {"key": 45, "velocity": 90},
         "lanes": [{"source": "cc:74", "dest": "cc:74"},
                   {"source": "cc:11", "dest": "cc:11"}]},
    ],
}


def _role(name="player", ugens=None, welcome=None):
    return Role(name=name, role_class=RoleClass.SHARED, capacity=None,
                scored=True, ugen_manifest=ugens or {}, welcome=welcome)


def test_grant_acquires_a_voice_and_sets_the_program():
    pool = FakePool()
    AudioBridge(pool).on_grant("dev1", _role(ugens=PLAYER_UGENS))
    assert len(pool.acquired) == 1
    assert ("program", 89) in pool.acquired[0].sent


def test_grant_with_empty_ugen_manifest_acquires_nothing():
    # TestBit's jammer role: silent, and it must not burn a channel.
    pool = FakePool()
    AudioBridge(pool).on_grant("dev1", _role(name="jammer"))
    assert pool.acquired == []


def test_declared_cc_lane_reaches_the_voice():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.feed_midi("dev1", 0xB0, 74, 100)
    assert ("cc", 74, 100) in pool.acquired[0].sent


def test_undeclared_cc_is_dropped_not_forwarded():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.feed_midi("dev1", 0xB0, 7, 100)                  # cc:7 has no lane
    assert not [s for s in pool.acquired[0].sent if s[0] == "cc"]


def test_lane_remaps_the_controller_number():
    # The lane is a remap seam, not decoration: cc:74 in, cc:11 out.
    remap = {"instruments": [{"instrument": "flsyn",
                              "lanes": [{"source": "cc:74", "dest": "cc:11"}]}]}
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=remap))
    br.feed_midi("dev1", 0xB0, 74, 64)
    assert ("cc", 11, 64) in pool.acquired[0].sent


def test_program_change_rides_the_same_path():
    # led_smoke's --program override goes through feed_midi like everything
    # else; without 0xC0 handling that flag would silently do nothing.
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.feed_midi("dev1", 0xC0, 5, 0)
    assert ("program", 5) in pool.acquired[0].sent


def test_midi_for_an_ungranted_device_is_ignored():
    pool = FakePool()
    AudioBridge(pool).feed_midi("nobody", 0xB0, 74, 100)   # must not raise


def test_start_and_stop_drone_use_the_declared_note():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.start_drone("dev1")
    assert ("note_on", 45, 90) in pool.acquired[0].sent
    br.stop_drone("dev1")
    assert ("note_off", 45) in pool.acquired[0].sent


def test_start_drone_is_idempotent():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.start_drone("dev1")
    br.start_drone("dev1")
    assert len([s for s in pool.acquired[0].sent if s[0] == "note_on"]) == 1


def test_role_without_a_drone_starts_no_note():
    no_drone = {"instruments": [{"instrument": "flsyn", "program": 1}]}
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=no_drone))
    br.start_drone("dev1")
    assert not [s for s in pool.acquired[0].sent if s[0] == "note_on"]


def test_release_stops_the_drone_silences_and_frees_the_voice():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.start_drone("dev1")
    voice = pool.acquired[0]
    br.on_release("dev1")
    assert ("note_off", 45) in voice.sent
    assert ("all_off",) in voice.sent
    assert pool.released == [voice]


def test_release_is_idempotent_and_midi_after_it_is_ignored():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.on_release("dev1")
    br.on_release("dev1")                               # must not raise
    br.feed_midi("dev1", 0xB0, 74, 100)                 # must not raise
    assert len(pool.released) == 1


def test_shutdown_frees_every_voice_and_shuts_the_pool():
    # Boundary rule 1: owning the ugen id space means freeing it at unload.
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.on_grant("dev2", _role(ugens=PLAYER_UGENS))
    br.shutdown()
    assert len(pool.released) == 2
    assert pool.shut is True


WELCOME = {"light": {"instrument": "glow", "params": {"hue": 0.33},
                     "duration": 1.5},
           "audio": {"instrument": "chime", "duration": 1.5}}


def _clock_from(values):
    return iter(values).__next__


def test_welcome_cue_plays_on_its_own_voice_not_the_drone_voice():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0] * 8))
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS, welcome=WELCOME))
    assert len(pool.acquired) == 2                      # drone voice + cue voice
    cue = pool.acquired[1]
    assert [s for s in cue.sent if s[0] == "note_on"]   # the chime sounded
    drone_voice = pool.acquired[0]
    assert not [s for s in drone_voice.sent if s[0] == "note_on"]


def test_welcome_cue_note_off_fires_after_its_duration_and_frees_the_voice():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0, 1.0, 2.0]))
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS, welcome=WELCOME))
    cue = pool.acquired[1]
    br.tick()                                            # now = 1.0, not due yet
    assert not [s for s in cue.sent if s[0] == "note_off"]
    assert cue not in pool.released
    br.tick()                                            # now = 2.0, past 1.5
    assert [s for s in cue.sent if s[0] == "note_off"]
    assert cue in pool.released


def test_tick_polls_the_pool():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0, 0.1, 0.2]))
    br.tick()
    br.tick()
    assert pool.polls == 2


def test_role_without_a_welcome_audio_half_plays_no_cue():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS,
                              welcome={"light": {"instrument": "glow"}}))
    assert len(pool.acquired) == 1                       # drone voice only


def test_unknown_welcome_instrument_raises_rather_than_playing_silence():
    pool = FakePool()
    br = AudioBridge(pool)
    with pytest.raises(KeyError) as ei:
        br.on_grant("dev1", _role(ugens=PLAYER_UGENS,
                                  welcome={"audio": {"instrument": "gong"}}))
    assert "gong" in str(ei.value)


def test_shutdown_releases_a_still_sounding_welcome_voice():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0] * 8))
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS, welcome=WELCOME))
    br.shutdown()
    assert len(pool.released) == 2                       # drone voice and cue voice
