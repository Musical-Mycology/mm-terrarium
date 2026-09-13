"""The LAN prepare rule: what the Terrarium does when MycoQuest asks it to
load a Bit into the lobby (spec docs/superpowers/specs/
2026-09-13-mycoquest-handoff-terrarium-design.md, section 4).

Pure stdlib. `decide_prepare` is the rule; `PrepareAuthority` (below, Task
2) applies it against the registry and the engine. Nothing here touches a
socket: harness/www_server.py enqueues a PrepareRequest on the server
thread and devicelink/agent.py drains it on the tick thread, the same shape
as the web start queue.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from control.state import State

ACTION_LOAD = "load"
ACTION_NOOP = "noop"
ACTION_NONE = "none"

REASON_NO_ROOM = "no room loaded"
REASON_BAD_KEY = "bad key"
REASON_BUSY = "busy"


@dataclass
class PrepareReply:
    """The slot the www handler waits on. Filled by the drain."""
    done: threading.Event
    accepted: bool = False
    reason: str | None = None
    visible: bool = False


@dataclass(frozen=True)
class PrepareRequest:
    """One queued web prepare (harness/www_server.py -> DeviceLinkAgent).
    `key` is excluded from repr so a logged request never leaks it."""
    key: str | None = field(repr=False)
    bit: str
    dev: str | None
    source: str
    reply: PrepareReply


@dataclass(frozen=True)
class PrepareDecision:
    accepted: bool
    reason: str | None
    action: str            # ACTION_LOAD | ACTION_NOOP | ACTION_NONE
    visible: bool          # may the refusal reason reach the caller


@dataclass(frozen=True)
class PrepareRequested:
    """The engine observer record for every prepare attempt."""
    source: str
    source_dev: str | None
    bit: str
    accepted: bool
    reason: str | None


def decide_prepare(*, room_ready: bool, state: State, loaded_bit: str | None,
                   bit: str, known: bool, when: str | None,
                   expected_key: str | None, key: str | None) -> PrepareDecision:
    """Spec section 4.2, evaluated in order. A bad key (or an unknown Bit,
    or a Bit that does not take an admin start) is refused silently so a
    stranger learns nothing; busy and no-room are visible because the app
    needs them."""
    if not room_ready:
        return PrepareDecision(False, REASON_NO_ROOM, ACTION_NONE, True)
    if (not known or when != "admin" or not expected_key
            or key is None or key != expected_key):
        return PrepareDecision(False, REASON_BAD_KEY, ACTION_NONE, False)
    if state is State.IDLE:
        return PrepareDecision(True, None, ACTION_LOAD, True)
    if state is State.SETUP and loaded_bit == bit:
        return PrepareDecision(True, None, ACTION_NOOP, True)
    return PrepareDecision(False, REASON_BUSY, ACTION_NONE, True)
