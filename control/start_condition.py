"""Pure start-decision evaluator for the harness.

Consumes the merged `StartCondition` (control/bit_config.py). This module is
consumed by the HARNESS, not the engine -- keep it free of engine imports and
side effects.
"""

from __future__ import annotations

from control.bit_config import StartCondition


def scored_count(gs) -> int:
    """Sum `count` over gs.registration.counts() entries whose role is scored.

    Scored-ness of a role is resolved off gs.bit.role_table (same idiom as
    control/registration.py's RegistrationState.counts()). Returns 0 when
    gs.registration is None. A counts() entry whose role name is absent from
    the current role_table counts as unscored: a room unloaded mid-SETUP
    leaves the ROOM-class role's registration count behind with no matching
    role_table entry, and start evaluation must not crash the harness there.
    """
    if gs.registration is None:
        return 0
    role_table = gs.bit.role_table
    total = 0
    for name, count, _capacity in gs.registration.counts():
        role = role_table.roles.get(name)
        if role is not None and role.scored:
            total += count
    return total


def start_decision(cond: StartCondition, *, scored: int, elapsed: float,
                    setup_seconds: float) -> str | None:
    """Pure start/abort/hold decision for the given StartCondition."""
    if cond.when == "immediate":
        return "start" if elapsed >= setup_seconds else None

    if cond.when == "operator":
        return None

    if cond.when == "players":
        if scored >= cond.min_scored:
            return "start"
        if cond.timeout_seconds is not None and elapsed >= cond.timeout_seconds:
            return cond.on_timeout
        return None

    return None
