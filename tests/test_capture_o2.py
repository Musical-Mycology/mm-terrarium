"""CaptureBit end to end over the o2lite transport: hello, join, open,
chunked telemetry, close, and a trace on disk with every sample and every
PCM frame, no gaps. Carries tests/test_capture_smoke.py (deleted with the
websocket wire) onto the only device transport."""
import json
import wave

import pytest

pytest.importorskip("luxaeterna")

from bits.capture.capture_bit import CAPTURE_NODE, CaptureBit   # noqa: E402
from capture.store import CaptureStore                            # noqa: E402
from control.engine import GameServer                             # noqa: E402
from control.wire_json import dumps as wire_dumps                 # noqa: E402
from devicelink.agent import DeviceLinkAgent                      # noqa: E402
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport   # noqa: E402
from devicelink.protocol import (chunk_telemetry_batch,           # noqa: E402
                                 encode_telemetry_body)
from tests.test_telemetry_chunks import _hundred_ms_batch         # noqa: E402

DEV = "ie1"

SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"},
          "audio_stream": "record.startStream",
          "audio": {"rate": 16000, "bits": 16, "channels": 1}}


def _blob(value) -> bytes:
    return wire_dumps(value).encode("utf-8")


def _stack(tmp_path):
    fake = FakeO2Lite(now=100.0)
    fake.set_services("actl")                 # what arco.initialize() did
    transport = O2LiteTransport()
    transport.start(fake)
    store = CaptureStore(root=tmp_path, session_id="SESSION",
                         bit={"name": "capture", "version": "0.1"},
                         clock=fake.time_get)
    gs = GameServer({"capture": lambda: CaptureBit(store=store)},
                    clock=fake.time_get)
    agent = DeviceLinkAgent(gs, transport, clock=fake.time_get)
    gs.load_bit("capture")
    gs.run()
    fake.deliver("/game/hello", "ssss", (DEV, "capture-client", "1", "testshroom"))
    fake.deliver("/game/join", "ss", (DEV, CAPTURE_NODE))
    agent.poll()
    return fake, store, gs, agent


def _addressed(fake, address: str) -> list:
    return [sent for sent in fake.sent if sent[0] == address]


def test_a_chunked_capture_lands_on_disk_whole(tmp_path):
    fake, store, gs, agent = _stack(tmp_path)
    assert _addressed(fake, f"/{DEV}/deny") == []

    body = _hundred_ms_batch()                # 10 motion samples, 1600 PCM frames
    body["capture_id"] = "tap-001"            # match the capture opened below
    fake.deliver("/game/capture", "ssb", (DEV, "open", _blob(
        {"capture_id": "tap-001", "label": "tap", "series": 1,
         "window_ms": 1500.0, "t0": 100.0, "source": SOURCE})))
    chunks = chunk_telemetry_batch(body, first_seq=0)
    assert len(chunks) >= 2                   # it really was split
    for chunk in chunks:
        fake.deliver("/game/telemetry", "sfb",
                     (DEV, 100.0, encode_telemetry_body(chunk)))
    fake.deliver("/game/capture", "ssb", (DEV, "close", _blob(
        {"capture_id": "tap-001", "n": 10, "ok": True,
         "outputs": [{"t_ms": -1500.0, "event": "countdown", "level": 0.6}]})))
    agent.poll()

    assert _addressed(fake, f"/{DEV}/error") == []
    trace = json.loads((tmp_path / "SESSION" / "tap" / "001.json").read_text())
    assert trace["label"] == "tap"
    assert trace["capture_id"] == "tap-001"
    assert trace["n"] == 10
    assert trace["samples"]["t_ms"] == body["t_ms"]
    assert trace["samples"]["az"] == body["az"]
    assert trace["outputs"][0]["event"] == "countdown"
    assert trace["audio"]["t0_ms"] == body["pcm_t0_ms"]
    assert trace.get("gaps", []) == []
    with wave.open(str(tmp_path / "SESSION" / "tap" / "001.wav")) as wav:
        assert wav.getframerate() == 16000
        assert wav.readframes(wav.getnframes()) == body["pcm"]


def test_a_refusal_comes_back_as_an_error_over_o2lite(tmp_path):
    fake, store, gs, agent = _stack(tmp_path)
    chunk = chunk_telemetry_batch(_hundred_ms_batch(with_audio=False),
                                  first_seq=0)[0]
    fake.deliver("/game/telemetry", "sfb",
                 (DEV, 100.0, encode_telemetry_body(chunk)))
    agent.poll()

    errors = _addressed(fake, f"/{DEV}/error")
    assert len(errors) == 1
    # fake.sent rows are (address, timestamp, typespec, args)
    assert "no open capture" in errors[0][3][1]
