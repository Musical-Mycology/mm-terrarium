"""Per-Bit runtime registration state: who holds what role. See design
spec section 4 and the join-resolution rules in section 3 (SETUP vs RUNNING).
"""

from dataclasses import dataclass

from control.roles import Role, RoleClass, RoleTable
from control.state import State


@dataclass
class JoinResult:
    granted: bool
    role: str | None = None
    role_class: RoleClass | None = None
    scored: bool | None = None
    # Role.breath, carried out to DeviceLinkAgent so it knows whether to
    # drive this device's cc:11 breath. None on a denial, like `scored`.
    breath: bool | None = None
    reason: str | None = None
    hint: str | None = None
    # The instrument-requirement slot this join filled, and the carried
    # instrument's name that filled it -- set by GameServer.join on a
    # granted, requires-bearing role. None for ROOM joins and roles with no
    # Role.requires (wire-friendly: name, not the Instrument object).
    slot: str | None = None
    instrument: str | None = None
    # Composed per-role config blob for /<dev>/role -- filled by
    # GameServer.join on granted results (control/role_config.py);
    # RegistrationState itself never touches it.
    config: dict | None = None


_SCORED_FULL_HINT = ("the Bit's scored slots are taken; you will get a jam "
                     "role at start")


class RegistrationState:
    """Created when a Bit loads, discarded when it unloads. In SETUP a
    handshake only RESERVES a scored slot (`validated`); run() turns the
    reservations, plus a jam role for everyone else, into `assignments`
    (spec 2026-10-01-instrument-handshake-protocol section 3.6)."""

    def __init__(self, role_table: RoleTable, max_scored: int | None = None):
        self.role_table = role_table
        self.max_scored = max_scored
        self.assignments: dict[str, tuple[str, str, RoleClass]] = {}
        self.validated: dict[str, tuple[str, str]] = {}
        self._counts: dict[str, int] = {name: 0 for name in role_table.roles}

    def scored_cap(self) -> int | None:
        total = 0
        for role in self.role_table.roles.values():
            if not role.scored or role.role_class is RoleClass.ROOM:
                continue
            if role.capacity is None:
                return self.max_scored
            total += role.capacity
        if self.max_scored is not None:
            return min(total, self.max_scored)
        return total

    def _deny(self, reason: str, hint: str) -> JoinResult:
        return JoinResult(granted=False, reason=reason, hint=hint)

    def _granted(self, role: Role) -> JoinResult:
        return JoinResult(granted=True, role=role.name,
                          role_class=role.role_class, scored=role.scored,
                          breath=role.breath)

    def validate(self, dev: str, node: str) -> JoinResult:
        held = self.validated.get(dev)
        if held is not None:
            return self._granted(self.role_table.roles[held[1]])
        candidates = self.role_table.node_map.get(node)
        if not candidates:
            return self._deny("no such node",
                              f"this Bit declares no node {node!r}")
        cap = self.scored_cap()
        if cap is not None and len(self.validated) >= cap:
            return self._deny("scored full", _SCORED_FULL_HINT)
        saw_scored = False
        for role_name in candidates:
            role = self.role_table.roles[role_name]
            if not role.scored or role.role_class is RoleClass.ROOM:
                continue
            saw_scored = True
            if role.capacity is not None and \
                    self._counts[role_name] >= role.capacity:
                continue
            self.validated[dev] = (node, role_name)
            self._counts[role_name] += 1
            return self._granted(role)
        if saw_scored:
            return self._deny("scored full", _SCORED_FULL_HINT)
        return self._deny("no such node",
                          f"node {node!r} grants no scored role")

    def assign(self, dev: str, node: str, role: Role) -> bool:
        current = self.assignments.get(dev)
        if current is not None and current[1] == role.name:
            return False
        self.release(dev)
        if role.name not in self.role_table.roles:
            self.role_table.roles[role.name] = role
        self._counts.setdefault(role.name, 0)
        self.assignments[dev] = (node, role.name, role.role_class)
        self._counts[role.name] += 1
        return True

    def materialize(self, jam_devs, jam_for) -> list:
        out = []
        reserved, self.validated = self.validated, {}
        for dev, (node, role_name) in reserved.items():
            role = self.role_table.roles[role_name]
            # The reservation already counted this slot; move it, do not
            # count it twice.
            self.assignments[dev] = (node, role_name, role.role_class)
            out.append((dev, role))
        for dev in jam_devs:
            if dev in self.assignments:
                continue
            role = jam_for(dev)
            if self.assign(dev, "", role):
                out.append((dev, role))
        return out

    def release(self, dev: str) -> bool:
        held = self.validated.pop(dev, None)
        if held is not None:
            self._counts[held[1]] -= 1
            return True
        prev = self.assignments.pop(dev, None)
        if prev is None:
            return False
        _, role_name, _ = prev
        self._counts[role_name] -= 1
        return True

    def release_all(self) -> list[str]:
        for dev in list(self.validated):
            self.release(dev)
        devs = list(self.assignments)
        for dev in devs:
            self.release(dev)
        return devs

    def counts(self) -> list[tuple[str, int, int | None]]:
        """Live per-role (name, count, capacity) snapshot -- the public view
        of _counts for callers outside Control (e.g. the uplink) that need
        registration fill-state without reaching into a private attribute.
        """
        return [
            (role.name, self._counts[role.name], role.capacity)
            for role in self.role_table.roles.values()
        ]

    def granted(self) -> list[tuple[str, str, RoleClass]]:
        """Every current assignment as (dev, role_name, role_class) in join
        order, without ROOM-class bindings (a fixture, never a player). The
        uplink builds bit_completed.players from this at COMPLETING."""
        return [(dev, role_name, role_class)
                for dev, (_node, role_name, role_class) in self.assignments.items()
                if role_class is not RoleClass.ROOM]
