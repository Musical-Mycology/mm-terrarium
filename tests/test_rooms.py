import pytest

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


def test_room_role_builds_capacity_one_room_class_role():
    name, role, node = room_role(RoomType.TEST)
    assert role.role_class == RoleClass.ROOM
    assert role.capacity == 1
    assert role.scored is False
    assert node == ROOM_NODE_IDS[RoomType.TEST]
    assert name == "room_test"


def test_room_role_carries_declared_manifests():
    _, role, _ = room_role(
        RoomType.DEMO,
        light_manifest={"instruments": [{"instrument": "aurora", "target": "primary"}]},
        ugen_manifest={"instruments": [{"instrument": "flsyn"}]})
    assert role.light_manifest["instruments"][0]["instrument"] == "aurora"
    assert role.ugen_manifest["instruments"][0]["instrument"] == "flsyn"


def test_room_defaults_to_unbound():
    room = Room(room_type=RoomType.TEST)
    assert room.bound_dev is None


def test_room_role_name_matches_room_role_helper():
    name, role, node = room_role(RoomType.DEMO)
    assert name == room_role_name(RoomType.DEMO)


def test_room_role_name_is_deterministic_per_type():
    assert room_role_name(RoomType.TEST) == "room_test"
    assert room_role_name(RoomType.DEMO) == "room_demo"
