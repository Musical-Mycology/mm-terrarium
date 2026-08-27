"""The trigger read model the Terrarium Console renders.

Pure dict builders with no engine imports, mirroring control/room_view.py so
this is testable with no GameServer, no renderer and no socket.

Script steps are serialized field by field rather than as raw cue tuples: the
browser then renders "cc:74 = 127" without re-deriving MIDI semantics, and
`kind` discriminates light from play the same way the Room panel's instrument
list discriminates light from audio.
"""

from __future__ import annotations

from control.cues import PlayCue
from control.triggers import SOURCE_WIRE


def _step_view(step) -> dict:
    cue = step.cue
    if isinstance(cue, PlayCue):
        return {"offset": float(step.offset), "kind": "play",
                "dev": cue.dev, "name": cue.name, "params": cue.params}
    dev, status, data1, data2 = cue
    return {"offset": float(step.offset), "kind": "light", "dev": dev,
            "status": status, "data1": data1, "data2": data2}


def trigger_view(trigger) -> dict:
    """One declared trigger, as the Console draws its card."""
    return {
        "name": trigger.name,
        "description": trigger.description,
        "target": trigger.target.name,
        "condition": {
            "name": trigger.condition.name,
            "description": trigger.condition.description,
            "source": SOURCE_WIRE[trigger.condition.source],
            "verb": trigger.condition.verb,
        },
        "script": [_step_view(step) for step in trigger.script],
    }


def triggers_view(trigger_table) -> list[dict]:
    """Every declared trigger, in declaration order. Empty when no Bit is
    loaded, which the panel renders as "No triggers declared"."""
    if trigger_table is None:
        return []
    return [trigger_view(t) for t in trigger_table.triggers.values()]


def trigger_fired_view(record) -> dict:
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
