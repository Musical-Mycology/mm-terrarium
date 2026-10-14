import pytest

from control.pixel_wire import (CHANNELS_PER_PIXEL, WHITE_W, WIRE_ORDER,
                                rgbw, solid_frame)


def test_the_wire_is_rgbw_four_per_pixel():
    assert WIRE_ORDER == "RGBW"
    assert CHANNELS_PER_PIXEL == 4
    assert WHITE_W == (0, 0, 0, 255)


def test_rgb_pads_w_zero_and_rgbw_passes_through():
    assert rgbw((1, 2, 3)) == (1, 2, 3, 0)
    assert rgbw((1, 2, 3, 4)) == (1, 2, 3, 4)


@pytest.mark.parametrize("bad", [(1, 2), (1, 2, 3, 4, 5), ()])
def test_rgbw_refuses_other_lengths(bad):
    with pytest.raises(ValueError, match="colour must be"):
        rgbw(bad)


def test_solid_frame_is_r_g_b_w_in_order():
    assert solid_frame((10, 20, 30), 1.0, 8) == bytes([10, 20, 30, 0] * 2)
    assert solid_frame(WHITE_W, 1.0, 8) == bytes([0, 0, 0, 255] * 2)


def test_solid_frame_scales_and_clamps():
    assert solid_frame((200, 0, 0, 100), 0.5, 4) == bytes([100, 0, 0, 50])
    assert solid_frame((255, 255, 255, 255), 2.0, 4) == bytes([255] * 4)


def test_blackout_is_all_zero_including_w():
    assert solid_frame((0, 0, 0), 0.0, 48) == bytes(48)
    assert solid_frame(WHITE_W, 0.0, 48) == bytes(48)


def test_solid_frame_truncates_to_length():
    assert solid_frame((1, 2, 3), 1.0, 6) == bytes([1, 2, 3, 0, 1, 2])
