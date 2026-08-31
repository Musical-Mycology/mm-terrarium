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


def _scripted_view(function_decl) -> dict:
    return {
        "kind": "scripted",
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


def _generator_view(function_decl) -> dict:
    spec = function_decl.generator
    return {
        "kind": "generator",
        "name": function_decl.name,
        "description": function_decl.description,
        "lane": {"dev": spec.dev, "status": spec.status, "data1": spec.data1},
        "waveform": spec.waveform,
        "period": float(spec.period),
        "lo": spec.lo,
        "hi": spec.hi,
    }


def _stream_output_view(output) -> dict:
    return {
        "dev": output.dev,
        "status": output.status,
        "data1": output.data1,
        "out_lo": output.out_lo,
        "out_hi": output.out_hi,
        "mode": output.mode,
    }


def _stream_view(function_decl) -> dict:
    spec = function_decl.stream
    return {
        "kind": "stream",
        "name": function_decl.name,
        "description": function_decl.description,
        "verb": spec.verb,
        "arg": spec.arg,
        "in_lo": spec.in_lo,
        "in_hi": spec.in_hi,
        "outputs": [_stream_output_view(output) for output in spec.outputs],
    }


def function_view(function_decl) -> dict:
    """One declared function, as the Console draws its card. The shape is
    kind-tagged: SCRIPTED keeps the original target/condition/script fields,
    GENERATOR carries its lane/waveform/period/lo/hi, STREAM carries its
    verb/arg/domain/outputs. See
    docs/superpowers/specs/2026-08-27-functions-and-trigger-rename-design.md
    sections 6 and 8.
    """
    if function_decl.kind is FunctionKind.GENERATOR:
        return _generator_view(function_decl)
    if function_decl.kind is FunctionKind.STREAM:
        return _stream_view(function_decl)
    return _scripted_view(function_decl)


def functions_view(function_table) -> list[dict]:
    """Every declared function, in declaration order, kind-tagged. Empty
    when no Bit is loaded, which the panel renders as "No functions
    declared"."""
    if function_table is None:
        return []
    return [function_view(fn) for fn in function_table.functions.values()]


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
