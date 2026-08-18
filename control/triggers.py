"""Bit-declared triggers: the named things an operator can see coming.

A Bit declares a TriggerTable parallel to its RoleTable. Each entry names a
thing that can happen, describes it in words an operator reads, says where it
lands, names the condition the BIT evaluates, and carries a declarative cue
script: an ordered list of (offset_seconds, cue).

Declarative rather than a callable for three reasons. The Console can render
the real steps rather than only a prose description; a test can assert the
exact cue sequence with no Arco; and manual fire becomes pushing data through
the dispatch path that already exists rather than calling into Bit code at an
arbitrary moment.

Pure and stdlib-only apart from control.cues, which is itself pure, so this
module imports in the offline suite with no renderer and no Arco. Validation
lives here rather than in a sibling config module because, unlike
control/role_config.py, there is no composed device-side blob to keep apart
from the declaration, and expand_script belongs next to the shape it expands.

See docs/superpowers/specs/
2026-08-17-bit-declared-triggers-and-cue-scripts-design.md sections 5 to 7.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum, auto

from control.cues import ROOM, TARGET, LightCue, PlayCue


class TriggerTarget(Enum):
    ROOM = auto()      # every Room fixture
    DEVICE = auto()    # the device that fired, when there was one
    ALL = auto()       # Room fixtures plus every registered non-ROOM device


class ConditionSource(Enum):
    GESTURE_VERB = auto()       # a device gesture the Bit adjudicates
    BIT_ADJUDICATED = auto()    # decided inside the Bit, no message behind it
    ADMIN_MANUAL = auto()       # exists only to be fired by an operator


# Wire spellings, so the Console and the fired record read the same words and
# neither has to lowercase an enum name and hope.
SOURCE_WIRE: dict[ConditionSource, str] = {
    ConditionSource.GESTURE_VERB: "gesture-verb",
    ConditionSource.BIT_ADJUDICATED: "bit-adjudicated",
    ConditionSource.ADMIN_MANUAL: "admin-manual",
}

# What actually fired a trigger THIS time, which is not the same thing as the
# source its condition declares: an operator may fire a gesture-verb trigger by
# hand, and the record has to keep those distinguishable. See TriggerFired.
FIRED_BY_GESTURE_VERB = "gesture-verb"
FIRED_BY_BIT_ADJUDICATED = "bit-adjudicated"
FIRED_BY_ADMIN_MANUAL = "admin-manual"

# A trigger name becomes a DOM id in console/static/triggers.js, the same way
# a capture label becomes a path component in capture/store.py. Restricted at
# the declaration boundary for the same reason: it is cheaper to refuse an odd
# name at Bit load than to escape it at every consumer.
_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_LEGAL_SCRIPT_DEVS = (TARGET, ROOM)


@dataclass(frozen=True)
class Condition:
    """What makes a trigger fire, in the Bit's own words.

    `verb` is required exactly when source is GESTURE_VERB, and is metadata
    plus something real for load-time validation to check. It does NOT cause
    Control to fire the trigger when that verb arrives: a tap that misses or a
    tilt below threshold must not fire, so the Bit's handler returns a
    FireTrigger explicitly or does not. Auto-firing on verb dispatch would put
    condition evaluation, however trivial, inside Control.
    """
    name: str
    description: str
    source: ConditionSource
    verb: str | None = None


@dataclass(frozen=True)
class ScriptStep:
    """One step of a cue script. `offset` is seconds from the trigger's `at`.

    `cue` is a plain (dev, status, data1, data2) tuple or a PlayCue, whose dev
    is cues.TARGET or cues.ROOM. A LightCue is refused: it names its own
    absolute time, and the offset is this step's timing.
    """
    offset: float
    cue: object


@dataclass(frozen=True)
class Trigger:
    name: str
    description: str
    target: TriggerTarget
    condition: Condition
    script: tuple[ScriptStep, ...] = ()


@dataclass
class TriggerTable:
    triggers: dict[str, Trigger] = field(default_factory=dict)


@dataclass(frozen=True)
class TriggerFired:
    """One fire, as the Console and any future uplink observer see it.

    fired_by and declared_source are separate on purpose. A manual fire of a
    gesture-verb trigger records fired_by="admin-manual" against
    declared_source="gesture-verb"; collapsing them is what would make an
    operator action indistinguishable from real gameplay in the log.

    devs and steps report what the fire RESOLVED to, not what it declared, so a
    fire that reached nothing is visibly that rather than silently absent.
    """
    name: str
    condition: str
    fired_by: str
    declared_source: str
    dev: str | None
    devs: tuple[str, ...]
    at: float
    steps: int


def validate_trigger_table(trigger_table, verb_names) -> None:
    """Shallow structural validation of a Bit's authored TriggerTable.

    Called from GameServer.load_bit alongside validate_role_declarations, and
    raises ValueError with a message locating the offending field, so a typo'd
    Bit fails as a load-time BitLoadError rather than mid-installation.

    `verb_names` is the key set of the Bit's verb_handlers(). Cross-referencing
    it here is what makes a declared-but-unimplemented gesture trigger a load
    failure. Deliberately shallow everywhere else, in the same places
    control/role_config.py is: a cue's controller number is not checked against
    any manifest lane, because an undeclared cc is already dropped by design in
    AudioBridge._apply_midi, and the light half's instrument registry belongs
    to luxaeterna, which Control cannot see.
    """
    if not isinstance(trigger_table, TriggerTable):
        raise ValueError(
            f"trigger_table: must be a TriggerTable, "
            f"got {type(trigger_table).__name__}")
    for key, trigger in trigger_table.triggers.items():
        where = f"trigger {key!r}"
        if not isinstance(trigger, Trigger):
            raise ValueError(
                f"{where}: must be a Trigger, got {type(trigger).__name__}")
        if trigger.name != key:
            raise ValueError(
                f"{where}: name {trigger.name!r} does not match its key")
        if not isinstance(trigger.name, str) or not _NAME_RE.match(trigger.name):
            raise ValueError(
                f"{where}: name {trigger.name!r} must match [A-Za-z0-9_-]+ "
                f"(it becomes a DOM id on the Console)")
        if not isinstance(trigger.description, str) or not trigger.description:
            raise ValueError(f"{where}: description must be non-empty")
        if not isinstance(trigger.target, TriggerTarget):
            raise ValueError(
                f"{where}: target must be a TriggerTarget, "
                f"got {trigger.target!r}")
        _validate_condition(trigger, verb_names)
        _validate_script(trigger)


def _validate_condition(trigger: Trigger, verb_names) -> None:
    where = f"trigger {trigger.name!r} condition"
    condition = trigger.condition
    if not isinstance(condition, Condition):
        raise ValueError(
            f"{where}: must be a Condition, got {type(condition).__name__}")
    if not isinstance(condition.name, str) or not condition.name:
        raise ValueError(f"{where}: name must be non-empty")
    if not isinstance(condition.description, str) or not condition.description:
        raise ValueError(f"{where}: description must be non-empty")
    if not isinstance(condition.source, ConditionSource):
        raise ValueError(
            f"{where}: source must be a ConditionSource, "
            f"got {condition.source!r}")
    if condition.source is ConditionSource.GESTURE_VERB:
        if not condition.verb:
            raise ValueError(
                f"{where}: a gesture-verb condition must name a verb")
        if condition.verb not in verb_names:
            raise ValueError(
                f"{where}: verb {condition.verb!r} is not implemented by "
                f"verb_handlers() (implemented: {sorted(verb_names)})")
    elif condition.verb is not None:
        raise ValueError(
            f"{where}: verb {condition.verb!r} is only meaningful on a "
            f"gesture-verb condition, not on "
            f"{SOURCE_WIRE[condition.source]}")


def _validate_script(trigger: Trigger) -> None:
    script = trigger.script
    if not isinstance(script, tuple):
        raise ValueError(
            f"trigger {trigger.name!r} script: must be a tuple, "
            f"got {type(script).__name__}")
    previous: float | None = None
    for idx, step in enumerate(script):
        where = f"trigger {trigger.name!r} script[{idx}]"
        if not isinstance(step, ScriptStep):
            raise ValueError(
                f"{where}: must be a ScriptStep, got {type(step).__name__}")
        offset = _validate_offset(step, where, previous)
        previous = offset
        _validate_step_cue(step, where)


def _validate_offset(step: ScriptStep, where: str,
                     previous: float | None) -> float:
    offset = step.offset
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        raise ValueError(f"{where}: offset must be a number, got {offset!r}")
    offset = float(offset)
    if not math.isfinite(offset):
        raise ValueError(f"{where}: offset must be finite, got {step.offset!r}")
    if offset < 0:
        raise ValueError(f"{where}: offset must be >= 0, got {offset}")
    if previous is not None and offset < previous:
        raise ValueError(
            f"{where}: offset {offset} is earlier than the previous step's "
            f"{previous}; steps must be in non-decreasing order, because the "
            f"Console renders them as a sequence")
    return offset


def _validate_step_cue(step: ScriptStep, where: str) -> None:
    cue = step.cue
    if isinstance(cue, LightCue):
        raise ValueError(
            f"{where}: a LightCue names its own absolute time, and a script "
            f"step's timing is its offset. Declare a plain "
            f"(dev, status, data1, data2) tuple instead")
    if isinstance(cue, PlayCue):
        if float(step.offset) != 0.0:
            raise ValueError(
                f"{where}: a PlayCue must sit at offset 0. The device owns "
                f"when a local sample fires and the play path has no queue, so "
                f"a non-zero offset would be silently ignored")
        _validate_script_dev(cue.dev, where)
        return
    if not isinstance(cue, tuple) or len(cue) != 4:
        raise ValueError(
            f"{where}: cue must be a PlayCue or a 4-tuple "
            f"(dev, status, data1, data2), got {cue!r}")
    dev, status, data1, data2 = cue
    _validate_script_dev(dev, where)
    for label, value in (("status", status), ("data1", data1),
                         ("data2", data2)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where}: {label} must be an int, got {value!r}")
        if not 0 <= value <= 255:
            raise ValueError(f"{where}: {label} {value} is outside 0-255")


def _validate_script_dev(dev, where: str) -> None:
    if dev not in _LEGAL_SCRIPT_DEVS:
        raise ValueError(
            f"{where}: dev must be cues.TARGET ({TARGET!r}) or cues.ROOM "
            f"({ROOM!r}), got {dev!r}. Device ids are assigned at runtime, so "
            f"a literal in a static declaration can never resolve")
