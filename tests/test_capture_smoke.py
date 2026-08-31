"""End to end: a synthetic device drives hello -> join -> open -> telemetry
-> close through the real DeviceLinkAgent and lands a real trace file.

This is the regression that makes the whole capture path exercisable with no
phone, no microphone and no hardware."""

import json
import struct

import pytest

# harness.capture_smoke imports devicelink.agent, which imports
# harness.device_bridge and therefore luxaeterna. Guarded exactly as
# tests/test_devicelink_agent.py does, so the core suite still collects
# without the sibling checkout.
pytest.importorskip("luxaeterna")

from bits.capture.capture_bit import CAPTURE_NODE
from harness.capture_smoke import build
from tests.test_devicelink_agent import FakeServer

SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"},
          "audio_stream": "record.startStream",
          "audio": {"rate": 16000, "bits": 16, "channels": 1}}


def _axes(n):
    return {"ax": [1.0] * n, "ay": [0.0] * n, "az": [9.8] * n,
            "gx": [0.0] * n, "gy": [0.0] * n, "gz": [0.0] * n}


def test_a_synthetic_device_produces_a_real_trace_on_disk(tmp_path):
    import base64

    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from bits.capture.capture_bit import CaptureBit
    from capture.store import CaptureStore

    store = CaptureStore(root=tmp_path, session_id="SESSION",
                         bit={"name": "capture", "version": "0.1"})
    gs = GameServer({"capture": lambda: CaptureBit(store=store)})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    gs.load_bit("capture")
    gs.run()

    client = object()
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", ["ie1", "capture-client", "1"])
    server.deliver(client, "/game/join", "ss", ["ie1", CAPTURE_NODE])
    agent.poll()
    assert server.addressed("/ie1/deny") == []

    server.deliver(client, "/game/capture", "ssb", [
        "ie1", "open", {"capture_id": "tap-001", "label": "tap", "series": 1,
                        "window_ms": 1500.0, "t0": 100.0, "source": SOURCE}])
    pcm = struct.pack("<4h", 0, 900, -900, 0)
    server.deliver(client, "/game/telemetry", "sfb", [
        "ie1", 100.0, {"capture_id": "tap-001", "seq": 0,
                       "t_ms": [0.0, 10.0, 20.0], **_axes(3),
                       "pcm": base64.b64encode(pcm).decode(),
                       "pcm_t0_ms": 0.5}])
    server.deliver(client, "/game/capture", "ssb", [
        "ie1", "close", {"capture_id": "tap-001", "n": 3, "ok": True,
                         "outputs": [{"t_ms": -1500.0, "event": "countdown",
                                      "level": 0.6}]}])
    agent.poll()

    assert server.addressed("/ie1/error") == []
    body = json.loads((tmp_path / "SESSION" / "tap" / "001.json").read_text())
    assert body["label"] == "tap"
    assert body["capture_id"] == "tap-001"
    assert body["n"] == 3
    assert body["samples"]["az"] == [9.8, 9.8, 9.8]
    assert body["outputs"][0]["event"] == "countdown"
    assert body["audio"]["t0_ms"] == 0.5
    assert (tmp_path / "SESSION" / "tap" / "001.wav").exists()


def test_a_refusal_comes_back_as_an_error_frame(tmp_path):
    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from bits.capture.capture_bit import CaptureBit
    from capture.store import CaptureStore

    store = CaptureStore(root=tmp_path, session_id="SESSION",
                         bit={"name": "capture", "version": "0.1"})
    gs = GameServer({"capture": lambda: CaptureBit(store=store)})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    gs.load_bit("capture")
    gs.run()

    client = object()
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", ["ie1", "capture-client", "1"])
    server.deliver(client, "/game/join", "ss", ["ie1", CAPTURE_NODE])
    server.deliver(client, "/game/telemetry", "sfb", [
        "ie1", 100.0, {"capture_id": "tap-001", "seq": 0,
                       "t_ms": [0.0], **_axes(1)}])
    agent.poll()

    errors = server.addressed("/ie1/error")
    assert len(errors) == 1
    assert "no open capture" in errors[0]["args"][1]


def test_build_wires_the_store_to_the_bit(tmp_path):
    gs, server, agent, store = build(host="127.0.0.1", port=0,
                                     capture_dir=tmp_path,
                                     session_id="SESSION")
    try:
        assert store.session_dir == tmp_path / "SESSION"
        gs.load_bit("capture")
        assert gs.bit.status()["session"] == "SESSION"
    finally:
        server.stop()
