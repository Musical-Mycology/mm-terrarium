"""The function read model the Terrarium Console renders.

Pure dict builders with no engine imports, mirroring control/room_view.py so
this is testable with no GameServer, no renderer and no socket.

Script steps are serialized field by field rather than as raw cue tuples: the
browser then renders "cc:74 = 127" without re-deriving MIDI semantics, and
`kind` discriminates light from play the same way the Room panel's instrument
list discriminates light from audio.
"""

from __future__ import annotations

from control.cues import MuteCue, PlayCue, SolidCue
from control.functions import SOURCE_WIRE, FunctionKind


def _step_view(step) -> dict:
    cue = step.cue
    if isinstance(cue, PlayCue):
        return {"offset": float(step.offset), "kind": "play",
                "dev": cue.dev, "name": cue.name, "params": cue.params}
    if isinstance(cue, SolidCue):
        return {"offset": float(step.offset), "kind": "solid", "dev": cue.dev,
                "rgb": list(cue.rgb), "level": cue.level,
                "duration": cue.duration}
    if isinstance(cue, MuteCue):
        return {"offset": float(step.offset), "kind": "mute", "dev": cue.dev}
    dev, status, data1, data2 = cue
    return {"offset": float(step.offset), "kind": "light", "dev": dev,
            "status": status, "data1": data1, "data2": data2}


def function_view(function_decl) -> dict:
    """One declared function, as the Console draws its card."""
    return {
        "name": function_decl.name,
        "description": function_decl.description,
        "target": function_decl.target.name,
        "condition": {
            "name": function_decl.condition.name,
            "description": function_decl.condition.description,
            "source": SOURCE_WIRE[function_decl.condition.source],
            "verb": function_decl.condition.verb,
        },
        "script": [_step_view(step) for step in function_decl.script],
    }


def functions_view(function_table) -> list[dict]:
    """Every declared SCRIPTED function, in declaration order. Empty when no
    Bit is loaded, which the panel renders as "No functions declared".

    GENERATOR and STREAM functions are not yet rendered here -- the Console
    only understands the SCRIPTED card shape (target/condition/script) today.
    Kind-tagged cards for the other kinds are a later Console slice (see
    docs/superpowers/specs/2026-08-27-functions-and-trigger-rename-design.md
    section 10 item 6); skipping them here rather than crashing is what lets
    a Bit declare one without losing its Console panel meanwhile.
    """
    if function_table is None:
        return []
    return [function_view(fn) for fn in function_table.functions.values()
            if fn.kind is FunctionKind.SCRIPTED]


def function_fired_view(record) -> dict:
    """One fire.

    fired_by and declared_source are both carried, deliberately: the panel
    tags an admin-manual fire distinctly, and collapsing the two fields is
    what would let an operator action read as gameplay.
    """
    return {
        "name": record.name,
        "condition": record.condition,
        "fired_by": record.fired_by,
        "declared_source": record.declared_source,
        "dev": record.dev,
        "devs": list(record.devs),
        "at": record.at,
        "steps": record.steps,
        "room_name": record.room_name,
    }
