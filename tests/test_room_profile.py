"""RoomProfile: the Room's own N-fixture declaration. Pure -- no luxaeterna,
which is the point (see the design spec section 4's correction note)."""

import pathlib

import pytest

from control.cues import ROOM, TARGET
from control.functions import Function, FunctionKind, GeneratorSpec
from control.instrument import Instrument
from control.room_profile import (RoomBlock, RoomFixture, RoomProfile,
                                  RoomZone)
from control.terrarium_config import load_terrarium_config
from tests.instrument_fixtures import GENERIC_SURFACE


def _generator_instrument(name, data1=74, period=12.0, dev=ROOM):
    return Instrument(
        name=name, capabilities=frozenset({"light.surface"}),
        functions=(Function(
            name="glow", description="ambient glow", kind=FunctionKind.GENERATOR,
            generator=GeneratorSpec(dev=dev, status=0xB0, data1=data1,
                                    waveform="triangle", period=period,
                                    lo=0, hi=127)),))


def room_profile(name: str) -> RoomProfile:
    """This module's own tests exercise RoomProfile's shape validation
    plus, for the shipped TEST/DEMO rooms, that terrarium.toml still
    describes the same fixtures the old code-owned registry used to."""
    return load_terrarium_config("terrarium.toml").rooms[name].profile


def _fixture(name="main", blocks=None, zones=(), color_order="GRB",
            instrument=GENERIC_SURFACE):
    if blocks is None:
        blocks = (RoomBlock("b1", 0, 60),)
    return RoomFixture(name=name, color_order=color_order,
                       blocks=blocks, zones=zones, instrument=instrument)


def make_profile(instrument=GENERIC_SURFACE):
    return RoomProfile(surface_id="p", fixtures=(
        _fixture(instrument=instrument),))


def test_fixture_carries_its_instrument():
    profile = make_profile()
    assert profile.fixtures[0].instrument.name == "generic_surface"


def test_bad_fixture_instrument_fails_profile_construction():
    bad = Instrument(name="x", capabilities=frozenset({"nope.tag"}))
    with pytest.raises(ValueError, match="nope.tag"):
        make_profile(instrument=bad)


def test_two_fixtures_may_share_a_generator_lane():
    """Each fixture has its own session and lane space now (spec section
    3.5); the collision check moved to load_bit, after resolution. Both
    instruments declare dev=TARGET generators -- the only dev an
    instrument-owned generator may use (control/functions.py's
    _validate_generator) -- which is the case that genuinely cannot
    collide: each instrument's generator writes only its own declaring
    fixture's lane."""
    first = _generator_instrument("first", data1=74, dev=TARGET)
    second = _generator_instrument("second", data1=74, dev=TARGET)
    profile = RoomProfile(surface_id="r", fixtures=(
        _fixture("a", instrument=first), _fixture("b", instrument=second)))
    assert [f.name for f in profile.fixtures] == ["a", "b"]


def test_two_fixtures_with_distinct_generator_lanes_are_fine():
    first = _generator_instrument("first", data1=74, dev=TARGET)
    second = _generator_instrument("second", data1=75, dev=TARGET)   # distinct cc
    profile = RoomProfile(surface_id="p", fixtures=(
        _fixture(name="main", instrument=first),
        _fixture(name="accent", blocks=(RoomBlock("b2", 0, 30),),
                 instrument=second)))
    assert [f.name for f in profile.fixtures] == ["main", "accent"]


def test_test_room_declares_two_asymmetric_fixtures():
    profile = room_profile("TEST")
    assert profile.surface_id == "room_test"
    assert [f.name for f in profile.fixtures] == ["main", "accent"]
    assert [f.pixel_count for f in profile.fixtures] == [60, 30]


def test_main_fixture_keeps_the_original_three_zones():
    main = room_profile("TEST").fixtures[0]
    assert [z.name for z in main.zones] == ["left", "center", "right"]
    assert [(z.start, z.count) for z in main.zones] == [(0, 20), (20, 20), (40, 20)]


def test_accent_fixture_has_its_own_two_zones():
    accent = room_profile("TEST").fixtures[1]
    assert [z.name for z in accent.zones] == ["low", "high"]
    assert [(z.start, z.count) for z in accent.zones] == [(0, 15), (15, 15)]


def test_pixel_count_sums_every_fixture():
    assert room_profile("TEST").pixel_count == 90


def test_channel_count_is_three_per_pixel_of_the_whole_profile():
    assert room_profile("TEST").channel_count == 270


