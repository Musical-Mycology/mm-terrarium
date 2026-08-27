"""CaptureStore: layout, WAV sidecars, the index, gaps, expiry, and the
failure modes that must never wedge the tick loop. Uses tmp_path only --
no luxaeterna, core offline suite."""

import json
import struct
import wave
from pathlib import Path

import pytest

from capture.store import CaptureError, CaptureStore, new_session_id, wav_bytes
from devicelink.protocol import (MOTION_AXES, CaptureCommand, TelemetryBatch)

BIT = {"name": "capture", "version": "0.1"}
SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"},
          "audio_stream": "record.startStream",
          "audio": {"rate": 16000, "bits": 16, "channels": 1}}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def open_cmd(capture_id="shake-021", label="shake", series=3):
    return CaptureCommand(action="open", capture_id=capture_id,
                          meta={"capture_id": capture_id, "label": label,
                                "series": series, "window_ms": 3000.0,
                                "t0": 12345.678, "source": SOURCE})


def close_cmd(capture_id="shake-021", outputs=None):
    return CaptureCommand(action="close", capture_id=capture_id,
                          meta={"capture_id": capture_id, "n": 2, "ok": True,
                                "outputs": outputs or []})


def batch(seq=0, capture_id="shake-021", t_ms=None, pcm=b"", pcm_t0_ms=None):
    t_ms = [0.0, 10.0] if t_ms is None else t_ms
    return TelemetryBatch(capture_id=capture_id, seq=seq, t_ms=t_ms,
                          axes={a: [1.0] * len(t_ms) for a in MOTION_AXES},
                          pcm=pcm, pcm_t0_ms=pcm_t0_ms)


def make_store(tmp_path, clock=None):
    return CaptureStore(root=tmp_path, session_id="SESSION", bit=BIT,
                        clock=clock or FakeClock())


# --- session ids ---------------------------------------------------------

def test_session_id_is_filesystem_safe_and_sortable():
    import datetime
    sid = new_session_id(
        now=datetime.datetime(2026, 8, 7, 14, 22, 3,
                              tzinfo=datetime.timezone.utc),
        suffix="3f9a")
    assert sid == "2026-08-07T14-22-03Z-3f9a"
    assert ":" not in sid


def test_session_ids_differ_without_arguments():
    assert new_session_id() != new_session_id()


# --- wav framing ---------------------------------------------------------

def test_wav_bytes_is_a_readable_16bit_mono_wav():
    pcm = struct.pack("<4h", 0, 1000, -1000, 32767)
    data = wav_bytes(pcm, rate=16000)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"

    import io
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.readframes(4) == pcm


# --- the happy path ------------------------------------------------------

def test_a_closed_capture_lands_on_disk_with_its_wav(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch(seq=0, pcm=struct.pack("<2h", 5, -5),
                              pcm_t0_ms=0.4))
    store.close_capture("ie1", close_cmd().meta)

    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["label"] == "shake"
    assert body["capture_id"] == "shake-021"
    assert body["n"] == 2
    assert body["truncated"] is False
    assert body["audio"]["file"] == "003.wav"
    assert (tmp_path / "SESSION" / "shake" / "003.wav").exists()


