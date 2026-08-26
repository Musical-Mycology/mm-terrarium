"""harness/sim_audio.py: generated tones and the afplay sink, no audio in CI."""

from __future__ import annotations

import io
import wave

from harness.sim_audio import AfplaySink, build_sim_player, tone_wav


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
