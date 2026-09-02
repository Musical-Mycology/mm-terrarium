import pytest

from control.cues import fixture_dev
from control.engine import BitLoadError, GameServer
from control.functions import (Condition, ConditionSource, Function, FunctionKind,
                               FunctionTable, FunctionTarget, GeneratorSpec, ScriptStep)
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.state import State
from control.terrarium_config import load_terrarium_config
from tests.instrument_fixtures import GENERIC_SURFACE
from control.rooms import Room, room_role_name
from bits.test.test_bit import TestBit

TEST_PROFILE = load_terrarium_config("terrarium.toml").rooms["TEST"].profile


def make_room(name="TEST"):
    profile = RoomProfile(surface_id="room_x", fixtures=(
        RoomFixture(name="main", color_order="GRB",
                    blocks=(RoomBlock("main", 0, 10),),
                    zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),))
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


class _FixtureBit(TestBit):
    """TestBit plus one declaration that names a fixture the TEST Room lacks."""
    @property
    def function_table(self):
        table = super().function_table
        table.functions["ceiling"] = Function(
            name="ceiling", description="d", target=FunctionTarget.ROOM,
            condition=Condition(name="c", description="d",
                                source=ConditionSource.ADMIN_MANUAL),
            script=(ScriptStep(0.0, (fixture_dev("ceiling"), 0xB0, 74, 1)),))
        return table


def test_load_bit_refuses_a_bit_that_addresses_a_missing_fixture():
    gs = GameServer({"B": _FixtureBit})
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="N")
    with pytest.raises(BitLoadError, match="ceiling"):
        gs.load_bit("B")
    assert gs.state is State.IDLE


class _CollidingBit(TestBit):
    """TestBit's drift (ROOM cc:74) plus a fixture generator on accent cc:74:
    both write the accent's cc:74 lane once resolved."""
    @property
    def function_table(self):
        table = super().function_table
        table.functions["accent_drift"] = Function(
            name="accent_drift", description="d", kind=FunctionKind.GENERATOR,
            generator=GeneratorSpec(dev=fixture_dev("accent"), status=0xB0, data1=74,
                                    waveform="triangle", period=3.0))
        return table


def test_load_bit_refuses_generators_that_collide_after_resolution():
    gs = GameServer({"B": _CollidingBit})
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="N")
    with pytest.raises(BitLoadError, match="lane"):
        gs.load_bit("B")


def test_load_bit_with_no_room_skips_the_fixture_contract():
    gs = GameServer({"B": _FixtureBit})
    gs.load_bit("B")     # roomless boot: nothing to check against
    assert gs.state is State.SETUP
