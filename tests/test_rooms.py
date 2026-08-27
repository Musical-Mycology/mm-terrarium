from control.roles import Role, RoleClass
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.rooms import Room, room_role, room_role_name

TEST_PROFILE = RoomProfile(
    surface_id="room_test",
    fixtures=(
        RoomFixture(
            name="main", color_order="GRB",
            blocks=(RoomBlock("main", 0, 60),),
            zones=(RoomZone("left", 0, 20),
                  RoomZone("center", 20, 20),
                  RoomZone("right", 40, 20))),
        RoomFixture(
            name="accent", color_order="GRB",
            blocks=(RoomBlock("accent", 0, 30),),
            zones=(RoomZone("low", 0, 15),
                  RoomZone("high", 15, 15))),
    ),
)


def make_room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE"):
    return Room(name=name, profile=profile, node_id=node_id)


def test_room_role_capacity_matches_the_profiles_fixture_count():
    room = make_room()
    name, role, node = room_role(room)
    assert role.role_class == RoleClass.ROOM
    assert role.capacity == len(room.profile.fixtures)
    assert role.capacity == 2
    assert role.scored is False
    assert node == "ROOM_TEST_NODE"
    assert name == "room_test"


def test_room_role_carries_declared_manifests():
    room = make_room()
    _, role, _ = room_role(
        room,
        light_manifest={"instruments": [{"instrument": "rainbow", "target": "primary"}]},
        ugen_manifest={"instruments": [{"instrument": "flsyn"}]})
    assert role.light_manifest["instruments"][0]["instrument"] == "rainbow"
    assert role.ugen_manifest["instruments"][0]["instrument"] == "flsyn"


def test_room_defaults_to_unbound():
    room = make_room()
    assert room.bound == {}
    assert room.fully_bound() is False


def test_room_fully_bound_requires_every_fixture():
    room = make_room()
    room.bound["main"] = "sim-room-main"
    assert room.fully_bound() is False
    room.bound["accent"] = "sim-room-accent"
    assert room.fully_bound() is True


def test_room_role_name_matches_room_role_helper():
    room = make_room()
    name, role, node = room_role(room)
    assert name == room_role_name(room.name)


def test_room_role_name_is_deterministic_per_type():
    assert room_role_name("TEST") == "room_test"
    assert room_role_name("DEMO") == "room_demo"


def test_room_carries_profile_and_node_id():
    room = make_room()
    assert room.profile.pixel_count == 90
    assert not room.fully_bound()
    room.bound["main"] = "d1"
    room.bound["accent"] = "d2"
    assert room.fully_bound()
