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
    # This role's light declaration in the light-manifest v2 wire shape
    # (luxaeterna docs/superpowers/specs/2026-07-22-synth-session-lifecycle-
    # design.md section 9; adopted here per docs/superpowers/specs/
    # 2026-07-22-light-manifest-v2-adoption-design.md). Authored subset only:
    #   {"instruments": [{instrument, target, params?,
    #                     lanes?: [{source, dest, curve?}]}]}
    # welcome/bit_name/bit_version/role are composed into the outgoing blob
    # by Control at adoption time and are forbidden here (validated at Bit
    # load, control/role_config.py). {} parses device-side as "no light".
    # The Terrarium Console displays it; the composed blob, not this field,
    # reaches Lux Aeterna.
    light_manifest: dict = field(default_factory=dict)
    # The role's welcome ceremony, both halves declared in one place:
    #   {"light": {instrument, params?, duration?},
    #    "audio": {instrument, params?, duration?}}
    # light folds into the outgoing light_manifest blob (plays in LOADING
    # instead of sys:loaded); audio stays Control-side for the future Arco
    # cue path (no consumer yet; shape frozen so Bit authors declare both
    # together from day one).
    welcome: dict | None = None


@dataclass
class RoleTable:
    roles: dict[str, Role]
    node_map: dict[str, list[str]]  # node id -> ordered role-name fallback list
