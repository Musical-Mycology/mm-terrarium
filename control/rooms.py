"""Room: the physical (or simulated) LED/mic/speaker hardware a Terrarium
installation offers. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
sections 3-4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from control.roles import Role, RoleClass

if TYPE_CHECKING:
    from control.registration import RegistrationState


class RoomType(Enum):
    TEST = auto()
    DEMO = auto()


class RoomResolutionError(Exception):
    """Raised when a target RoomType's recipe isn't satisfiable. Resolution
    never downgrades to a lesser type -- see design spec section 3."""


@dataclass(frozen=True)
class RoomRecipe:
    requires_array_backend: bool


ROOM_RECIPES: dict[RoomType, RoomRecipe] = {
    RoomType.TEST: RoomRecipe(requires_array_backend=False),
    RoomType.DEMO: RoomRecipe(requires_array_backend=True),
}

# Canonical, Control-owned Registration Node id per RoomType. Every Bit that
# declares support for a RoomType binds its ROOM-class role to this node (via
# room_role() below), so any compatible Bit can serve the same Room backend
# without re-declaring a fresh node id. Never surfaced in the Console or any
# app UI -- see design spec section 7.
ROOM_NODE_IDS: dict[RoomType, str] = {
    RoomType.TEST: "ROOM_TEST_NODE",
    RoomType.DEMO: "ROOM_DEMO_NODE",
}


def resolve_room_type(target: RoomType, *,
                      array_backend_configured: bool) -> RoomType:
    """Check target's recipe against what this installation has configured.
    Returns target on success. Raises RoomResolutionError -- never downgrades
    -- on failure."""
    recipe = ROOM_RECIPES[target]
    if recipe.requires_array_backend and not array_backend_configured:
        raise RoomResolutionError(
            f"{target.name} requires an array backend, none configured")
    return target


@dataclass
class Room:
    """Resolved once at boot. `bound` maps fixture name to the dev bound as
    that fixture's rendering backend -- see control/room_binding.py and
    control/boot.py. A fixture absent from this dict is simply not bound
    yet; a Room with SOME but not all fixtures bound renders to the ones it
    has (see design spec section 6)."""
    room_type: RoomType
    bound: dict[str, str] = field(default_factory=dict)

    def fully_bound(self, profile) -> bool:
        return all(fixture.name in self.bound for fixture in profile.fixtures)


def room_role_name(room_type: RoomType) -> str:
    """The deterministic role name every Bit supporting room_type must use
    for its ROOM-class role, so any compatible Bit's declaration is found
    the same way -- see control/rooms.py:room_role and
    devicelink/agent.py's Room-wiring, which looks this up off a loaded
    Bit's role_table."""
    return f"room_{room_type.name.lower()}"


def room_role(room_type: RoomType, *, ugen_manifest: dict | None = None,
             light_manifest: dict | None = None) -> tuple[str, Role, str]:
    """Build a ROOM-class Role for room_type plus its canonical node id, so a
    Bit can merge them into its own RoleTable.roles / node_map. The role name
    is deterministic per RoomType so two Bits supporting the same RoomType
    declare identical role names -- see design spec section 3. Capacity is
    the profile's own fixture count: one join per fixture, no more -- see
    design spec section 4.

    Imports room_profile locally (not at module top) because
    control/room_profile.py imports RoomType from this module -- a top-level
    import here would make the two modules circularly dependent on each
    other's not-yet-defined names.
    """
    from control.room_profile import room_profile

    name = room_role_name(room_type)
    role = Role(
        name=name,
        role_class=RoleClass.ROOM,
        capacity=len(room_profile(room_type).fixtures),
        scored=False,
        ugen_manifest=ugen_manifest or {},
        light_manifest=light_manifest or {},
    )
    return name, role, ROOM_NODE_IDS[room_type]


def non_room_counts(
        registration: "RegistrationState") -> list[tuple[str, int, int | None]]:
    """RegistrationState.counts() has no role_class in its tuples, so
    filtering ROOM-class roles out requires cross-referencing role_table.
    Shared by console/agent.py and uplink/link.py -- neither surface may
    reveal the Room's occupancy. See design spec section 7."""
    room_names = {r.name for r in registration.role_table.roles.values()
                 if r.role_class == RoleClass.ROOM}
    return [c for c in registration.counts() if c[0] not in room_names]
