"""ArtNetFixtureSink: hold until due, coalesce, keepalive, power limit,
universe split, close-to-black, never block the tick. Spec 2026-09-23
sections 4.3, 5, 8."""

import socket
import threading
import time

import pytest

pytest.importorskip("luxaeterna")

from luxaeterna.backends.artnet import ArtNet
from luxaeterna.exceptions import BackendError
from luxaeterna.power import PowerBudget, PowerLimiter

from devicelink.artnet_sink import ArtNetFixtureSink
from tests.artnet_fake import StrictFakeArtNet


def _sink(px=4, *, max_amps=10.0, lead=0.0, keepalive=0.25, start_universe=0):
    now = [100.0]
    backend = StrictFakeArtNet()
    sink = ArtNetFixtureSink(
        name="DEMO-array", pixel_count=px, start_universe=start_universe,
        budget=PowerBudget(max_amps=max_amps), backend=backend,
        clock=lambda: now[0], lead=lead, keepalive=keepalive)
    return sink, backend, now


def _frame(px, value):
    return bytes([value]) * (px * 4)


def test_a_frame_is_held_until_its_when():
    sink, backend, now = _sink()
    sink.send_frame(_frame(4, 10), when=100.06)
    sink._service_once(100.05)
    assert backend.sent == []
    sink._service_once(100.06)
    assert backend.sent == [(0, _frame(4, 10) + bytes(512 - 16))]


def test_lead_sends_early():
    sink, backend, now = _sink(lead=0.02)
    sink.send_frame(_frame(4, 10), when=100.06)
    sink._service_once(100.03)
    assert backend.sent == []
    sink._service_once(100.041)                  # when - lead, past float noise
    assert len(backend.sent) == 1


def test_several_due_frames_coalesce_to_the_newest():
    sink, backend, now = _sink()
    for v in (1, 2, 3):
        sink.send_frame(_frame(4, v), when=100.0 + v / 100)
    sink._service_once(101.0)
    assert [p[:16] for _u, p in backend.sent] == [_frame(4, 3)]


def test_a_late_frame_is_sent_and_counted():
    sink, backend, now = _sink()
    sink.send_frame(_frame(4, 10), when=99.0)
    sink._service_once(100.0)
    assert len(backend.sent) == 1
    assert sink.stats()["late"] == 1


def test_keepalive_resends_the_last_frame_after_the_interval_and_not_before():
    sink, backend, now = _sink(keepalive=0.25)
    sink.send_frame(_frame(4, 10), when=100.0)
    sink._service_once(100.0)
    sink._service_once(100.2)
    assert len(backend.sent) == 1
    sink._service_once(100.25)
    assert len(backend.sent) == 2
    assert sink.stats()["keepalives"] == 1


def test_nothing_is_sent_before_the_first_frame():
    sink, backend, now = _sink()
    assert sink._service_once(100.0) == pytest.approx(100.25)
    assert backend.sent == []


def test_service_returns_the_earlier_of_next_due_and_keepalive():
    sink, backend, now = _sink(keepalive=0.25)
    sink.send_frame(_frame(4, 10), when=100.0)
    sink.send_frame(_frame(4, 11), when=100.1)
    assert sink._service_once(100.0) == pytest.approx(100.1)


def test_every_frame_passes_the_power_limiter():
    sink, backend, now = _sink(px=864, max_amps=5.0)
    sink.send_frame(_frame(864, 255), when=100.0)
    sink._service_once(100.0)
    payload = b"".join(p for _u, p in backend.sent)[:864 * 4]
    limiter = PowerLimiter(PowerBudget(max_amps=5.0))
    assert max(payload) <= limiter.hard_ceiling
    assert limiter.estimate_amps(payload) <= 5.0 + 1e-9


def test_864_rgbw_pixels_go_out_as_seven_full_universes():
    sink, backend, now = _sink(px=864, start_universe=3)
    sink.send_frame(_frame(864, 10), when=100.0)
    sink._service_once(100.0)
    assert [u for u, _p in backend.sent] == [3, 4, 5, 6, 7, 8, 9]
    assert all(len(p) == 512 for _u, p in backend.sent)
    last = backend.sent[-1][1]
    assert last[:384] == bytes([10]) * 384 and last[384:] == bytes(128)


def test_a_wrong_width_frame_is_dropped_never_truncated(caplog):
    sink, backend, now = _sink()
    with caplog.at_level("WARNING"):
        sink.send_frame(bytes(15), when=100.0)
        sink.send_frame(bytes(15), when=100.0)
    sink._service_once(100.0)
    assert backend.sent == []
    assert sum("width" in r.message for r in caplog.records) == 1


def test_a_send_error_is_counted_and_retried_on_keepalive():
    sink, backend, now = _sink()
    backend.fail = BackendError("boom")
    sink.send_frame(_frame(4, 10), when=100.0)
    sink._service_once(100.0)
    assert sink.stats()["send_errors"] == 1
    backend.fail = None
    sink._service_once(100.25)
    assert len(backend.sent) == 1


def test_close_sends_black_and_closes_the_backend():
    sink, backend, now = _sink()
    sink.send_frame(_frame(4, 10), when=100.0)
    sink._service_once(100.0)
    sink.close()
    assert backend.sent[-1] == (0, bytes(512))
    assert backend.closes == 1


def test_send_frame_never_blocks_while_the_backend_is_stalled():
    now = [0.0]
    backend = StrictFakeArtNet()
    backend.gate = threading.Event()
    sink = ArtNetFixtureSink(name="t", pixel_count=4, start_universe=0,
                             budget=PowerBudget(max_amps=10.0), backend=backend,
                             clock=time.monotonic)
    sink.start()
    try:
        sink.send_frame(_frame(4, 1), when=time.monotonic())
        time.sleep(0.05)                       # sender is now inside send()
        t0 = time.monotonic()
        for v in range(50):
            sink.send_frame(_frame(4, v), when=time.monotonic())
        assert time.monotonic() - t0 < 0.05
    finally:
        backend.gate.set()
        sink.close()


def test_the_thread_starts_and_stops():
    backend = StrictFakeArtNet()
    sink = ArtNetFixtureSink(name="t", pixel_count=4, start_universe=0,
                             budget=PowerBudget(max_amps=10.0), backend=backend,
                             clock=time.monotonic, keepalive=0.02)
    sink.start()
    sink.send_frame(_frame(4, 10), when=time.monotonic())
    deadline = time.monotonic() + 2.0
    while sink.stats()["keepalives"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    sink.close()
    assert sink.stats()["frames_sent"] >= 1
    assert sink.stats()["keepalives"] >= 2
    assert not any(t.name == "artnet-t" for t in threading.enumerate())


@pytest.mark.parametrize("payload", [bytes(0), bytes(1), bytes(2), bytes(511),
                                     bytes(512), bytes(514)])
def test_the_fake_refuses_exactly_what_the_real_backend_refuses(payload):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    real = ArtNet(host="127.0.0.1", port=rx.getsockname()[1])
    fake = StrictFakeArtNet()
    try:
        for backend in (real, fake):
            with pytest.raises(BackendError):
                backend.send(payload)          # before open(): both refuse
            backend.open()
        outcomes = []
        for backend in (real, fake):
            try:
                backend.send(payload)
                outcomes.append("ok")
            except BackendError:
                outcomes.append("refused")
        assert outcomes[0] == outcomes[1]
    finally:
        real.close()
        rx.close()
