import pytest

from control.room_profile import room_profile
from control.rooms import RoomType
from harness.room_simulator import BLOCK_PALETTE, WebSimLeds, build, \
    identify_blocks_frame


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
    client, backend = build("dev", room_type="TEST", fixture="main", serve=False)
    assert backend._cap.pixel_count == 60


def test_build_widens_the_client_to_the_room_frame():
    client, backend = build("dev", room_type="TEST", fixture="main", serve=False)
    assert client.expected_channels == 180


def test_build_scopes_to_the_named_fixture_not_the_whole_profile():
    client, backend = build("dev", room_type="TEST", fixture="accent", serve=False)
    assert backend._cap.pixel_count == 30
    assert client.expected_channels == 90


def test_clear_sends_a_room_width_all_zero_frame():
    from harness.room_simulator import WebSimLeds
    backend = FakeBackend()
    WebSimLeds(backend, channels=180).clear()
    assert backend.sent == [bytes(180)]


def test_identify_blocks_frame_paints_demo_blocks_distinctly():
    profile = room_profile(RoomType.DEMO)
    frame = identify_blocks_frame(profile, "array")
    (array,) = profile.fixtures
    assert len(frame) == array.pixel_count * 3          # 2592
    # First pixel of each 144px block carries that block's own palette
    # color, GRB order per the profile.
    for i, block in enumerate(array.blocks):
        r, g, b = BLOCK_PALETTE[i % len(BLOCK_PALETTE)]
        offset = block.start * 3
        assert frame[offset:offset + 3] == bytes((g, r, b))
    # Adjacent blocks differ at their boundary.
    for prev, cur in zip(array.blocks, array.blocks[1:]):
        last_of_prev = (cur.start - 1) * 3
        first_of_cur = cur.start * 3
        assert frame[last_of_prev:last_of_prev + 3] != \
            frame[first_of_cur:first_of_cur + 3]


def test_identify_blocks_frame_works_for_a_single_block_fixture():
    profile = room_profile(RoomType.TEST)
    frame = identify_blocks_frame(profile, "accent")
    assert len(frame) == 30 * 3
    r, g, b = BLOCK_PALETTE[0]
    assert frame[:3] == bytes((g, r, b))
    assert frame == frame[:3] * 30
