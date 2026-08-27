"""Task 5: Bit instrument requirements resolved at load_bit (spec section 4).

Covers the implicit "room" slot synthesized from room_manifests(), an
explicit "room" slot overriding that implication, min_pixels checks against
the profile's total pixel_count, optional requirements never blocking a
load, Role.requires naming an undeclared slot, and the no-requirements/
no-room-manifests baseline.
"""
import pytest

from bits.test.test_bit import TestBit
from control.bit import Bit
from control.engine import BitLoadError, GameServer
from control.instrument import Instrument, InstrumentRequirement
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.roles import Role, RoleClass, RoleTable
from control.rooms import Room
from control.state import State
from tests.instrument_fixtures import GENERIC_SURFACE

LIGHT_ONLY = Instrument(
    name="light_only_surface",
    capabilities=frozenset({"light.surface"}),
    accepted_triggers=("midi", "solid"),
)


def _light_only_profile(pixel_count=90):
    return RoomProfile(surface_id="room_light_only", fixtures=(
        RoomFixture(name="main", color_order="GRB",
                    blocks=(RoomBlock("main", 0, pixel_count),),
                    zones=(RoomZone("all", 0, pixel_count),),
                    instrument=LIGHT_ONLY),))


def _make_room(profile, name="TEST"):
    return Room(name=name, profile=profile, node_id=f"ROOM_{name}_NODE")


def _bare_role_table():
    return RoleTable(roles={}, node_map={})


class RoomAudioBit(TestBit):
    """TestBit's own room_manifests (inherited) declares a ugen manifest, so
    the implicit "room" slot needs audio.flsyn -- unsatisfiable against a
    light-only room."""


class ExplicitRoomSlotBit(TestBit):
    """Declares its own "room" slot needing only light.surface, overriding
    the implicit synthesis that would otherwise also demand audio.flsyn."""

    def instrument_requirements(self):
        return (InstrumentRequirement(
            slot="room", capabilities=frozenset({"light.surface"})),)


class MinPixelsBit(Bit):
    """A minimal Bit (no Room manifests) with one non-optional requirement
    demanding more pixels than the test room has."""

    @property
    def role_table(self):
        return _bare_role_table()

    def instrument_requirements(self):
        return (InstrumentRequirement(
            slot="surface", capabilities=frozenset({"light.surface"}),
            min_pixels=1000),)


class OptionalUnresolvedBit(Bit):
    """A non-empty, unsatisfiable requirement -- but optional=True, so it
    must never block a load."""

    @property
    def role_table(self):
        return _bare_role_table()

    def instrument_requirements(self):
        return (InstrumentRequirement(
            slot="surface", capabilities=frozenset({"audio.flsyn"}),
            optional=True),)


class TypoRequiresBit(Bit):
    """Declares slot "surface" but a Role requires the typo'd "surfac"."""

    def instrument_requirements(self):
        return (InstrumentRequirement(
            slot="surface", capabilities=frozenset({"light.surface"})),)

    @property
    def role_table(self):
        role = Role(name="player", role_class=RoleClass.SHARED,
                    capacity=None, scored=True, requires="surfac")
        return RoleTable(roles={"player": role}, node_map={})


class NoRequirementsBit(Bit):
    """No instrument_requirements(), no room_manifests() -- the baseline
    that must keep loading exactly as before this slice."""

    @property
    def role_table(self):
        return _bare_role_table()


def test_implicit_room_slot_fails_load_on_capability_gap():
    gs = GameServer({"RoomAudioBit": RoomAudioBit},
                     room_binding=RoomBindingRegistry())
    gs.room = _make_room(_light_only_profile())
    with pytest.raises(BitLoadError, match="audio.flsyn"):
        gs.load_bit("RoomAudioBit")
    assert gs.state is State.IDLE


def test_explicit_room_slot_overrides_the_implication():
    gs = GameServer({"ExplicitRoomSlotBit": ExplicitRoomSlotBit},
                     room_binding=RoomBindingRegistry())
    gs.room = _make_room(_light_only_profile())
    gs.load_bit("ExplicitRoomSlotBit")
    assert gs.state is State.SETUP
    assert gs._slot_requirements["room"].capabilities == frozenset(
        {"light.surface"})


def test_min_pixels_checks_profile_pixel_count():
    gs = GameServer({"MinPixelsBit": MinPixelsBit},
                     room_binding=RoomBindingRegistry())
    gs.room = _make_room(_light_only_profile(pixel_count=90))
    with pytest.raises(BitLoadError, match="1000"):
        gs.load_bit("MinPixelsBit")
    assert gs.state is State.IDLE


def test_optional_unresolved_slot_is_not_an_error():
    gs = GameServer({"OptionalUnresolvedBit": OptionalUnresolvedBit},
                     room_binding=RoomBindingRegistry())
    gs.room = _make_room(_light_only_profile())
    gs.load_bit("OptionalUnresolvedBit")
    assert gs.state is State.SETUP


def test_requires_naming_undeclared_slot_is_a_load_error():
    gs = GameServer({"TypoRequiresBit": TypoRequiresBit})
    with pytest.raises(BitLoadError, match="surfac"):
        gs.load_bit("TypoRequiresBit")
    assert gs.state is State.IDLE


def test_bit_with_no_requirements_and_no_room_manifests_loads():
    gs = GameServer({"NoRequirementsBit": NoRequirementsBit})
    gs.load_bit("NoRequirementsBit")
    assert gs.state is State.SETUP
    assert gs._slot_requirements == {}
