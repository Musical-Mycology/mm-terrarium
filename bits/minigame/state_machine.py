"""A small state machine for a Bit: named states with on_enter, on_tick and
on_exit hooks.

Every hook takes `at`, the presentation time the engine hands the Bit (a
verb handler's `at`, or `fires(at)`'s), and returns a list of outputs -- for
a Bit, the cues or FireFunctions it would otherwise return itself. The
machine concatenates them in the order they happened: the old state's
on_exit, then the new state's on_enter.

A hook never switches state directly. It calls `self.request(name, at)`, and
the machine carries the transition out once the hook has returned, so one
state's exit never runs inside another state's hook. `at` on a request is
the transition's own time (an exit stamped at a scheduled moment rather than
this tick's); omitted, it is the time of the call that made the request.

Pure stdlib: a Bit package may use only the stdlib and mm-terrarium.
"""

from __future__ import annotations

# A chain of on_enter hooks each requesting the next state is legal, but one
# that never settles is a bug in the states; stop it rather than hang a tick.
MAX_CHAINED_TRANSITIONS = 16


class State:
    """One state. Subclasses set `name` and override the hooks they need;
    every hook defaults to doing nothing."""

    name: str = ""

    def __init__(self) -> None:
        self.machine: StateMachine | None = None

    def on_enter(self, at: float | None) -> list:
        """Called once as the machine enters this state."""
        return []

    def on_tick(self, at: float | None) -> list:
        """Called on every StateMachine.tick() while this is the current
        state."""
        return []

    def on_exit(self, at: float | None) -> list:
        """Called once as the machine leaves this state."""
        return []

    def request(self, name: str, at: float | None = None) -> None:
        """Ask the machine to move to `name` once this hook returns."""
        self.machine.request(name, at)


class StateMachine:
    def __init__(self, states, initial: str) -> None:
        self.states: dict[str, State] = {}
        for state in states:
            if not state.name:
                raise ValueError(f"{type(state).__name__} has no name")
            if state.name in self.states:
                raise ValueError(f"duplicate state name {state.name!r}")
            state.machine = self
            self.states[state.name] = state
        if initial not in self.states:
            raise ValueError(f"initial state {initial!r} is not one of "
                             f"{sorted(self.states)}")
        self.initial = initial
        self.current: State | None = None
        self._pending: tuple[str, float | None] | None = None

    @property
    def state(self) -> str | None:
        """The current state's name, or None before start()."""
        return self.current.name if self.current is not None else None

    def start(self, at: float | None = None) -> list:
        """Enter the initial state. A fresh start: whatever state the
        machine was in is dropped without its on_exit."""
        self.current = None
        self._pending = None
        return self.transition(self.initial, at)

    def transition(self, name: str, at: float | None = None) -> list:
        """Leave the current state and enter `name` now, then carry out any
        transition the entered state requested. Re-entering the current
        state runs its on_exit and on_enter again."""
        if name not in self.states:
            raise ValueError(f"no state {name!r}; states are "
                             f"{sorted(self.states)}")
        out: list = []
        for _ in range(MAX_CHAINED_TRANSITIONS):
            if self.current is not None:
                out.extend(self.current.on_exit(at) or ())
            self.current = self.states[name]
            out.extend(self.current.on_enter(at) or ())
            if self._pending is None:
                return out
            name, req_at = self._pending
            self._pending = None
            at = at if req_at is None else req_at
        raise RuntimeError(f"state transitions did not settle after "
                           f"{MAX_CHAINED_TRANSITIONS} steps")

    def request(self, name: str, at: float | None = None) -> None:
        """Queue a transition for when the running hook returns. A later
        request in the same hook replaces an earlier one."""
        if name not in self.states:
            raise ValueError(f"no state {name!r}; states are "
                             f"{sorted(self.states)}")
        self._pending = (name, at)

    def tick(self, at: float | None = None) -> list:
        """Run the current state's on_tick, then any transition it
        requested. Does nothing before start()."""
        if self.current is None:
            return []
        out = list(self.current.on_tick(at) or ())
        if self._pending is not None:
            name, req_at = self._pending
            self._pending = None
            out.extend(self.transition(name, at if req_at is None else req_at))
        return out
