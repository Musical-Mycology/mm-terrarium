"""ChaseBit: the reference cross-fixture effect, split out of TestBit so
TestBit stays loadable on DEMO (whose one "array" fixture chase does not
name). Written to the TEST Room spec (fixtures "main" and "accent")."""

import pytest

from control.bit_registry import BitRegistry
from control.cues import fixture_dev
from control.engine import BitLoadError, GameServer
from control.room_binding import RoomBindingRegistry
from control.rooms import Room
from control.state import State
from control.terrarium_config import load_terrarium_config

from bits.chase.chase_bit import ChaseBit

TEST_PROFILE = load_terrarium_config("terrarium.toml").rooms["TEST"].profile
DEMO_PROFILE = load_terrarium_config("terrarium.toml").rooms["DEMO"].profile


def test_chase_steps_main_then_accent():
    fn = ChaseBit().function_table.functions["chase"]
    assert [(s.offset, s.cue[0]) for s in fn.script] == [
        (0.0, fixture_dev("main")), (0.5, fixture_dev("accent")),
        (1.0, fixture_dev("main")), (1.0, fixture_dev("accent"))]


def test_chase_bit_loads_on_the_test_room():
    gs = GameServer({"ChaseBit": ChaseBit}, room_binding=RoomBindingRegistry())
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    gs.load_bit("ChaseBit")
    assert gs.state is State.SETUP


def test_chase_bit_is_refused_on_the_demo_room():
    gs = GameServer({"ChaseBit": ChaseBit}, room_binding=RoomBindingRegistry())
    gs.room = Room(name="DEMO", profile=DEMO_PROFILE, node_id="ROOM_DEMO_NODE")
    with pytest.raises(BitLoadError) as exc:
        gs.load_bit("ChaseBit")
    assert "main" in str(exc.value) and "accent" in str(exc.value)
    assert gs.state is State.IDLE


def test_chase_bit_package_parses_with_test_only_room_types():
    reg = BitRegistry.discover()
    assert reg.errors == []
    assert "ChaseBit" in reg.packages
    pkg = reg.packages["ChaseBit"]
    assert pkg.config.launch.room_types == ("TEST",)
    cls = reg.bit_class("ChaseBit")
    assert cls is ChaseBit
