"""Shared test doubles for the mm-terrarium test suite.

Tier 2 consolidation, PR A
(docs/superpowers/specs/2026-09-25-tier2-consolidation-design.md). Each class
here replaces two or more near-identical local copies that used to be
defined inside individual test functions across tests/test_terrarium_boot.py
and other test modules.

Bespoke doubles that only ever had ONE real use -- the closing-fade
countdown agent, the poll-driven state-flip agent tied to a specific gs,
FakeRegistration (two genuinely different constructor shapes), the wide
fixed-attribute _FakeTerrarium, tests/test_run_stack.py's child-wrapping
_RecordingPopen -- are NOT here. (test_terrarium_boot.py's own three
identical _RecordingPopen copies become one module-level class in that
file, Task A4 Step 5b.) They stay local to their one call site.
"""
from __future__ import annotations

from control.terrarium import TerrariumState


class FakeAgent:
    """Stands in for harness/o2_shroom.py's Agent in the terrarium_boot
    poll loops. `closing` mirrors the real agent's attribute the loops
    read via `getattr(agent, "closing", 0)`. `poll_error`, when given, is
    raised (the same exception instance, every call) instead of counting
    -- use this for a test asserting a loop must never reach poll() at
    all, e.g. `FakeAgent(poll_error=AssertionError("must not poll..."))`.
    `polls` counts every call, whether or not poll_error is set."""

    def __init__(self, closing=0, poll_error=None):
        self.closing = closing
        self.poll_error = poll_error
        self.polls = 0

    def poll(self):
        self.polls += 1
        if self.poll_error is not None:
            raise self.poll_error


class FakeArco:
    """Stands in for the Arco subprocess handle the boot loops poll().
    `returncode=None` (the default) models a live process; any other
    value models one that already exited. `poll_error`, when given, is
    raised instead of returning `returncode`. `returncode` is a plain
    public attribute, so a test may still flip it after construction
    (e.g. `arco.returncode = 1`) instead of passing it at construction
    time."""

    def __init__(self, returncode=None, poll_error=None):
        self.returncode = returncode
        self.poll_error = poll_error
        self.polls = 0

    def poll(self):
        self.polls += 1
        if self.poll_error is not None:
            raise self.poll_error
        return self.returncode


class StaticGS:
    """A GameServer double whose `.state` never changes on its own.
    `tick_error`, when given, is raised by tick() instead of no-op'ing --
    for a loop that must never call tick() once some other condition
    (parent-gone, no-room, ...) has already fired. `ticks` counts every
    tick() call, error or not."""

    def __init__(self, state, tick_error=None):
        self.state = state
        self.tick_error = tick_error
        self.ticks = 0

    def tick(self, dt):
        self.ticks += 1
        if self.tick_error is not None:
            raise self.tick_error


class TickingGS:
    """A GameServer double whose `.state` flips from `state` to
    `end_state` once tick() has been called `after` times (default 3,
    matching every current call site)."""

    def __init__(self, state, end_state, after=3):
        self.state = state
        self.end_state = end_state
        self.after = after
        self.ticks = 0

    def tick(self, dt):
        self.ticks += 1
        if self.ticks >= self.after:
            self.state = self.end_state


class RoomlessTerrarium:
    """Stands in for a Terrarium in the `_serve_roomless`/
    `_restart_room_clients` supporting cast. `.arco` defaults to a fresh
    FakeArco(). `unload_room(force=False)` always appends `force` to
    `.unload_calls` (a list, so a caller that only wants a count reads
    `len(unload_calls)`); when `unload_to_no_room` is True (the default)
    it also flips `.state` to TerrariumState.NO_ROOM. Pass
    `unload_to_no_room=False` for a caller whose original local double
    never transitioned state on unload."""

    def __init__(self, unload_to_no_room=True):
        self.state = TerrariumState.ROOM_READY
        self.arco = FakeArco()
        self.unload_calls = []
        self._unload_to_no_room = unload_to_no_room

    def unload_room(self, force=False):
        self.unload_calls.append(force)
        if self._unload_to_no_room:
            self.state = TerrariumState.NO_ROOM


class RecordingClient:
    """Stands in for a transport or pool client whose start()/stop()/
    quiesce() calls a recycle/restart test asserts an order over.
    `calls` is the list every call appends a label to -- pass the SAME
    list to a transport instance and a pool instance to get one merged
    call-order record, or a fresh list per instance to record each one
    separately. `prefix` names this instance ("pool" or "transport") in
    the recorded labels. `start_error`, when given, is raised by
    start() instead of recording.

    start() accepts an optional positional `o2` and keyword-only `pump`
    to match both real call shapes (`pool.start()` and
    `transport.start(o2, *, pump=None)`): a "transport" instance
    records the tuple `(f"{prefix}-start", o2)`; any other prefix
    records the bare string `f"{prefix}-start"`."""

    def __init__(self, calls, prefix, start_error=None):
        self.calls = calls
        self.prefix = prefix
        self.start_error = start_error

    def start(self, o2=None, *, pump=None):
        if self.start_error is not None:
            raise self.start_error
        if self.prefix == "transport":
            self.calls.append((f"{self.prefix}-start", o2))
        else:
            self.calls.append(f"{self.prefix}-start")

    def stop(self):
        self.calls.append(f"{self.prefix}-stop")

    def quiesce(self):
        self.calls.append(f"{self.prefix}-quiesce")


class FakeClock:
    """Shared fake time source. `.now` is the current time; `__call__()`
    returns it (the shape every `clock`/`time_source` parameter this
    replaces expects); `advance(seconds)` adds to it. `.now` is a plain
    public attribute, so a caller may also mutate it directly
    (`clock.now += 5.0`) instead of calling advance()."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeObservable:
    """Stands in for both `gs` and `terrarium` in tests that only need a
    no-op `add_observer()` plus fixed `room`/`state`/`bit`/`bit_name`
    attributes -- instantiate it once per role
    (`gs = FakeObservable(); terrarium = FakeObservable()`)."""

    room = None
    state = TerrariumState.NO_ROOM
    bit = None
    bit_name = None

    def add_observer(self, observer):
        pass
