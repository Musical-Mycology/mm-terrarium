import pytest

from harness.websim_leds import BLOCK_PALETTE, WebSimLeds, identify_blocks_frame


def room_profile(name):
    from control.terrarium_config import load_terrarium_config
    return load_terrarium_config("terrarium.toml").rooms[name].profile


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


def test_clear_sends_a_room_width_all_zero_frame():
    from harness.websim_leds import WebSimLeds
    backend = FakeBackend()
    WebSimLeds(backend, channels=180).clear()
    assert backend.sent == [bytes(180)]


def test_identify_blocks_frame_paints_demo_blocks_distinctly():
    profile = room_profile("DEMO")
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
    profile = room_profile("TEST")
    frame = identify_blocks_frame(profile, "accent")
    assert len(frame) == 30 * 3
    r, g, b = BLOCK_PALETTE[0]
    assert frame[:3] == bytes((g, r, b))
    assert frame == frame[:3] * 30


def test_o2_shroom_exposes_identify_blocks():
    """The --identify-blocks build-out tool used to live on the websocket
    Room simulator (deleted in the o2lite cutover). It rides the o2lite
    Testshroom now, on the --no-join path, and never touches o2lite."""
    import inspect

    import harness.o2_shroom as o2_shroom

    source = inspect.getsource(o2_shroom)
    assert "--identify-blocks" in source
    assert "identify_blocks_frame(" in source


def test_on_show_sees_each_displayed_frame_with_the_clock_reading():
    backend = FakeBackend()
    seen = []
    leds = WebSimLeds(backend, channels=36,
                      on_show=lambda frame, now: seen.append((frame, now)),
                      clock=lambda: 42.5)

    leds.show(bytes(range(36)))

    assert backend.sent == [bytes(range(36))]
    assert seen == [(bytes(range(36)), 42.5)]


def test_on_show_without_a_clock_passes_none():
    seen = []
    leds = WebSimLeds(FakeBackend(), channels=36,
                      on_show=lambda frame, now: seen.append(now))
    leds.show(bytes(36))
    assert seen == [None]


def test_clear_does_not_call_on_show():
    seen = []
    leds = WebSimLeds(FakeBackend(), channels=36,
                      on_show=lambda frame, now: seen.append(frame))
    leds.clear()
    assert seen == []


def test_a_raising_on_show_never_stops_the_frame(caplog):
    backend = FakeBackend()

    def boom(frame, now):
        raise RuntimeError("tapper broke")

    leds = WebSimLeds(backend, channels=36, on_show=boom)
    leds.show(bytes(36))
    assert backend.sent == [bytes(36)]
