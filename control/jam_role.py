"""The jam role a device gets at RUNNING when it did not validate (spec
2026-10-01-instrument-handshake-protocol section 3.7): the Bit's own
JAM-class role, else a role synthesized per carried instrument from its
[solo] table, so an undeclared jam follows the instrument's Solo
behaviour. Pure stdlib."""
from __future__ import annotations

from copy import deepcopy

from control.roles import Role, RoleClass

SOLO_PREFIX = "solo:"
_EVENT_VERB = {"tap": "tap", "double_tap": "tap", "shake": "shake"}


def bit_jam_role(role_table) -> Role | None:
    for role in role_table.roles.values():
        if role.role_class is RoleClass.JAM:
            return role
    return None


def solo_role(instrument) -> Role:
    solo = instrument.solo
    if solo is not None:
        light = deepcopy(solo.light_manifest)
        uses = sorted({_EVENT_VERB[e] for e in solo.bindings
                       if e in _EVENT_VERB})
    else:
        light = deepcopy(instrument.light_manifest)
        uses = []
    return Role(name=SOLO_PREFIX + instrument.name,
                role_class=RoleClass.JAM, capacity=None, scored=False,
                uses=uses, light_manifest=light)


def is_solo_role(name: str) -> bool:
    return name.startswith(SOLO_PREFIX)


def solo_event(verb: str, args: list) -> str:
    if verb == "tap" and len(args) > 3:
        try:
            if int(args[3]) >= 2:
                return "double_tap"
        except (TypeError, ValueError):
            pass
    return verb
