"""Probe P5 of the o2lite migration spec, as a permanent test: a 100 ms
telemetry batch with 16 kHz audio does not fit one o2lite message, and the
reference chunker splits it so every blob fits under the C library's cap.
Runs in the core offline suite."""
import base64
import struct

import pytest

from devicelink.protocol import (MOTION_AXES, O2_MAX_MSG_LEN,
                                 TELEMETRY_BLOB_BUDGET, chunk_telemetry_batch,
                                 decode_telemetry_batch, encode_telemetry_body)


def _hundred_ms_batch(with_audio: bool = True) -> dict:
    n = 10                                  # 100 Hz motion for 100 ms
    body = {"capture_id": "shake-021",
            "t_ms": [700.0 + 10.0 * i for i in range(n)]}
    for k, axis in enumerate(MOTION_AXES):
        body[axis] = [round(-9.80665 + 0.001 * (i + k), 6) for i in range(n)]
    if with_audio:
        frames = 1600                       # 16 kHz for 100 ms
        body["pcm"] = struct.pack(f"<{frames}h",
                                  *[(i * 37) % 65536 - 32768 for i in range(frames)])
        body["pcm_t0_ms"] = 700.4
    return body


def test_budget_sits_under_the_c_library_cap():
    assert O2_MAX_MSG_LEN == 4096
    assert 0 < TELEMETRY_BLOB_BUDGET < O2_MAX_MSG_LEN


def test_a_100ms_batch_with_audio_does_not_fit_one_message():
    body = _hundred_ms_batch()
    whole = dict(body, seq=0,
                 pcm=base64.b64encode(body["pcm"]).decode("ascii"))
    assert len(encode_telemetry_body(whole)) > TELEMETRY_BLOB_BUDGET


def test_chunks_fit_and_reassemble_without_loss():
    body = _hundred_ms_batch()
    chunks = chunk_telemetry_batch(body, first_seq=7)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(encode_telemetry_body(chunk)) <= TELEMETRY_BLOB_BUDGET

    decoded = [decode_telemetry_batch(["ie1", 0.0, c]) for c in chunks]
    assert [d.seq for d in decoded] == list(range(7, 7 + len(chunks)))
    assert sum((d.t_ms for d in decoded), []) == body["t_ms"]
    for axis in MOTION_AXES:
        assert sum((d.axes[axis] for d in decoded), []) == body[axis]
    assert b"".join(d.pcm for d in decoded) == body["pcm"]
    assert decoded[0].pcm_t0_ms == body["pcm_t0_ms"]
    # each later chunk's pcm_t0_ms is the frames-before offset on the audio clock
    sent = 0
    for d in decoded:
        assert d.pcm_t0_ms == pytest.approx(body["pcm_t0_ms"] + sent * 1000.0 / 16000)
        sent += len(d.pcm) // 2


def test_a_motion_only_batch_is_one_chunk():
    chunks = chunk_telemetry_batch(_hundred_ms_batch(with_audio=False), first_seq=0)
    assert len(chunks) == 1
    assert "pcm" not in chunks[0]
    assert chunks[0]["seq"] == 0


def test_an_unsplittable_batch_is_refused():
    """One motion sample cannot be split further, so a batch whose single
    sample carries more audio than fits has to be refused: the producer
    must send more often, and silently dropping audio is not an option."""
    body = _hundred_ms_batch()
    body["t_ms"] = body["t_ms"][:1]
    for axis in MOTION_AXES:
        body[axis] = body[axis][:1]
    with pytest.raises(ValueError, match="more often"):
        chunk_telemetry_batch(body, first_seq=0)
