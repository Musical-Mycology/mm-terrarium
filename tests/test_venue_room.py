"""The VENUE Room: LED bars + three fiber-optic engines, two RGBW
fixtures on two WLED controllers (spec 2026-09-25)."""

from control.builtins import builtin_functions
from control.terrarium_config import load_terrarium_config

CONFIG = load_terrarium_config("terrarium.toml")
VENUE = CONFIG.rooms["VENUE"].profile
# LEDs per fiber engine. Spec 2026-09-25 section 2, input I1: pending, so
# 1 until the hardware owner supplies it. rooms/VENUE.toml must agree.
N = 1


def test_venue_fiber_is_a_light_only_instrument():
    inst = CONFIG.instruments["venue_fiber"]
    assert inst.capabilities == frozenset({"light.surface"})
    assert set(builtin_functions(inst)) == {"flash", "stop"}


def test_venue_declares_bars_then_fiber_both_rgbw():
    assert [f.name for f in VENUE.fixtures] == ["bars", "fiber"]
    assert all(f.color_order == "RGBW" for f in VENUE.fixtures)
    assert CONFIG.rooms["VENUE"].node_id == "ROOM_VENUE_NODE"
    assert CONFIG.rooms["VENUE"].backends == ("devicelink", "array")


def test_venue_bars_match_the_demo_array():
    bars = VENUE.fixtures[0]
    (array,) = CONFIG.rooms["DEMO"].profile.fixtures
    assert bars.instrument.name == "venue_array"
    assert bars.pixel_count == 864
    assert bars.blocks == array.blocks
    assert bars.zones == array.zones


def test_venue_fiber_has_one_block_and_one_zone_per_engine():
    fiber = VENUE.fixtures[1]
    assert fiber.instrument.name == "venue_fiber"
    assert fiber.pixel_count == 3 * N
    assert [(b.name, b.start, b.count) for b in fiber.blocks] == [
        ("e1", 0, N), ("e2", N, N), ("e3", 2 * N, N)]
    assert [(z.name, z.start, z.count) for z in fiber.zones] == [
        ("b1", 0, N), ("b2", N, N), ("b3", 2 * N, N)]
    assert VENUE.channel_count == (864 + 3 * N) * 4
