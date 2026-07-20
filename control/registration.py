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
    reason: str | None = None
    hint: str | None = None


class RegistrationState:
    """Created when a Bit loads, discarded when it unloads."""

    def __init__(self, role_table: RoleTable):
        self.role_table = role_table
        self.assignments: dict[str, tuple[str, str, RoleClass]] = {}
        self._counts: dict[str, int] = {name: 0 for name in role_table.roles}

    def join(self, dev: str, node: str, state: State) -> JoinResult:
        candidates = self.role_table.node_map.get(node)
        if not candidates:
            return JoinResult(granted=False, reason="no such node")

        last_full_role = None
        for role_name in candidates:
            role = self.role_table.roles[role_name]
            if role.scored and state == State.RUNNING:
                continue  # scored roles closed once running; try the next fallback
            if role.capacity is not None and self._counts[role_name] >= role.capacity:
                last_full_role = role_name
                continue
            self._assign(dev, node, role)
            return JoinResult(granted=True, role=role.name,
                               role_class=role.role_class, scored=role.scored)

        if last_full_role is not None:
            return JoinResult(granted=False, reason=f"{last_full_role} at capacity")
        # Every candidate was scored and we're RUNNING -- capacity was never
        # the blocker.
        return JoinResult(granted=False,
                           reason="registration closed for scored roles")

    def _assign(self, dev: str, node: str, role: Role) -> None:
        self.release(dev)  # re-tapping a different node is a role switch
        self.assignments[dev] = (node, role.name, role.role_class)
        self._counts[role.name] += 1

    def release(self, dev: str) -> bool:
        prev = self.assignments.pop(dev, None)
        if prev is None:
            return False
        _, role_name, _ = prev
        self._counts[role_name] -= 1
        return True

    def release_all(self) -> list[str]:
        devs = list(self.assignments)
        for dev in devs:
            self.release(dev)
        return devs
