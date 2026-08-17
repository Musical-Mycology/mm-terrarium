import pytest

from harness.room_simulator import WebSimLeds, build


class FakeBackend:
    def __init__(self):
        self.sent = []

    def send(self, frame, universe_id: int = 0) -> None:
        self.sent.append(bytes(frame))


def test_show_forwards_the_frame_to_the_backend():
    backend = FakeBackend()
    leds = WebSimLeds(backend, channels=36)

    leds.show(bytes(range(36)))

    assert backend.sent == [bytes(range(36))]


def test_clear_sends_an_all_zero_frame():
    backend = FakeBackend()
    leds = WebSimLeds(backend, channels=36)

    leds.clear()

    assert backend.sent == [bytes(36)]


def test_build_wires_the_client_and_backend():
    pytest.importorskip("luxaeterna.backends.websim")

    client, backend = build("sim-room", serve=False)

    assert client.dev == "sim-room"
    assert client.leds is not None
    assert backend.is_open is False  # build() doesn't open() -- main() does
    assert backend.label == "sim-room"


def test_build_uses_the_room_surface_not_the_shroom():
    pytest.importorskip("luxaeterna")
    from harness.room_simulator import build
    client, backend = build("sim-room", serve=False)
    # WebSimBackend stores its capability privately as _cap and exposes no
    # public accessor (luxaeterna backends/websim.py). Reaching for it is
    # deliberate: the alternative is asserting nothing about the surface the
    # simulator actually renders, which is the whole point of this task.
    assert backend._cap.surface_id == "room_test"
    assert backend._cap.pixel_count == 60


def test_build_widens_the_client_to_the_room_frame():
    pytest.importorskip("luxaeterna")
    from harness.room_simulator import build
    client, backend = build("sim-room", serve=False)
    assert client.expected_channels == 180


def test_clear_sends_a_room_width_all_zero_frame():
    from harness.room_simulator import WebSimLeds
    backend = FakeBackend()
    WebSimLeds(backend, channels=180).clear()
    assert backend.sent == [bytes(180)]