def test_color_order_is_the_shared_order():
    assert room_profile("TEST").color_order == "GRB"


def test_zones_are_namespaced_by_fixture():
    names = [z.name for z in room_profile("TEST").zones]
    assert names == ["main.left", "main.center", "main.right",
                     "accent.low", "accent.high"]


def test_zones_are_offset_into_the_concatenated_surface():
    zones = {z.name: (z.start, z.count) for z in room_profile("TEST").zones}
    assert zones["main.left"] == (0, 20)
    assert zones["main.right"] == (40, 20)
    assert zones["accent.low"] == (60, 15)   # offset past main's 60 px
    assert zones["accent.high"] == (75, 15)


def test_test_rooms_declared_zones_happen_to_tile_gaplessly():
    """A property of THIS profile's declared data, not an enforced
    invariant -- see test_zones_need_not_be_declared_in_position_order_or_
    tile_gaplessly below for what validation actually requires (no overlap,
    no overrun)."""
    profile = room_profile("TEST")
    cursor = 0
    for zone in profile.zones:
        assert zone.start == cursor, f"zone {zone.name} does not abut its predecessor"
        cursor += zone.count
    assert cursor == profile.pixel_count


def test_fixture_slices_are_channel_offsets_in_declaration_order():
    slices = room_profile("TEST").fixture_slices()
    assert slices == (("main", 0, 180), ("accent", 180, 90))


def test_primary_is_not_declared_here():
    """luxaeterna's SurfaceCapability.zone() synthesizes `primary` on demand,
    and harness/room_surface.py appends it. Declaring it here would make it a
    real zone that the Console would draw on top of every other one."""
    assert "primary" not in [z.name for z in room_profile("TEST").zones]


def test_demo_profile_matches_the_real_array_scale():
    """864 px = 6 m x 144 LED/m, the real Terrarium array
    (MM_HARDWARE_DESIGN.md section 7.1), one block per meter run."""
    profile = room_profile("DEMO")
    assert profile.surface_id == "room_demo"
    (array,) = profile.fixtures
    assert array.name == "array"          # matches tests/test_room_binding.py
    assert array.pixel_count == 864
    assert profile.channel_count == 2592
    assert [b.name for b in array.blocks] == ["m1", "m2", "m3", "m4", "m5", "m6"]
    assert all(b.count == 144 for b in array.blocks)
    assert [z.name for z in array.zones] == ["left", "center", "right"]
    assert all(z.count == 288 for z in array.zones)


def test_demo_zones_and_blocks_are_independent_axes():
    """3 zones over 6 blocks, deliberately not 1:1 -- zones target
    gameplay, blocks describe hardware (spec section 2.1)."""
    (array,) = room_profile("DEMO").fixtures
    zone_bounds = {(z.start, z.start + z.count) for z in array.zones}
    block_bounds = {(b.start, b.start + b.count) for b in array.blocks}
    assert zone_bounds != block_bounds


def test_profile_is_immutable():
    profile = room_profile("TEST")
    with pytest.raises(Exception):
        profile.fixtures = ()


def test_fixture_is_immutable():
    fixture = room_profile("TEST").fixtures[0]
    with pytest.raises(Exception):
        fixture.pixel_count = 99


def test_zone_is_a_plain_value():
    zone = RoomZone("left", 0, 20)
    assert (zone.name, zone.start, zone.count) == ("left", 0, 20)


def test_a_profile_needs_at_least_one_fixture():
    with pytest.raises(ValueError, match="no fixtures"):
        RoomProfile(surface_id="empty", fixtures=())


def test_duplicate_fixture_names_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        RoomProfile(surface_id="dup", fixtures=(
            _fixture(name="a", blocks=(RoomBlock("a", 0, 10),)),
            _fixture(name="a", blocks=(RoomBlock("a", 0, 10),))))


def test_mixed_color_order_across_fixtures_is_rejected():
    with pytest.raises(ValueError, match="color_order"):
        RoomProfile(surface_id="mixed", fixtures=(
            _fixture(name="a", color_order="GRB", blocks=(RoomBlock("a", 0, 10),)),
            _fixture(name="b", color_order="RGB", blocks=(RoomBlock("b", 0, 10),))))


def test_zones_overrunning_their_fixture_are_rejected():
    with pytest.raises(ValueError, match="overrun"):
        RoomProfile(surface_id="overrun", fixtures=(
            _fixture(name="a", blocks=(RoomBlock("a", 0, 10),),
                    zones=(RoomZone("all", 0, 20),)),))


