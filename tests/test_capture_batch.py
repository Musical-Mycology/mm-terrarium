"""Wire decoders for /game/capture and /game/telemetry. No sockets, no
luxaeterna: these run in the core offline suite."""

import base64

import pytest

from devicelink.protocol import (
    MOTION_AXES,
    TELEMETRY_BATCH_SCHEMA,
    decode_capture_command,
    decode_telemetry_batch,
)


def _axes(n, fill=0.0):
    return {axis: [fill] * n for axis in MOTION_AXES}


def _batch(**over):
    body = {"capture_id": "shake-021", "seq": 0,
            "t_ms": [0.0, 10.0, 20.0], **_axes(3)}
    body.update(over)
    return ["ie1", 1234.5, body]


def _source(**over):
    src = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
           "platform": "ios 18.5", "device_model": "iPhone 15",
           "motion_stream": "sensors_plus.accelerometer+gyroscope",
           "gravity_included": True, "requested_hz": 100,
           "units": {"accel": "m/s^2", "gyro": "rad/s"}}
    src.update(over)
    return src


def _open(**over):
    meta = {"capture_id": "shake-021", "label": "shake", "series": 3,
            "window_ms": 3000, "t0": 12345.678, "source": _source()}
    meta.update(over)
    return ["ie1", "open", meta]


# --- telemetry -----------------------------------------------------------

def test_schema_constant_is_frozen():
    assert TELEMETRY_BATCH_SCHEMA == "mm-telemetry-batch/1"


def test_decodes_a_motion_only_batch():
    batch = decode_telemetry_batch(_batch())
    assert batch.capture_id == "shake-021"
    assert batch.seq == 0
    assert batch.t_ms == [0.0, 10.0, 20.0]
    assert batch.axes["ax"] == [0.0, 0.0, 0.0]
    assert batch.pcm == b""
    assert batch.pcm_t0_ms is None


def test_decodes_pcm_as_raw_bytes():
    pcm = (1234).to_bytes(2, "little", signed=True) * 4
    batch = decode_telemetry_batch(
        _batch(pcm=base64.b64encode(pcm).decode(), pcm_t0_ms=0.4))
    assert batch.pcm == pcm
    assert batch.pcm_t0_ms == 0.4


def test_ints_are_accepted_as_floats():
    batch = decode_telemetry_batch(_batch(t_ms=[0, 10, 20]))
    assert batch.t_ms == [0.0, 10.0, 20.0]


@pytest.mark.parametrize("args, message", [
    (["ie1", 1.0], "needs 3 args"),
    (["ie1", 1.0, "nope"], "batch must be an object"),
    (_batch(capture_id=""), "capture_id"),
    (_batch(capture_id=7), "capture_id"),
    (_batch(seq=-1), "seq"),
    (_batch(seq="0"), "seq"),
    (_batch(t_ms=[]), "t_ms"),
    (_batch(t_ms=[0.0, 20.0, 10.0]), "non-decreasing"),
    (_batch(t_ms=[0.0, "x", 20.0]), "t_ms"),
    (_batch(ax=[0.0, 0.0]), "ax"),
    (_batch(gz=None), "gz"),
    (_batch(pcm="not base64!!", pcm_t0_ms=0.0), "pcm"),
    (_batch(pcm=base64.b64encode(b"odd").decode(), pcm_t0_ms=0.0), "int16"),
    (_batch(pcm=base64.b64encode(b"\x00\x00").decode()), "pcm_t0_ms"),
])
def test_malformed_telemetry_is_rejected(args, message):
    with pytest.raises(ValueError) as exc:
        decode_telemetry_batch(args)
    assert message in str(exc.value)


def test_missing_axis_is_rejected():
    body = _batch()[2]
    del body["gy"]
    with pytest.raises(ValueError) as exc:
        decode_telemetry_batch(["ie1", 1.0, body])
    assert "gy" in str(exc.value)


# --- capture -------------------------------------------------------------

def test_decodes_open():
    cmd = decode_capture_command(_open())
    assert cmd.action == "open"
    assert cmd.capture_id == "shake-021"
    assert cmd.meta["label"] == "shake"
    assert cmd.meta["source"]["requested_hz"] == 100


def test_decodes_close_without_a_source_block():
    cmd = decode_capture_command(
        ["ie1", "close", {"capture_id": "shake-021", "n": 301, "ok": True,
                          "outputs": []}])
    assert cmd.action == "close"
    assert cmd.meta["n"] == 301


def test_decodes_abandon():
    cmd = decode_capture_command(
        ["ie1", "abandon", {"capture_id": "shake-021", "reason": "cancelled"}])
    assert cmd.action == "abandon"
    assert cmd.meta["reason"] == "cancelled"


def test_open_accepts_a_null_audio_block():
    """Mic permission denied is not fatal: the client still opens the
    capture, motion-only, with audio explicitly null."""
    cmd = decode_capture_command(
        _open(source=_source(audio_stream=None, audio=None)))
    assert cmd.meta["source"]["audio"] is None


@pytest.mark.parametrize("args, message", [
    (["ie1", "open"], "needs 3 args"),
    (["ie1", "wiggle", {"capture_id": "x"}], "unknown capture action"),
    (["ie1", "open", "nope"], "meta must be an object"),
    (["ie1", "open", {}], "capture_id"),
    (_open(label=""), "label"),
    (_open(series="3"), "series"),
    (_open(window_ms=0), "window_ms"),
    (_open(window_ms=-1), "window_ms"),
    (_open(t0="soon"), "t0"),
    (_open(source={}), "source"),
    (_open(source=None), "source"),
])
def test_malformed_capture_command_is_rejected(args, message):
    with pytest.raises(ValueError) as exc:
        decode_capture_command(args)
    assert message in str(exc.value)


def test_open_with_an_incomplete_source_block_is_rejected():
    """Section 7.1 of the spec: a threshold is only meaningful against the
    stream that produced it, so a trace with a partial source block is not
    usable and must never reach disk."""
    src = _source()
    del src["motion_stream"]
    with pytest.raises(ValueError) as exc:
        decode_capture_command(_open(source=src))
    assert "motion_stream" in str(exc.value)
