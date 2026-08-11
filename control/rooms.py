"""Room: the physical (or simulated) LED/mic/speaker hardware a Terrarium
installation offers. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
sections 3-4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from control.roles import Role, RoleClass


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
    """Resolved once at boot. bound_dev is set once a device (physical,
    simulated, or reconnected-from-a-prior-run) is attached as this Room's
    rendering backend -- see control/room_binding.py and control/boot.py."""
    room_type: RoomType
    bound_dev: str | None = None


def room_role(room_type: RoomType, *, ugen_manifest: dict | None = None,
             light_manifest: dict | None = None) -> tuple[str, Role, str]:
    """Build a ROOM-class Role for room_type plus its canonical node id, so a
    Bit can merge them into its own RoleTable.roles / node_map. The role name
    is deterministic per RoomType so two Bits supporting the same RoomType
    declare identical role names -- see design spec section 3."""
    name = f"room_{room_type.name.lower()}"
    role = Role(
        name=name,
        role_class=RoleClass.ROOM,
        capacity=1,
        scored=False,
        ugen_manifest=ugen_manifest or {},
        light_manifest=light_manifest or {},
    )
    return name, role, ROOM_NODE_IDS[room_type]