def test_trace_carries_no_room_provenance_when_store_has_none(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd().meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert "room_name" not in body
    assert "terrarium_config_version" not in body


def test_trace_carries_room_provenance_when_store_has_it(tmp_path):
    store = CaptureStore(root=tmp_path, session_id="SESSION", bit=BIT,
                         clock=FakeClock(),
                         provenance={"room_name": "atrium",
                                     "terrarium_config_version": "1-abcdef012345"})
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd().meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["room_name"] == "atrium"
    assert body["terrarium_config_version"] == "1-abcdef012345"


def test_t0_device_comes_from_the_open_command_not_a_default(tmp_path):
    """Design Rule 4, timestamps at the source: t0_device must be the
    device's own clock reading, or every trace's offsets would silently
    anchor to a meaningless 0.0 instead of a real moment."""
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd().meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["t0_device"] == 12345.678


def test_the_file_number_comes_from_the_series(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd(capture_id="tap-007", label="tap",
                                       series=7))
    store.append("ie1", batch(capture_id="tap-007"))
    store.close_capture("ie1", close_cmd(capture_id="tap-007").meta)
    assert (tmp_path / "SESSION" / "tap" / "007.json").exists()


def test_the_index_gains_one_line_per_closed_capture(tmp_path):
    store = make_store(tmp_path)
    for series, label in ((1, "tap"), (2, "shake")):
        cid = f"{label}-{series:03d}"
        store.open_capture("ie1", open_cmd(cid, label, series))
        store.append("ie1", batch(capture_id=cid))
        store.close_capture("ie1", close_cmd(cid).meta)

    lines = (tmp_path / "SESSION" / "index.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["label"] == "tap"
    assert first["path"] == "tap/001.json"
    assert first["n"] == 2
    assert first["truncated"] is False


def test_outputs_from_close_reach_the_trace(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd(outputs=[
        {"t_ms": -1800.0, "event": "countdown", "level": 0.6}]).meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["outputs"][0]["event"] == "countdown"


def test_a_motion_only_capture_writes_no_wav(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd().meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["audio"] is None
    assert not (tmp_path / "SESSION" / "shake" / "003.wav").exists()


def test_a_dropped_batch_is_stamped_as_a_gap(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch(seq=0))
    store.append("ie1", batch(seq=2))
    store.close_capture("ie1", close_cmd().meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["gaps"] == [{"expected": 1, "got": 2}]


# --- refusals ------------------------------------------------------------

def test_telemetry_for_an_unopened_capture_is_refused(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CaptureError) as exc:
        store.append("ie1", batch())
    assert "no open capture" in str(exc.value)


def test_telemetry_for_the_wrong_capture_id_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    with pytest.raises(CaptureError) as exc:
        store.append("ie1", batch(capture_id="tap-001"))
    assert "tap-001" in str(exc.value)


def test_opening_twice_on_one_device_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    with pytest.raises(CaptureError) as exc:
        store.open_capture("ie1", open_cmd())
    assert "already open" in str(exc.value)


def test_closing_nothing_is_refused(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CaptureError):
        store.close_capture("ie1", close_cmd().meta)


def test_a_stale_batch_is_refused_not_appended(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch(seq=0))
    store.append("ie1", batch(seq=1))
    with pytest.raises(CaptureError) as exc:
        store.append("ie1", batch(seq=1))
    assert "stale" in str(exc.value)


def test_two_devices_opening_the_same_label_series_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd("shake-021", "shake", 3))
    with pytest.raises(CaptureError) as exc:
        store.open_capture("ie2", open_cmd("shake-099", "shake", 3))
    assert "shake" in str(exc.value) and "3" in str(exc.value)
    assert "ie1" in str(exc.value)


def test_a_second_write_to_the_same_label_series_does_not_clobber_the_first(
        tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd("shake-021", "shake", 3))
    store.append("ie1", batch(capture_id="shake-021"))
    store.close_capture("ie1", close_cmd("shake-021").meta)

    original = (tmp_path / "SESSION" / "shake" / "003.json").read_text()

    store.open_capture("ie2", open_cmd("shake-099", "shake", 3))
    store.append("ie2", batch(capture_id="shake-099"))
    store.close_capture("ie2", close_cmd("shake-099").meta)  # must not raise

    assert store.failures == 1
    on_disk = (tmp_path / "SESSION" / "shake" / "003.json").read_text()
    assert on_disk == original
    assert json.loads(on_disk)["capture_id"] == "shake-021"


def test_two_devices_capture_independently(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd("shake-021", "shake", 21))
    store.open_capture("ie2", open_cmd("tap-004", "tap", 4))
    store.append("ie1", batch(capture_id="shake-021"))
    store.append("ie2", batch(capture_id="tap-004"))
    store.close_capture("ie1", close_cmd("shake-021").meta)
    store.close_capture("ie2", close_cmd("tap-004").meta)
    assert (tmp_path / "SESSION" / "shake" / "021.json").exists()
    assert (tmp_path / "SESSION" / "tap" / "004.json").exists()


# --- abandon, expiry, unload --------------------------------------------

def test_abandon_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.abandon("ie1", "mic permission denied")
    assert not (tmp_path / "SESSION" / "shake").exists()
    assert store.open_ids() == {}


def test_an_idle_capture_is_closed_truncated(tmp_path):
    """A phone that walks out of WiFi range must not leave a capture open
    forever."""
    clock = FakeClock()
    store = make_store(tmp_path, clock)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())

    clock.advance(5.0)
    assert store.expire(10.0) == []

    clock.advance(6.0)
    assert store.expire(10.0) == ["ie1"]
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["truncated"] is True
    assert store.open_ids() == {}


def test_activity_resets_the_idle_timer(tmp_path):
    clock = FakeClock()
    store = make_store(tmp_path, clock)
    store.open_capture("ie1", open_cmd())
    clock.advance(9.0)
    store.append("ie1", batch(seq=0))
    clock.advance(9.0)
    assert store.expire(10.0) == []


def test_truncate_all_closes_everything_still_open(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd("shake-021", "shake", 21))
    store.open_capture("ie2", open_cmd("tap-004", "tap", 4))
    store.append("ie1", batch(capture_id="shake-021"))
    store.append("ie2", batch(capture_id="tap-004"))
    assert sorted(store.truncate_all("bit unloaded")) == ["ie1", "ie2"]
    assert store.open_ids() == {}
    for label, num in (("shake", "021"), ("tap", "004")):
        body = json.loads(
            (tmp_path / "SESSION" / label / f"{num}.json").read_text())
        assert body["truncated"] is True
        assert body["notes"] == "bit unloaded"


# --- window enforcement ---------------------------------------------------

def _open_cmd_with_window(window_ms):
    return CaptureCommand(
        action="open", capture_id="shake-021",
        meta={"capture_id": "shake-021", "label": "shake", "series": 3,
              "window_ms": window_ms, "t0": 12345.678, "source": SOURCE})


def test_a_batch_past_window_plus_grace_is_refused_and_truncates(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", _open_cmd_with_window(1000.0))
    store.append("ie1", batch(seq=0, t_ms=[0.0, 10.0]))

    with pytest.raises(CaptureError) as exc:
        store.append("ie1", batch(seq=1, t_ms=[6001.0]))
    assert "window" in str(exc.value)
    assert "ie1" not in store.open_ids()

    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["truncated"] is True
    assert body["n"] == 2


def test_a_batch_within_window_plus_grace_is_accepted(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", _open_cmd_with_window(1000.0))
    store.append("ie1", batch(seq=0, t_ms=[0.0, 10.0]))

    store.append("ie1", batch(seq=1, t_ms=[6000.0]))  # exactly window+grace

    assert "ie1" in store.open_ids()


# --- status read-out -----------------------------------------------------

def test_counts_and_open_ids_track_the_session(tmp_path):
    store = make_store(tmp_path)
    assert store.counts() == {}
    store.open_capture("ie1", open_cmd())
    assert store.open_ids() == {"ie1": "shake-021"}
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd().meta)
    assert store.counts() == {"shake": 1}
    assert store.open_ids() == {}
    assert store.bytes_written > 0


# --- failure containment -------------------------------------------------

def test_a_failing_write_is_contained_and_counted(tmp_path, monkeypatch):
    """Boundary rule 2: a full disk must never wedge the tick loop."""
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())

    def boom(*_a, **_kw):
        raise OSError("no space left on device")

    monkeypatch.setattr("pathlib.Path.write_text", boom)
    monkeypatch.setattr("pathlib.Path.write_bytes", boom)

    store.close_capture("ie1", close_cmd().meta)      # must not raise
    assert store.failures == 1
    assert store.open_ids() == {}                      # capture still released


def test_an_index_append_failure_is_also_contained_and_counted(tmp_path, monkeypatch):
    """The trace .json and .wav writes are guarded (see the test above), but
    _append_index does a separate filesystem write of its own -- it must be
    just as contained, or a read-only index.jsonl would wedge the tick loop
    on the third of close_capture's three writes."""
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())

    real_open = Path.open

    def boom(self, *a, **kw):
        if self.name == "index.jsonl":
            raise OSError("read-only file system")
        return real_open(self, *a, **kw)

    monkeypatch.setattr("pathlib.Path.open", boom)

    store.close_capture("ie1", close_cmd().meta)      # must not raise
    assert store.failures == 1
    assert store.open_ids() == {}                      # capture still released
