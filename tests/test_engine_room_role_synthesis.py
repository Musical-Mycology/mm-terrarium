from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.rooms import Room, room_role_name
from bits.test.test_bit import TestBit


def make_room(name="TEST"):
    profile = RoomProfile(surface_id="room_x", fixtures=(
        RoomFixture(name="main", color_order="GRB",
                    blocks=(RoomBlock("main", 0, 10),),
                    zones=(RoomZone("all", 0, 10),)),))
    return Room(name=name, profile=profile, node_id=f"ROOM_{name}_NODE")


def test_load_bit_synthesizes_room_role_from_active_room():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = make_room()
    gs.load_bit("TestBit")
    rname = room_role_name("TEST")
    role = gs.registration.role_table.roles[rname]
    assert role.capacity == 1          # the room above has ONE fixture
    assert role.light_manifest         # TestBit's room_manifests light half
    assert gs.registration.role_table.node_map["ROOM_TEST_NODE"] == [rname]


def test_load_bit_without_room_declares_no_room_role():
    gs = GameServer({"TestBit": TestBit})
    gs.load_bit("TestBit")
    assert room_role_name("TEST") not in gs.registration.role_table.roles
