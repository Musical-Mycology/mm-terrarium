"""CaptureBit: role table, verb dispatch, refusals, status, teardown.
Runs against a real CaptureStore over tmp_path. No luxaeterna -- this is the
Bit, not the transport -- so it lives in the core offline suite."""

import json

import pytest

from bits.capture.capture_bit import CAPTURE_NODE, CaptureBit
from capture.store import CaptureStore
from control.engine import GameServer
from control.roles import RoleClass

SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"}}

AXES = {"ax": [1.0, 1.0], "ay": [0.0, 0.0], "az": [9.8, 9.8],
        "gx": [0.0, 0.0], "gy": [0.0, 0.0], "gz": [0.0, 0.0]}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def open_args(dev="ie1", capture_id="shake-021", label="shake", series=3):
    # source is a fresh shallow copy per call: one test deletes a key from
    # it in place, and args[2]["source"] must not alias the shared module
    # level SOURCE dict or that mutation leaks into every later test.
    return [dev, "open", {"capture_id": capture_id, "label": label,
                          "series": series, "window_ms": 3000.0,
                          "t0": 12345.678, "source": dict(SOURCE)}]


def close_args(dev="ie1", capture_id="shake-021"):
    return [dev, "close", {"capture_id": capture_id, "n": 2, "ok": True,
                           "outputs": []}]


def telemetry_args(dev="ie1", capture_id="shake-021", seq=0):
    return [dev, 1234.5, {"capture_id": capture_id, "seq": seq,
                          "t_ms": [0.0, 10.0], **AXES}]


def make_bit(tmp_path, clock=None):
    store = CaptureStore(root=tmp_path, session_id="SESSION",
                         bit={"name": "capture", "version": "0.1"},
                         clock=clock or FakeClock())
    return CaptureBit(store=store), store


# --- declarations --------------------------------------------------------

def test_declares_one_unscored_recorder_role(tmp_path):
    bit, _ = make_bit(tmp_path)
    table = bit.role_table
    assert set(table.roles) == {"recorder"}
    recorder = table.roles["recorder"]
    assert recorder.role_class is RoleClass.SHARED
    assert recorder.scored is False
    assert recorder.capacity is None
    assert table.node_map == {CAPTURE_NODE: ["recorder"]}


def test_declares_no_light_or_audio(tmp_path):
    """The phone is the whole instrument here; the Bit has no light or audio
    consequence to decide. This also keeps the no-manifest path exercised."""
    bit, _ = make_bit(tmp_path)
    recorder = bit.role_table.roles["recorder"]
    assert recorder.light_manifest == {}
    assert recorder.ugen_manifest == {}
    assert recorder.welcome is None


def test_never_self_completes(tmp_path):
    bit, _ = make_bit(tmp_path)
    assert bit.update(1000.0) is False


def test_loads_cleanly_through_the_engine(tmp_path):
    """Role declarations are validated at load_bit; a typo would be a
    BitLoadError here rather than a device-side parse error later."""
    bit, _ = make_bit(tmp_path)
    gs = GameServer({"capture": lambda: bit})
    gs.load_bit("capture")
    assert gs.join("ie1", CAPTURE_NODE).granted is True


# --- the happy path through verb dispatch --------------------------------

def test_a_full_capture_round_trip_writes_a_trace(tmp_path):
    bit, _ = make_bit(tmp_path)
    gs = GameServer({"capture": lambda: bit})
    gs.load_bit("capture")
    gs.join("ie1", CAPTURE_NODE)

    assert gs.data("ie1", "capture", open_args()) is None
    assert gs.data("ie1", "telemetry", telemetry_args(seq=0)) is None
    assert gs.data("ie1", "telemetry", telemetry_args(seq=1)) is None
    assert gs.data("ie1", "capture", close_args()) is None

    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["label"] == "shake"
    assert body["n"] == 4


def test_abandon_drops_the_capture(tmp_path):
    bit, store = make_bit(tmp_path)
    bit._on_capture("ie1", open_args(), 0.0)
    bit._on_capture("ie1", ["ie1", "abandon",
                            {"capture_id": "shake-021", "reason": "cancelled"}], 0.0)
    assert store.open_ids() == {}
    assert not (tmp_path / "SESSION" / "shake").exists()


# --- refusals reach the device -------------------------------------------

def test_telemetry_without_an_open_capture_is_refused(tmp_path):
    bit, _ = make_bit(tmp_path)
    reason = bit._on_telemetry("ie1", telemetry_args(), 0.0)
    assert isinstance(reason, str)
    assert "no open capture" in reason


def test_a_malformed_batch_is_refused_with_the_parse_reason(tmp_path):
    bit, _ = make_bit(tmp_path)
    bit._on_capture("ie1", open_args(), 0.0)
    reason = bit._on_telemetry("ie1", ["ie1", 1.0, {"capture_id": "shake-021",
                                                    "seq": 0, "t_ms": []}], 0.0)
    assert isinstance(reason, str)
    assert "t_ms" in reason


def test_an_incomplete_source_block_is_refused(tmp_path):
    """Spec 7.1: a trace whose source is unknown is not usable."""
    bit, _ = make_bit(tmp_path)
    args = open_args()
    del args[2]["source"]["motion_stream"]
    reason = bit._on_capture("ie1", args, 0.0)
    assert isinstance(reason, str)
    assert "motion_stream" in reason


def test_a_refusal_reaches_the_device_as_an_error_reason(tmp_path):
    """End to end through Task 2's contract widening."""
    bit, _ = make_bit(tmp_path)
    gs = GameServer({"capture": lambda: bit})
    gs.load_bit("capture")
    gs.join("ie1", CAPTURE_NODE)
    reason = gs.data("ie1", "telemetry", telemetry_args())
    assert "no open capture" in reason


# --- expiry and teardown -------------------------------------------------

def test_update_expires_an_idle_capture(tmp_path):
    clock = FakeClock()
    bit, store = make_bit(tmp_path, clock)
    bit._on_capture("ie1", open_args(), 0.0)
    bit._on_telemetry("ie1", telemetry_args(), 0.0)

    clock.advance(5.0)
    bit.update(5.0)
    assert store.open_ids() == {"ie1": "shake-021"}

    clock.advance(6.0)
    bit.update(6.0)
    assert store.open_ids() == {}
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["truncated"] is True


def test_on_unload_truncates_whatever_is_still_open(tmp_path):
    bit, store = make_bit(tmp_path)
    bit._on_capture("ie1", open_args(), 0.0)
    bit._on_telemetry("ie1", telemetry_args(), 0.0)
    bit.on_unload()
    assert store.open_ids() == {}
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["truncated"] is True


# --- status --------------------------------------------------------------

def test_status_reports_the_session_for_the_console(tmp_path):
    bit, _ = make_bit(tmp_path)
    bit._on_capture("ie1", open_args(), 0.0)
    assert bit.status()["open"] == {"ie1": "shake-021"}

    bit._on_telemetry("ie1", telemetry_args(), 0.0)
    bit._on_capture("ie1", close_args(), 0.0)
    status = bit.status()
    assert status["session"] == "SESSION"
    assert status["captures"] == {"shake": 1}
    assert status["open"] == {}
    assert status["failures"] == 0
    assert status["bytes"] > 0
