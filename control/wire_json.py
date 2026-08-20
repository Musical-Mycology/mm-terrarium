"""json.dumps for anything leaving this process.

A non-finite float is not representable in JSON. Python's encoder emits the
bare tokens Infinity/-Infinity/NaN anyway, as a documented extension, and
Python's own decoder accepts them -- so a payload carrying one round-trips
cleanly within Python and is rejected outright by every strict parser,
including every browser's JSON.parse and Dart's jsonDecode.

That asymmetry cost a live run on 2026-08-19. TestBit.status()'s
run_duration is float("inf") under --hold, which harness/run_stack.py
always passes, so the Terrarium Console's snapshot failed JSON.parse in the
browser and every panel rendered empty while the stack was perfectly
healthy. Design:
docs/superpowers/specs/2026-08-19-wire-json-and-console-script-isolation-design.md

Every outbound JSON boundary in this repo calls dumps() rather than
json.dumps for that reason. Pure stdlib, so control/ stays free of
luxaeterna and pyarco.
"""

from __future__ import annotations

import json
import logging
import math
import re

logger = logging.getLogger(__name__)

# Paths already warned about, so a 44 Hz loop does not produce a 44 Hz log.
# Keyed on the SHAPE of the path (list indices collapsed to "[]") so the set
# is bounded by the payload's schema rather than by how much data flows
# through it.
_warned: set[str] = set()

_INDEX = re.compile(r"\[\d+\]")


def _note(value: float, path: str) -> None:
    shape = _INDEX.sub("[]", path)
    if shape in _warned:
        return
    _warned.add(shape)
    logger.warning(
        "non-finite float %r at %s is not representable in JSON; sending "
        "null instead", value, shape)


def _sanitise(value, path: str):
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        _note(value, path)
        return None
    if isinstance(value, dict):
        return {k: _sanitise(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise(v, f"{path}[{i}]") for i, v in enumerate(value)]
    return value


def dumps(obj, **kwargs) -> str:
    """Serialise obj as JSON a strict parser will accept.

    Non-finite floats become null. That is not an arbitrary placeholder:
    this wire format already uses null to mean unbounded (an uncapped
    Role.capacity is None, and the Console renders it as an infinity sign),
    so null carries the meaning Infinity was reaching for and needs no
    consumer-side change.

    allow_nan=False is deliberate belt-and-braces. If _sanitise ever misses
    a path, json.dumps raises rather than emitting a token no browser can
    read: a loud failure in a test beats a silent one at a venue.

    kwargs pass through to json.dumps, because capture/store.py already
    serialises with separators=(",", ":").
    """
    return json.dumps(_sanitise(obj, "$"), allow_nan=False, **kwargs)
