import pytest

from control.room_profile import room_profile
from control.roles import Role, RoleClass
from control.rooms import (
    ROOM_NODE_IDS,
    Room,
    RoomResolutionError,
    RoomType,
    resolve_room_type,
    room_role,
    room_role_name,
)


def test_resolve_room_type_test_needs_no_array_backend():
    assert resolve_room_type(
        RoomType.TEST, array_backend_configured=False) == RoomType.TEST


def test_resolve_room_type_demo_succeeds_with_array_backend():
    assert resolve_room_type(
        RoomType.DEMO, array_backend_configured=True) == RoomType.DEMO


def test_resolve_room_type_demo_fails_without_array_backend():
    with pytest.raises(RoomResolutionError):
        resolve_room_type(RoomType.DEMO, array_backend_configured=False)


def test_room_role_capacity_matches_the_profiles_fixture_count():
    name, role, node = room_role(RoomType.TEST)
    assert role.role_class == RoleClass.ROOM
    assert role.capacity == len(room_profile(RoomType.TEST).fixtures)
    assert role.capacity == 2
    assert role.scored is False
    assert node == ROOM_NODE_IDS[RoomType.TEST]
    assert name == "room_test"


def test_room_role_carries_declared_manifests():
    _, role, _ = room_role(
        RoomType.TEST,
        light_manifest={"instruments": [{"instrument": "rainbow", "target": "primary"}]},
        ugen_manifest={"instruments": [{"instrument": "flsyn"}]})
    assert role.light_manifest["instruments"][0]["instrument"] == "rainbow"
    assert role.ugen_manifest["instruments"][0]["instrument"] == "flsyn"


def test_room_defaults_to_unbound():
    room = Room(room_type=RoomType.TEST)
    assert room.bound == {}
    assert room.fully_bound(room_profile(RoomType.TEST)) is False


def test_room_fully_bound_requires_every_fixture():
    room = Room(room_type=RoomType.TEST)
    room.bound["main"] = "sim-room-main"
    assert room.fully_bound(room_profile(RoomType.TEST)) is False
    room.bound["accent"] = "sim-room-accent"
    assert room.fully_bound(room_profile(RoomType.TEST)) is True


def test_room_role_name_matches_room_role_helper():
    name, role, node = room_role(RoomType.TEST)
    assert name == room_role_name(RoomType.TEST)


def test_room_role_name_is_deterministic_per_type():
    assert room_role_name(RoomType.TEST) == "room_test"
    assert room_role_name(RoomType.DEMO) == "room_demo"


def test_room_carries_profile_and_node_id():
    room = Room(room_type=RoomType.TEST,
                profile=room_profile(RoomType.TEST),
                node_id="ROOM_TEST_NODE")
    assert room.profile.pixel_count == 90
    assert not room.fully_bound()
    room.bound["main"] = "d1"
    room.bound["accent"] = "d2"
    assert room.fully_bound()
