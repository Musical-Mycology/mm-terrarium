"""array_smoke: builds a spanned array driver without touching the network."""

from __future__ import annotations

import pytest

pytest.importorskip("luxaeterna")

from harness.array_smoke import (          # noqa: E402
    TERRARIUM_MAX_AMPS,
    TERRARIUM_PIXELS,
    build,
    limited,
    travelling_wave,
)

HOST = "10.44.0.50"


# --- span sizing ---

def test_build_sizes_the_span_for_a_two_metre_bench_array():
    universe_set, loop = build(288, HOST)
    assert universe_set.span.pixel_count == 288
    assert universe_set.span.universe_count == 3
    assert len(universe_set.universes) == 3
    assert loop.running is False


def test_build_sizes_the_span_for_the_full_six_metre_array():
    universe_set, _ = build(TERRARIUM_PIXELS, HOST)
    assert universe_set.span.universe_count == 7


def test_build_honours_start_universe():
    universe_set, _ = build(288, HOST, start_universe=4)
    assert universe_set.span.universe_ids == [4, 5, 6]


def test_build_targets_the_requested_wled_host():
    _, loop = build(288, "10.44.0.77")
    assert loop.backend.host == "10.44.0.77"


def test_build_runs_at_the_dmx_refresh_rate():
    _, loop = build(288, HOST)
    assert loop.frame_interval == pytest.approx(1.0 / 44.0)


def test_build_uses_four_channels_per_pixel_for_rgbw():
    universe_set, _ = build(288, HOST)
    assert universe_set.span.channels_per_pixel == 4


# --- limiter wiring ---

def test_build_without_max_amps_installs_no_limiter():
    _, loop = build(TERRARIUM_PIXELS, HOST)
    assert loop.limiter is None


def test_build_with_max_amps_installs_a_limiter():
    _, loop = build(TERRARIUM_PIXELS, HOST, max_amps=TERRARIUM_MAX_AMPS)
    assert loop.limiter is not None
    assert loop.limiter.budget.max_amps == TERRARIUM_MAX_AMPS


def test_limiter_clamps_a_full_white_array_frame():
    _, loop = build(TERRARIUM_PIXELS, HOST, max_amps=TERRARIUM_MAX_AMPS)
    out = loop.limiter.apply(bytearray([255]) * 3456)
    assert max(out) <= 117
    assert loop.limiter.estimate_amps(out) <= TERRARIUM_MAX_AMPS


# --- the limited() paint wrapper ---

def test_limited_clamps_what_the_paint_hook_wrote():
    universe_set, loop = build(TERRARIUM_PIXELS, HOST,
                               max_amps=TERRARIUM_MAX_AMPS)
    hook = limited(lambda us: us.set_pixels(bytearray([255]) * 3456), loop)
    hook(universe_set)
    for universe in universe_set.universes:
        assert max(universe.get_frame()) <= 117


def test_limited_is_a_passthrough_when_no_limiter_is_installed():
    universe_set, loop = build(TERRARIUM_PIXELS, HOST)
    hook = limited(lambda us: us.fill_pixel(0, bytes([255, 255, 255, 255])), loop)
    hook(universe_set)
    assert universe_set.universes[0].get(0) == 255


def test_limited_still_calls_the_wrapped_paint():
    universe_set, loop = build(288, HOST, max_amps=TERRARIUM_MAX_AMPS)
    called = []
    hook = limited(lambda us: called.append(True), loop)
    hook(universe_set)
    assert called == [True]


# --- the demo pattern ---

def test_travelling_wave_writes_only_the_green_channel():
    universe_set, _ = build(288, HOST)
    travelling_wave(universe_set, 0.0)
    frame = universe_set.universes[0].get_frame()
    for px in range(128):
        r, g, b, w = frame[px * 4:px * 4 + 4]
        assert r == 0 and b == 0 and w == 0
        assert 0 <= g <= 90


def test_travelling_wave_covers_every_pixel_in_the_span():
    universe_set, _ = build(TERRARIUM_PIXELS, HOST)
    travelling_wave(universe_set, 0.0)
    last = universe_set.universes[6].get_frame()
    # pixel 863 lives at channel 380 of universe 6
    assert any(last[380:384])
