"""Capability-derived built-in Functions: stop, flash, ping.

Synthesized from an Instrument's capabilities, never authored -- the
operator's troubleshooting vocabulary is identical at every venue (spec
section 2). Names in RESERVED_NAMES are refused in every authored
FunctionTable, Bit or instrument, so a built-in can never be shadowed.
Pure stdlib (control/ discipline).
"""
from __future__ import annotations

from control.cues import TARGET, MuteCue, PlayCue, SolidCue
from control.functions import Function, FunctionKind, ScriptStep

RESERVED_NAMES: frozenset[str] = frozenset({"flash", "stop", "ping"})

PING_KEY = 57
PING_VEL = 100
PING_OFF_OFFSET = 0.5


def builtin_functions(instrument) -> dict[str, Function]:
    """The built-ins this instrument's capabilities support, keyed by name.

    stop needs any light.* or audio.*; flash needs any light.*; ping needs
    an audio capability (samples preferred -- sub-20 ms local path -- else
    a short note through the flsyn voice)."""
    caps = instrument.capabilities
    has_light = any(c.startswith("light.") for c in caps)
    has_samples = "audio.samples" in caps
    has_flsyn = "audio.flsyn" in caps
    out: dict[str, Function] = {}
    if has_light:
        steps = []
        if has_samples:
            steps.append(ScriptStep(0.0, PlayCue(TARGET, "chime", "")))
        steps.append(ScriptStep(0.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)))
        out["flash"] = Function(
            name="flash", kind=FunctionKind.SCRIPTED, script=tuple(steps),
            description="Light test: solid white for 5 s, then resume")
    if has_light or has_samples or has_flsyn:
        out["stop"] = Function(
            name="stop", kind=FunctionKind.SCRIPTED,
            script=(ScriptStep(0.0, MuteCue(TARGET)),),
            description="Latch this surface dark and silent until a play "
                        "un-mutes it")
    if has_samples:
        out["ping"] = Function(
            name="ping", kind=FunctionKind.SCRIPTED,
            script=(ScriptStep(0.0, PlayCue(TARGET, "chime", "")),),
            description="Audio test: play a chime on this surface")
    elif has_flsyn:
        out["ping"] = Function(
            name="ping", kind=FunctionKind.SCRIPTED,
            script=(ScriptStep(0.0, (TARGET, 0x90, PING_KEY, PING_VEL)),
                    ScriptStep(PING_OFF_OFFSET, (TARGET, 0x80, PING_KEY, 0))),
            description="Audio test: a short note through this surface's voice")
    return out
