import pytest

from harness.room_simulator import WebSimLeds, build


class FakeBackend:
    def __init__(self):
        self.sent = []

    def send(self, frame, universe_id: int = 0) -> None:
        self.sent.append(bytes(frame))


def test_show_forwards_the_frame_to_the_backend():
    backend = FakeBackend()
    leds = WebSimLeds(backend)

    leds.show(bytes(range(36)))

    assert backend.sent == [bytes(range(36))]


def test_clear_sends_an_all_zero_frame():
    backend = FakeBackend()
    leds = WebSimLeds(backend)

    leds.clear()

    assert backend.sent == [bytes(36)]


def test_build_wires_the_client_and_backend():
    pytest.importorskip("luxaeterna.backends.websim")

    client, backend = build("sim-room", serve=False)

    assert client.dev == "sim-room"
    assert client.leds is not None
    assert backend.is_open is False  # build() doesn't open() -- main() does
    assert backend.label == "sim-room"
