"""The in-memory Trace record: accumulation, gap detection, serialisation.
Pure -- no filesystem, no luxaeterna, core offline suite."""

import pytest

from capture.trace import TRACE_SCHEMA, Trace
from devicelink.protocol import MOTION_AXES, TelemetryBatch

SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"},
          "audio_stream": "record.startStream",
          "audio": {"rate": 16000, "bits": 16, "channels": 1}}


def make_trace(**over):
    kwargs = {"session": "2026-08-07T14-22-03Z-3f9a",
              "capture_id": "shake-021", "label": "shake", "series": 3,
              "dev": "ie1", "bit": {"name": "capture", "version": "0.1"},
              "source": SOURCE, "window_ms": 3000.0, "t0_device": 12345.678}
    kwargs.update(over)
    return Trace(**kwargs)


def make_batch(seq=0, t_ms=None, pcm=b"", pcm_t0_ms=None):
    t_ms = [0.0, 10.0] if t_ms is None else t_ms
    return TelemetryBatch(
        capture_id="shake-021", seq=seq, t_ms=t_ms,
        axes={axis: [float(i) for i in range(len(t_ms))]
              for axis in MOTION_AXES},
        pcm=pcm, pcm_t0_ms=pcm_t0_ms)


def test_schema_constant_is_frozen():
    assert TRACE_SCHEMA == "mm-telemetry-trace/1"


def test_a_fresh_trace_is_empty():
    trace = make_trace()
    assert trace.n == 0
    assert trace.gaps == []
    assert trace.truncated is False


def test_batches_concatenate_in_order():
    trace = make_trace()
    trace.append(make_batch(seq=0, t_ms=[0.0, 10.0]))
    trace.append(make_batch(seq=1, t_ms=[20.0, 30.0]))
    body = trace.to_dict(audio_file=None)
    assert body["n"] == 4
    assert body["samples"]["t_ms"] == [0.0, 10.0, 20.0, 30.0]
    assert body["samples"]["ax"] == [0.0, 1.0, 0.0, 1.0]
    assert body["gaps"] == []


def test_a_skipped_seq_is_recorded_as_a_gap():
    """A trace that lies about its own continuity is worse than no trace."""
    trace = make_trace()
    trace.append(make_batch(seq=0))
    trace.append(make_batch(seq=3))
    body = trace.to_dict(audio_file=None)
    assert body["gaps"] == [{"expected": 1, "got": 3}]
    assert body["n"] == 4          # the surviving samples are still kept


def test_a_stale_seq_is_rejected_rather_than_corrupting_the_trace():
    trace = make_trace()
    trace.append(make_batch(seq=0))
    trace.append(make_batch(seq=1))
    with pytest.raises(ValueError) as exc:
        trace.append(make_batch(seq=1))
    assert "stale" in str(exc.value)
    assert trace.n == 4            # unchanged by the rejected batch


def test_pcm_accumulates_and_the_first_offset_wins():
    trace = make_trace()
    trace.append(make_batch(seq=0, pcm=b"\x01\x00", pcm_t0_ms=0.4))
    trace.append(make_batch(seq=1, pcm=b"\x02\x00", pcm_t0_ms=100.4))
    assert bytes(trace.pcm) == b"\x01\x00\x02\x00"
    body = trace.to_dict(audio_file="021.wav")
    assert body["audio"]["t0_ms"] == 0.4
    assert body["audio"]["file"] == "021.wav"
    assert body["audio"]["rate"] == 16000


def test_a_motion_only_trace_serialises_audio_as_null():
    """Mic permission denied is not fatal (spec 6.2)."""
    trace = make_trace()
    trace.append(make_batch(seq=0))
    assert trace.to_dict(audio_file=None)["audio"] is None


def test_the_serialised_shape_matches_the_spec():
    trace = make_trace()
    trace.append(make_batch(seq=0, pcm=b"\x00\x00", pcm_t0_ms=0.0))
    trace.outputs = [{"t_ms": -1800.0, "event": "countdown", "level": 0.6}]
    trace.notes = "third rep felt sloppy"
    body = trace.to_dict(audio_file="021.wav")

    assert body["schema"] == TRACE_SCHEMA
    assert body["session"] == "2026-08-07T14-22-03Z-3f9a"
    assert body["capture_id"] == "shake-021"
    assert body["label"] == "shake"
    assert body["series"] == 3
    assert body["dev"] == "ie1"
    assert body["bit"] == {"name": "capture", "version": "0.1"}
    assert body["source"] == SOURCE
    assert body["window_ms"] == 3000.0
    assert body["t0_device"] == 12345.678
    assert body["truncated"] is False
    assert body["notes"] == "third rep felt sloppy"
    assert body["outputs"][0]["event"] == "countdown"
    assert set(body["samples"]) == {"t_ms", *MOTION_AXES}
    assert "NOT sample-locked" in body["audio"]["clock"]


def test_truncated_is_reported():
    trace = make_trace()
    trace.append(make_batch(seq=0))
    trace.truncated = True
    assert trace.to_dict(audio_file=None)["truncated"] is True
