"""RoomProfile: the Room's own N-fixture declaration. Pure -- no luxaeterna,
which is the point (see the design spec section 4's correction note)."""

import pathlib

import pytest

from control.room_profile import (ROOM_PROFILES, RoomFixture, RoomProfile,
                                  RoomZone, room_profile)
from control.rooms import RoomType


def test_test_room_declares_two_asymmetric_fixtures():
    profile = room_profile(RoomType.TEST)
    assert profile.surface_id == "room_test"
    assert [f.name for f in profile.fixtures] == ["main", "accent"]
    assert [f.pixel_count for f in profile.fixtures] == [60, 30]


def test_main_fixture_keeps_the_original_three_zones():
    main = room_profile(RoomType.TEST).fixtures[0]
    assert [z.name for z in main.zones] == ["left", "center", "right"]
    assert [(z.start, z.count) for z in main.zones] == [(0, 20), (20, 20), (40, 20)]


def test_accent_fixture_has_its_own_two_zones():
    accent = room_profile(RoomType.TEST).fixtures[1]
    assert [z.name for z in accent.zones] == ["low", "high"]
    assert [(z.start, z.count) for z in accent.zones] == [(0, 15), (15, 15)]


def test_pixel_count_sums_every_fixture():
    assert room_profile(RoomType.TEST).pixel_count == 90


def test_channel_count_is_three_per_pixel_of_the_whole_profile():
    assert room_profile(RoomType.TEST).channel_count == 270


def test_color_order_is_the_shared_order():
    assert room_profile(RoomType.TEST).color_order == "GRB"


def test_zones_are_namespaced_by_fixture():
    names = [z.name for z in room_profile(RoomType.TEST).zones]
    assert names == ["main.left", "main.center", "main.right",
                     "accent.low", "accent.high"]


def test_zones_are_offset_into_the_concatenated_surface():
    zones = {z.name: (z.start, z.count) for z in room_profile(RoomType.TEST).zones}
    assert zones["main.left"] == (0, 20)
    assert zones["main.right"] == (40, 20)
    assert zones["accent.low"] == (60, 15)   # offset past main's 60 px
    assert zones["accent.high"] == (75, 15)


def test_test_rooms_declared_zones_happen_to_tile_gaplessly():
    """A property of THIS profile's declared data, not an enforced
    invariant -- see test_zones_need_not_be_declared_in_position_order_or_
    tile_gaplessly below for what validation actually requires (no overlap,
    no overrun)."""
    profile = room_profile(RoomType.TEST)
    cursor = 0
    for zone in profile.zones:
        assert zone.start == cursor, f"zone {zone.name} does not abut its predecessor"
        cursor += zone.count
    assert cursor == profile.pixel_count


def test_fixture_slices_are_channel_offsets_in_declaration_order():
    slices = room_profile(RoomType.TEST).fixture_slices()
    assert slices == (("main", 0, 180), ("accent", 180, 90))


def test_primary_is_not_declared_here():
    """luxaeterna's SurfaceCapability.zone() synthesizes `primary` on demand,
    and harness/room_surface.py appends it. Declaring it here would make it a
    real zone that the Console would draw on top of every other one."""
    assert "primary" not in [z.name for z in room_profile(RoomType.TEST).zones]


def test_demo_room_raises_rather_than_downgrading():
    """Matches resolve_room_type()'s existing fail-hard-never-downgrade
    contract. DEMO's backend is a deferred follow-up spec."""
    with pytest.raises(NotImplementedError):
        room_profile(RoomType.DEMO)


def test_profile_is_immutable():
    profile = room_profile(RoomType.TEST)
    with pytest.raises(Exception):
        profile.fixtures = ()


def test_fixture_is_immutable():
    fixture = room_profile(RoomType.TEST).fixtures[0]
    with pytest.raises(Exception):
        fixture.pixel_count = 99


def test_every_room_type_key_maps_to_a_room_profile():
    for key, value in ROOM_PROFILES.items():
        assert isinstance(key, RoomType)
        assert isinstance(value, RoomProfile)


def test_zone_is_a_plain_value():
    zone = RoomZone("left", 0, 20)
    assert (zone.name, zone.start, zone.count) == ("left", 0, 20)


def test_a_profile_needs_at_least_one_fixture():
    with pytest.raises(ValueError, match="no fixtures"):
        RoomProfile(surface_id="empty", fixtures=())


def test_duplicate_fixture_names_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        RoomProfile(surface_id="dup", fixtures=(
            RoomFixture("a", 10, "GRB", ()), RoomFixture("a", 10, "GRB", ())))


def test_mixed_color_order_across_fixtures_is_rejected():
    with pytest.raises(ValueError, match="color_order"):
        RoomProfile(surface_id="mixed", fixtures=(
            RoomFixture("a", 10, "GRB", ()), RoomFixture("b", 10, "RGB", ())))


def test_zones_overrunning_their_fixture_are_rejected():
    with pytest.raises(ValueError, match="overrun"):
        RoomProfile(surface_id="overrun", fixtures=(
            RoomFixture("a", 10, "GRB", (RoomZone("all", 0, 20),)),))


def test_overlapping_zones_within_one_fixture_are_rejected():
    with pytest.raises(ValueError, match="overlap"):
        RoomProfile(surface_id="overlap", fixtures=(
            RoomFixture("a", 10, "GRB",
                       (RoomZone("x", 0, 5), RoomZone("y", 3, 5))),))


def test_zones_need_not_be_declared_in_position_order_or_tile_gaplessly():
    """Validation catches overlap and overrun (real configuration bugs), not
    declaration order or full coverage -- a fixture may leave pixels
    undeclared (no zone covers them) and may declare its zones in any order,
    exactly as harness/room_surface.py's adapter has always preserved
    whatever order a profile gives it (see tests/test_room_surface.py's
    test_zone_order_is_preserved_for_an_unsorted_profile, unaffected by this
    slice)."""
    profile = RoomProfile(surface_id="sparse-and-unsorted", fixtures=(
        RoomFixture("a", 10, "GRB",
                   (RoomZone("b", 6, 2), RoomZone("a", 0, 2))),))
    assert [z.name for z in profile.zones] == ["a.b", "a.a"]   # order preserved, gap at 2-6 allowed


def test_a_profile_over_the_single_universe_cap_is_rejected():
    with pytest.raises(ValueError, match="170"):
        RoomProfile(surface_id="huge", fixtures=(
            RoomFixture("a", 171, "GRB", ()),))


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
