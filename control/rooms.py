"""Room: the physical (or simulated) LED/mic/speaker hardware a Terrarium
installation offers. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
sections 3-4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from control.roles import Role, RoleClass

if TYPE_CHECKING:
    from control.registration import RegistrationState
    from control.room_profile import RoomProfile


class RoomResolutionError(Exception):
    """Raised when a target Room's recipe isn't satisfiable. Resolution
    never downgrades to a lesser type -- see design spec section 3."""


@dataclass
class Room:
    """Resolved once at boot. `name` is the config-name string this Room
    was loaded as (a key in TerrariumConfig.rooms, e.g. "TEST"/"DEMO").
    `bound` maps fixture name to the dev bound as that fixture's rendering
    backend -- see control/room_binding.py and control/boot.py. A fixture
    absent from this dict is simply not bound yet; a Room with SOME but not
    all fixtures bound renders to the ones it has (see design spec
    section 6)."""
    name: str
    profile: "RoomProfile"
    node_id: str
    bound: dict[str, str] = field(default_factory=dict)

    def fully_bound(self, profile=None) -> bool:
        p = profile if profile is not None else self.profile
        return all(fixture.name in self.bound for fixture in p.fixtures)


def room_role_name(room_name: str) -> str:
    """The deterministic role name every Bit supporting room_name must use
    for its ROOM-class role, so any compatible Bit's declaration is found
    the same way -- see control/rooms.py:room_role and
    devicelink/agent.py's Room-wiring, which looks this up off a loaded
    Bit's role_table."""
    return f"room_{room_name.lower()}"


def room_role(room: "Room", *, ugen_manifest: dict | None = None,
             light_manifest: dict | None = None) -> tuple[str, Role, str]:
    """Build a ROOM-class Role for room plus its node id, so a Bit -- or,
    now, the engine itself (control/engine.py's load_bit) -- can merge them
    into a RoleTable.roles / node_map. The role name is deterministic per
    room name so two Bits supporting the same room declare identical role
    names -- see design spec section 3. Capacity is the profile's own
    fixture count: one join per fixture, no more -- see design spec
    section 4."""
    name = room_role_name(room.name)
    role = Role(
        name=name,
        role_class=RoleClass.ROOM,
        capacity=len(room.profile.fixtures),
        scored=False,
        ugen_manifest=ugen_manifest or {},
        light_manifest=light_manifest or {},
    )
    return name, role, room.node_id


def non_room_counts(
        registration: "RegistrationState") -> list[tuple[str, int, int | None]]:
    """RegistrationState.counts() has no role_class in its tuples, so
    filtering ROOM-class roles out requires cross-referencing role_table.
    Shared by console/agent.py and uplink/link.py -- neither surface may
    reveal the Room's occupancy. See design spec section 7."""
    room_names = {r.name for r in registration.role_table.roles.values()
                 if r.role_class == RoleClass.ROOM}
    return [c for c in registration.counts() if c[0] not in room_names]
