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
    accepted_cues=("midi", "solid"),
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
    the implicit synthesis that would otherwise also demand audio.flsyn.
    Keeps TestBit's own "player" slot declared too (its inherited player
    Role still names it via `requires`) -- only the "room" slot's contract
    is being overridden here."""

    def instrument_requirements(self):
        return super().instrument_requirements() + (InstrumentRequirement(
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


# --- Task 6: carrier instruments gate role grants -------------------------


class PlayerSlotBit(Bit):
    """One non-ROOM role, "player", requiring gesture.tap -- TUNESHROOM
    satisfies this."""

    @property
    def role_table(self):
        role = Role(name="player", role_class=RoleClass.SHARED,
                    capacity=None, scored=False, requires="player")
        return RoleTable(roles={"player": role},
                         node_map={"node1": ("player",)})

    def instrument_requirements(self):
        return (InstrumentRequirement(
            slot="player", capabilities=frozenset({"gesture.tap"})),)


class UnsatisfiableSlotBit(Bit):
    """One non-ROOM role, "player", requiring light.surface -- TUNESHROOM
    lacks this, so every join must be refused."""

    @property
    def role_table(self):
        role = Role(name="player", role_class=RoleClass.SHARED,
                    capacity=None, scored=False, requires="player")
        return RoleTable(roles={"player": role},
                         node_map={"node1": ("player",)})

    def instrument_requirements(self):
        return (InstrumentRequirement(
            slot="player", capabilities=frozenset({"light.surface"})),)


class RequiresLessBit(Bit):
    """One non-ROOM role with no Role.requires at all -- join must behave
    exactly as before this slice: no slot/instrument gating or stamping."""

    @property
    def role_table(self):
        role = Role(name="player", role_class=RoleClass.SHARED,
                    capacity=None, scored=False)
        return RoleTable(roles={"player": role},
                         node_map={"node1": ("player",)})


def test_join_granted_when_carried_instrument_satisfies_slot():
    # DEFAULTSHROOM (the DeviceInfo.carried default -- 2026-08-31 carried-
    # instrument-wire) satisfies gesture.tap same as TUNESHROOM did.
    gs = GameServer({"PlayerSlotBit": PlayerSlotBit})
    gs.load_bit("PlayerSlotBit")
    gs.devices.hello("dev1", "device-one", "1.0")
    result = gs.join("dev1", "node1")
    assert result.granted
    assert result.slot == "player"
    assert result.instrument == "defaultshroom"
    assert result.config["slot"] == "player"
    assert result.config["instrument"] == "defaultshroom"


def test_join_refused_with_reason_when_contract_unsatisfied():
    gs = GameServer({"UnsatisfiableSlotBit": UnsatisfiableSlotBit})
    gs.load_bit("UnsatisfiableSlotBit")
    gs.devices.hello("dev1", "device-one", "1.0")
    result = gs.join("dev1", "node1")
    assert not result.granted
    assert "light.surface" in result.reason

    # The role's count must not have been consumed by the refused join --
    # a second (equally incapable, but that's not what's under test here)
    # device can still attempt and the role is still open, not at capacity.
    gs.devices.hello("dev2", "device-two", "1.0")
    result2 = gs.join("dev2", "node1")
    assert not result2.granted
    assert result2.reason == result.reason  # same contract reason, not "at capacity"


def test_role_without_requires_is_unchanged():
    gs = GameServer({"RequiresLessBit": RequiresLessBit})
    gs.load_bit("RequiresLessBit")
    gs.devices.hello("dev1", "device-one", "1.0")
    result = gs.join("dev1", "node1")
    assert result.granted
    assert result.slot is None
    assert result.instrument is None
    assert "slot" not in result.config
    assert "instrument" not in result.config


# --- Task 10: TestBit as the reference exemplar, through the full engine --

GESTURELESS_INSTRUMENT = Instrument(
    name="gestureless_widget",
    capabilities=frozenset({"light.pixels", "audio.samples"}),
    accepted_cues=("midi", "play", "solid", "mute"),
)


def test_testbit_player_join_is_granted_with_defaultshroom_carrier():
    # DEFAULTSHROOM (the DeviceInfo.carried default -- 2026-08-31 carried-
    # instrument-wire) satisfies light.pixels + gesture.tilt same as
    # TUNESHROOM did.
    gs = GameServer({"TestBit": TestBit})
    gs.load_bit("TestBit")
    gs.devices.hello("dev1", "device-one", "1.0")
    result = gs.join("dev1", "TEST_PLAYER_NODE")
    assert result.granted
    assert result.slot == "player"
    assert result.instrument == "defaultshroom"


def test_testbit_player_join_refused_when_carrier_lacks_gesture_tilt():
    gs = GameServer({"TestBit": TestBit})
    gs.load_bit("TestBit")
    gs.devices.hello("dev1", "device-one", "1.0")
    gs.devices.get("dev1").carried = GESTURELESS_INSTRUMENT
    result = gs.join("dev1", "TEST_PLAYER_NODE")
    assert not result.granted
    assert "gesture.tilt" in result.reason
