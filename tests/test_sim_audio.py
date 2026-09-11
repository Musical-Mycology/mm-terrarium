"""harness/sim_audio.py: generated tones and the afplay sink, no audio in CI."""

from __future__ import annotations

import io
import wave

from harness.sim_audio import (AfplaySink, KeyedChimePlayer, build_sim_player,
                               chime_wav_for_key, play_key, tone_wav)


def test_tone_wav_is_a_valid_mono_16bit_wav():
    data = tone_wav([(2000.0, 0.05)])
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() > 0


def test_sink_writes_a_file_and_invokes_its_runner():
    ran = []
    sink = AfplaySink(runner=lambda path: ran.append(path))
    sink.write("click", tone_wav([(2000.0, 0.05)]))
    assert len(ran) == 1
    assert ran[0].endswith(".wav")
    with open(ran[0], "rb") as handle:
        assert handle.read(4) == b"RIFF"


def test_sink_swallows_a_raising_runner():
    def boom(path):
        raise OSError("no afplay here")
    sink = AfplaySink(runner=boom)
    sink.write("click", tone_wav([(2000.0, 0.05)]))   # must not raise


def test_build_sim_player_preloads_click_and_chime():
    ran = []
    player = build_sim_player(runner=lambda path: ran.append(path))
    assert player.is_preloaded
    player.play("click")
    player.play("chime")
    assert len(ran) == 2


def test_play_key_parses_the_ceremony_params():
    assert play_key("key=71") == 71
    assert play_key("") is None
    assert play_key("key=x") is None


def test_keyed_chime_is_a_wav_whose_pitch_follows_the_key():
    for key in (69, 81):
        data = chime_wav_for_key(key)
        with wave.open(io.BytesIO(data)) as w:
            assert w.getnchannels() == 1 and w.getnframes() > 1000


def test_keyed_chime_player_writes_one_sample_per_key():
    written = []
    sink = AfplaySink(runner=lambda path: written.append(path))
    player = KeyedChimePlayer(sink)
    player.play(69)
    player.play(71)
    player.play(69)
    assert len(written) == 3
