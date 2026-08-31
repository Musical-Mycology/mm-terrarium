"""Sensing-side Triggers -- Spec 3 section 6; the acting side lives in
control/functions.py.

`EventTrigger` names a device-side detector: the DEVICE runs the detection
(e.g. an accelerometer spike heuristic), the SERVER owns the numeric
thresholds and ships them in the composed role blob, so the client
detectors converge on one server-declared truth instead of each guessing
its own.

`StreamTrigger` names a server-side transform applied to a raw gesture
stream in GameServer.data(), before stream Functions and verb handlers see
the args -- the seam where fusion/smoothing pipelines will live. v0 ships
one transform, "smooth" (an EMA), and one verb per trigger; fusion across
verbs is explicitly deferred until a real consumer demands it.

Pure stdlib, no luxaeterna, no pyarco (control/ discipline).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")

TRANSFORMS: frozenset[str] = frozenset({"smooth"})


class TriggerError(ValueError):
    pass


@dataclass(frozen=True)
class EventTrigger:
    """Device-side detection of a discrete gesture."""
    name: str                    # the verb it produces ("tap", "shake")
    description: str
    thresholds: dict = field(default_factory=dict)  # flat str -> number, shipped verbatim


@dataclass(frozen=True)
class StreamTrigger:
    """Server-side transform of a raw gesture stream."""
    name: str
    description: str
    verb: str
    arg: int
    transform: str                # vocabulary: TRANSFORMS, "smooth" (EMA) v0
    params: dict = field(default_factory=dict)  # smooth: {"alpha": 0<a<=1}


def validate_event_trigger(trigger: EventTrigger, where: str) -> None:
    """Shallow structural validation, raising TriggerError with a message
    locating the offending field by `where` (e.g. an instrument name)."""
    if not isinstance(trigger, EventTrigger):
        raise TriggerError(
            f"{where}: must be an EventTrigger, got {type(trigger).__name__}")
    if not isinstance(trigger.name, str) or not _NAME_RE.match(trigger.name):
        raise TriggerError(
            f"{where}: event trigger name {trigger.name!r} must match "
            f"[A-Za-z0-9_-]+")
    if not isinstance(trigger.description, str) or not trigger.description:
        raise TriggerError(
            f"{where}: event trigger {trigger.name!r} description must be "
            f"non-empty")
    if not isinstance(trigger.thresholds, dict):
        raise TriggerError(
            f"{where}: event trigger {trigger.name!r} thresholds must be a "
            f"dict, got {type(trigger.thresholds).__name__}")
    for key, value in trigger.thresholds.items():
        if not isinstance(key, str):
            raise TriggerError(
                f"{where}: event trigger {trigger.name!r} threshold key "
                f"{key!r} must be a str")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TriggerError(
                f"{where}: event trigger {trigger.name!r} threshold "
                f"{key!r}={value!r} must be numeric")


def validate_stream_trigger(trigger: StreamTrigger, where: str) -> None:
    """Shallow structural validation, raising TriggerError with a message
    locating the offending field by `where` (e.g. an instrument name)."""
    if not isinstance(trigger, StreamTrigger):
        raise TriggerError(
            f"{where}: must be a StreamTrigger, got {type(trigger).__name__}")
    if not isinstance(trigger.name, str) or not _NAME_RE.match(trigger.name):
        raise TriggerError(
            f"{where}: stream trigger name {trigger.name!r} must match "
            f"[A-Za-z0-9_-]+")
    if not isinstance(trigger.description, str) or not trigger.description:
        raise TriggerError(
            f"{where}: stream trigger {trigger.name!r} description must be "
            f"non-empty")
    if not isinstance(trigger.verb, str) or not trigger.verb:
        raise TriggerError(
            f"{where}: stream trigger {trigger.name!r} verb must be a "
            f"non-empty str")
    if not isinstance(trigger.arg, int) or isinstance(trigger.arg, bool):
        raise TriggerError(
            f"{where}: stream trigger {trigger.name!r} arg must be an int")
    if trigger.transform not in TRANSFORMS:
        raise TriggerError(
            f"{where}: stream trigger {trigger.name!r} transform "
            f"{trigger.transform!r} is not in {sorted(TRANSFORMS)}")
    if not isinstance(trigger.params, dict):
        raise TriggerError(
            f"{where}: stream trigger {trigger.name!r} params must be a "
            f"dict, got {type(trigger.params).__name__}")
    if trigger.transform == "smooth":
        alpha = trigger.params.get("alpha")
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) \
                or not (0 < alpha <= 1):
            raise TriggerError(
                f"{where}: stream trigger {trigger.name!r} smooth transform "
                f"requires params['alpha'] in (0, 1], got {alpha!r}")
