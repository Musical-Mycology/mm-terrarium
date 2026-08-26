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


# Sentinel dev id for a cue script step addressed at whatever the firing
# trigger declared as its target. Substituted during expansion
# (control.triggers.expand_script), before the cue ever reaches
# GameServer._resolve_dev, so that method is not edited by the trigger slice
# and ROOM resolution keeps working exactly as it does today. See
# docs/superpowers/specs/
# 2026-08-17-bit-declared-triggers-and-cue-scripts-design.md section 7.2.
TARGET = "@target"


@dataclass(frozen=True)
class FireTrigger:
    """A Bit's report that one of its own declared conditions is satisfied.

    Returned in the same list a Bit already returns cues in, from a verb
    handler or from cues(at), so a fire inherits that path's single
    presentation time and lands on the same frame as the ordinary cues
    returned beside it. `dev` names the device the fire is about, when there
    is one; it is what TriggerTarget.DEVICE resolves to.

    A distinct type rather than a magic tuple, for the same reason PlayCue and
    LightCue are: GameServer._dispatch_cues tells cue kinds apart by identity,
    never by guessing at tuple arity.
    """
    name: str
    dev: str | None = None


@dataclass(frozen=True)
class SolidCue:
    """A solid-color override applied ON TOP of a device's rendered session
    frame, bypassing instruments entirely -- so it works on every surface,
    including roles with empty light manifests. Applied Control-side at the
    frame-building seam (DeviceLinkAgent); nothing on the device wire changes.

    `duration` is seconds from `when`; None means latched until explicitly
    cleared (the mute blackout). `when=None` follows LightCue's convention:
    apply at the dispatch-supplied presentation time.
    """
    dev: str
    rgb: tuple[int, int, int]
    level: float
    duration: float | None
    when: float | None = None


@dataclass(frozen=True)
class MuteCue:
    """Latch a surface dark and silent (the Stop trigger). Distinct type for
    the same identity-dispatch reason as every cue here. Un-latching is not a
    cue: any non-mute trigger fired at the surface clears it (engine rule)."""
    dev: str
