"""Static role declarations a Bit provides. See design spec section 4."""

from dataclasses import dataclass, field
from enum import Enum, auto


class RoleClass(Enum):
    UNIQUE = auto()   # exclusive to one player (or capacity K)
    SHARED = auto()   # unbounded; every registrant gets the same effect
    JAM = auto()      # unbounded; full interaction but excluded from scoring
    ROOM = auto()     # capacity = the Room's own fixture count; binds one
                       # fixture's rendering backend per join, not a player
                       # -- see control/rooms.py:room_role and
                       # docs/superpowers/specs/2026-08-18-n-fixture-room-
                       # design.md section 4.


@dataclass
class Role:
    name: str
    role_class: RoleClass
    capacity: int | None  # None = unlimited (shared/jam); positive int for unique
    scored: bool
    # Device surfaces this role asks the device for, in either direction:
    # gestures it will read ("tilt", "tap", "shake"), and outputs it will
    # drive ("speaker", "mic"). One flat list, not inputs/outputs, because a
    # speaker is an output and the device's capability display is a single
    # convention across all of them. Composed into the /ie<N>/role blob;
    # the device renders exactly these as active.
    uses: list[str] = field(default_factory=list)
    # Local sample names this role may trigger via /ie<N>/play. Names, not
    # indices: harness/local_sample.py's SamplePlayer keys by name, and an
    # index would oblige every client to keep an ordered list in sync.
    samples: list[str] = field(default_factory=list)
    # This role's audio declaration, v0 and deliberately provisional (see
    # docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md section 9.2).
    # It is NOT the frozen wire contract light_manifest is, and it never ships
    # to the device: audio is Control's business (boundary rule 1). Shape:
    #   {"instruments": [{instrument, program?, drone?: {key, velocity},
    #                     lanes?: [{source: "cc:<n>", dest: "cc:<n>"}]}]}
    # Validated shallowly at Bit load (control/role_config.py). {} = no audio.
    ugen_manifest: dict = field(default_factory=dict)
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
