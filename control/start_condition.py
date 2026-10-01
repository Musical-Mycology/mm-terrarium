"""Pure start deciders: `timer_decision` (the harness timer) and
`decide_start` (an operator or admin start request).

Consumes the merged `StartCondition` (control/bit_config.py). The harness
drives timer decisions from it, and control/engine.py imports its
`scored_count` -- so keep it free of engine imports (that would be a cycle)
and side effects.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.bit_config import StartCondition
from control.lobby import (FEEDBACK_ACCEPT, FEEDBACK_MINIMUM, FEEDBACK_NONE,
                           FEEDBACK_REFUSED)


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


def timer_decision(cond: StartCondition, *, scored: int, elapsed: float,
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

    if cond.when == "admin":
        if cond.timeout_seconds is not None and elapsed >= cond.timeout_seconds:
            return cond.on_timeout
        return None

    return None


@dataclass(frozen=True)
class StartDecision:
    accepted: bool
    reason: str | None
    feedback: str


def decide_start(*, bit_loaded: bool, in_setup: bool, when: str | None,
                 expected_key: str | None, key: str | None, admin: bool,
                 scored: int, min_scored: int) -> StartDecision:
    """The start rule (spec section 2). `key is None` means an unkeyed
    operator surface (Console, uplink); a keyed source is judged against
    the Bit's key before anything else so a stranger with an old poster
    gets no room reaction at all."""
    if not bit_loaded:
        return StartDecision(False, "no Bit loaded", FEEDBACK_NONE)
    keyed = key is not None
    if keyed:
        if when != "admin":
            return StartDecision(False, "Bit does not take an admin start",
                                 FEEDBACK_NONE)
        if not expected_key or key != expected_key:
            return StartDecision(False, "bad key", FEEDBACK_NONE)
    if not in_setup:
        return StartDecision(False, "not in SETUP",
                             FEEDBACK_REFUSED if keyed else FEEDBACK_NONE)
    if admin:
        return StartDecision(True, None, FEEDBACK_ACCEPT)
    if min_scored > 0 and scored < min_scored:
        return StartDecision(False, "minimum not met", FEEDBACK_MINIMUM)
    return StartDecision(True, None, FEEDBACK_ACCEPT)
