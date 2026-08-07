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