def test_overlapping_zones_within_one_fixture_are_rejected():
    with pytest.raises(ValueError, match="overlap"):
        RoomProfile(surface_id="overlap", fixtures=(
            _fixture(name="a", blocks=(RoomBlock("a", 0, 10),),
                    zones=(RoomZone("x", 0, 5), RoomZone("y", 3, 5))),))


def test_zones_need_not_be_declared_in_position_order_or_tile_gaplessly():
    """Validation catches overlap and overrun (real configuration bugs), not
    declaration order or full coverage -- a fixture may leave pixels
    undeclared (no zone covers them) and may declare its zones in any order,
    exactly as harness/room_surface.py's adapter has always preserved
    whatever order a profile gives it (see tests/test_room_surface.py's
    test_zone_order_is_preserved_for_an_unsorted_profile, unaffected by this
    slice)."""
    profile = RoomProfile(surface_id="sparse-and-unsorted", fixtures=(
        _fixture(name="a", blocks=(RoomBlock("a", 0, 10),),
                zones=(RoomZone("b", 6, 2), RoomZone("a", 0, 2))),))
    assert [z.name for z in profile.zones] == ["a.b", "a.a"]   # order preserved, gap at 2-6 allowed


def test_fixture_pixel_count_is_sum_of_blocks():
    f = _fixture(blocks=(RoomBlock("b1", 0, 30), RoomBlock("b2", 30, 50),
                        RoomBlock("b3", 80, 20)))
    assert f.pixel_count == 100


def test_overlapping_blocks_are_refused():
    with pytest.raises(ValueError, match="overlapping blocks"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 0, 40), RoomBlock("b2", 30, 30))),))


def test_block_gaps_are_refused():
    # Blocks define the fixture's own extent, so unlike zones they must
    # tile it exactly: a gap means a pixel range no physical device drives.
    with pytest.raises(ValueError, match="do not tile"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 0, 30), RoomBlock("b2", 40, 30))),))


def test_block_not_starting_at_zero_is_refused():
    with pytest.raises(ValueError, match="do not tile"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 10, 30),)),))


def test_duplicate_block_names_are_refused():
    with pytest.raises(ValueError, match="duplicate block names"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 0, 30), RoomBlock("b1", 30, 30))),))


def test_a_block_over_170px_is_refused():
    with pytest.raises(ValueError, match="single-universe"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("big", 0, 171),)),))


def test_a_fixture_over_170px_is_fine_when_each_block_is_under():
    # The cap moved from whole-profile to per-block: this is the whole
    # point of blocks (spec section 2).
    profile = RoomProfile(surface_id="p", fixtures=(
        _fixture(blocks=(RoomBlock("b1", 0, 170), RoomBlock("b2", 170, 170))),))
    assert profile.pixel_count == 340


def test_zero_or_negative_block_count_is_refused():
    with pytest.raises(ValueError, match="positive"):
        RoomProfile(surface_id="p", fixtures=(
            _fixture(blocks=(RoomBlock("b1", 0, 0),)),))


def test_test_profile_declares_explicit_blocks():
    profile = room_profile("TEST")
    main, accent = profile.fixtures
    assert [b.name for b in main.blocks] == ["main"]
    assert main.pixel_count == 60
    assert [b.name for b in accent.blocks] == ["accent"]
    assert accent.pixel_count == 30


def test_no_control_module_imports_a_renderer_at_module_level():
    """Every control/ module must import, and the whole suite must run, with
    luxaeterna, pyarco and o2litepy absent. A MODULE-LEVEL import breaks that;
    a function-scoped one does not, because it runs only when called.

    Indented imports are deliberately not flagged. control/arco_process.py:37
    carries a lazy `from pyarco.arco_engine import arco` marked
    `# noqa: PLC0415 (lazy by design)` -- probing the Arco subprocess for
    readiness is that module's whole job. The repo states the stricter
    no-import-anywhere rule per-module where it applies (see control/audio.py's
    docstring), not package-wide. See the design spec section 4.
    """
    control_dir = pathlib.Path(__file__).resolve().parent.parent / "control"
    banned = ("luxaeterna", "pyarco", "o2litepy")
    offenders = []
    for path in sorted(control_dir.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if line[:1].isspace():          # indented: function-scoped, allowed
                continue
            if not (line.startswith("import ") or line.startswith("from ")):
                continue
            if any(line.split()[1].split(".")[0] == pkg for pkg in banned):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == [], ("control/ must have no module-level renderer "
                             "imports:\n" + "\n".join(offenders))
