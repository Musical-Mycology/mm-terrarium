"""Static role declarations a Bit provides. See design spec section 4."""

from dataclasses import dataclass, field
from enum import Enum, auto


class RoleClass(Enum):
    UNIQUE = auto()   # exclusive to one player (or capacity K)
    SHARED = auto()   # unbounded; every registrant gets the same effect
    JAM = auto()      # unbounded; full interaction but excluded from scoring


@dataclass
class Role:
    name: str
    role_class: RoleClass
    capacity: int | None  # None = unlimited (shared/jam); positive int for unique
    scored: bool
    # Placeholder for this role's per-player graph declaration (future
    # per-role graph-builder work). Unused in this slice; present so the
    # schema doesn't change later.
    ugen_manifest: list = field(default_factory=list)
    # Placeholder for this role's per-player light-lane declaration, sibling
    # to ugen_manifest. Light is authored in the same timeline as sound
    # (see mm-documents shroom-installations-design.md); this exists so the
    # schema doesn't change when the first real Bit declares light lanes.
    # The Terrarium Console displays it; it never drives Lux Aeterna's render
    # loop. Unused in this slice.
    light_manifest: list = field(default_factory=list)


@dataclass
class RoleTable:
    roles: dict[str, Role]
    node_map: dict[str, list[str]]  # node id -> ordered role-name fallback list
