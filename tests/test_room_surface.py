"""The RoomProfile -> luxaeterna SurfaceCapability adapter."""

import pytest

pytest.importorskip("luxaeterna")

from control.room_profile import (RoomBlock, RoomFixture, RoomProfile,
                                  RoomZone, room_profile)
from control.rooms import RoomType
from harness.room_surface import to_capability


def test_scalar_fields_carry_across():
    cap = to_capability(room_profile(RoomType.TEST))
    assert cap.surface_id == "room_test"
    assert cap.pixel_count == 90
    assert cap.color_order == "GRB"


def test_declared_zones_carry_across_in_order():
    cap = to_capability(room_profile(RoomType.TEST))
    named = [(z.name, z.start, z.count) for z in cap.zones]
    assert named[:3] == [("main.left", 0, 20), ("main.center", 20, 20),
                         ("main.right", 40, 20)]


def test_primary_is_appended_spanning_the_whole_surface():
    """light_manifest instruments target "primary" by default (see
    bits/test_bit.py's Room declaration), so it has to resolve -- now over
    the whole concatenated surface, not one fixture."""
    cap = to_capability(room_profile(RoomType.TEST))
    primary = cap.zone("primary")
    assert (primary.start, primary.count) == (0, 90)


def test_declared_zones_resolve_by_name():
    cap = to_capability(room_profile(RoomType.TEST))
    assert (cap.zone("main.center").start, cap.zone("main.center").count) == (20, 20)


def test_adapter_does_not_mutate_the_profile():
    profile = room_profile(RoomType.TEST)
    before = len(profile.zones)
    to_capability(profile)
    assert len(profile.zones) == before


def test_a_profile_with_no_zones_still_yields_a_usable_primary():
    profile = RoomProfile(surface_id="bare", fixtures=(
        RoomFixture(name="only", color_order="GRB",
                   blocks=(RoomBlock("only", 0, 12),), zones=()),))
    cap = to_capability(profile)
    assert cap.zone("primary").count == 12


def test_zone_order_is_preserved_for_an_unsorted_profile():
    profile = RoomProfile(surface_id="odd", fixtures=(
        RoomFixture(name="only", color_order="GRB",
                   blocks=(RoomBlock("only", 0, 30),),
                   zones=(RoomZone("b", 10, 20), RoomZone("a", 0, 10))),))
    cap = to_capability(profile)
    # Namespaced now (RoomProfile.zones prefixes every zone with its
    # fixture's name), and still in declaration order, not position order --
    # this test's whole point, unchanged: names[:2] == ["only.b", "only.a"].
    assert [z.name for z in cap.zones][:2] == ["only.b", "only.a"]
