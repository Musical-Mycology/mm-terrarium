"""Cue types a Bit's verb handler may return.

Light cues stay plain 4-tuples (dev, status, data1, data2) -- the MIDI shape
every existing Bit already returns, and changing it would be a breaking edit
to working code for no gain. PlayCue is a distinct type precisely so
GameServer.data() can tell the two apart by identity rather than by guessing
at tuple arity.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlayCue:
    """Trigger a device-local sample. `name` indexes the role's declared
    `samples` list by value, never by position. `params` is opaque to Control
    and to the transport: the Bit writes it, the device displays it."""
    dev: str
    name: str
    params: str = ""


@dataclass(frozen=True)
class LightCue:
    """A light cue carrying an explicit target time on the O2 clock.

    Plain 4-tuples (dev, status, data1, data2) remain valid and mean
    when=None, "apply on arrival" -- every Bit written before this type
    existed keeps working unchanged. A Bit opts into timing by returning
    LightCue instead. Distinct type rather than a 5-tuple for the same
    reason PlayCue is a distinct type: GameServer.data() tells cue kinds
    apart by identity, never by guessing at tuple arity.
    """
    dev: str
    status: int
    data1: int
    data2: int
    when: float | None = None
