"""Cue types a Bit's verb handler may return.

Light cues stay plain 4-tuples (dev, status, data1, data2) -- the MIDI shape
every existing Bit already returns, and changing it would be a breaking edit
to working code for no gain. PlayCue is a distinct type precisely so
GameServer.data() can tell the two apart by identity rather than by guessing
at tuple arity.
"""

from __future__ import annotations

from dataclasses import dataclass

# Sentinel dev id a Bit uses to target the Room. GameServer._resolve_dev turns
# it into the Room's bound dev; nothing downstream ever sees this string. A
# Bit therefore names the Room by a constant rather than by whatever id an
# admin-armed tap happened to bind, which is what keeps Bits offline-testable
# while still able to drive the Room. See docs/superpowers/specs/
# 2026-08-14-load-bearing-timed-cues-design.md section 4.1.
ROOM = "@room"


@dataclass(frozen=True)
class PlayCue:
    """Trigger a device-local sample. `name` indexes the role's declared
    `samples` list by value, never by position. `params` is opaque to Control
    and to the transport: the Bit writes it, the device displays it.

    Untimed by design: the device owns when a local sample fires, so there is
    nothing on this path for Control to schedule. Its `dev` still goes
    through ROOM resolution like any other cue's.
    """
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
