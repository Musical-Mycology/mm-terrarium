"""TeardownStack: a guarded, idempotent, LIFO stack of named teardown steps.

WHY THIS EXISTS. mm-terrarium had three separately-maintained teardown
orderings: control/boot.py's failure handler, harness/terrarium_boot.py's
build() failure handler, and harness/terrarium_boot.py's success-path
shutdown(). PR #24 corrected the first two and did not notice the third, so
on a normal, successful run the O2 hub was still being killed before the
o2lite clients that talk to it. Three lists that have to agree by hand is
the defect; a mechanism they all share is the fix.

THE INVARIANT, and the one thing to know before editing:

    Anything registered LATER is torn down EARLIER.

That is what makes client-before-hub structural. The devicelink server is
started before boot() and pushed first, so it stops last. Arco is spawned
next. The Room simulator is spawned after Arco and therefore stops before
it. An o2lite transport adopted after boot() returns stops before all of
them. Nobody maintains that order; it falls out of when things start.

Push order is DELIBERATE, not literally creation order. control/boot.py
creates Arco, then the GameServer, then the RoomBridge, but the Bit must
abort before the room bridge it may still cue into during on_unload -- so
boot() pushes the bridge step and then the Bit step, both after Arco. Push
points are chosen and documented at each call site.

WHY NOT contextlib.ExitStack. It unwinds LIFO and does continue past a
failing callback, but it re-raises the LAST exception and merely chains the
others as __context__, and it has no notion of step names. Teardown here
needs every failure, named, with the original boot failure staying primary.
"""

from __future__ import annotations

from typing import Callable


class TeardownStack:
    """Registered steps run in reverse order, each one guarded, once."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, Callable[[], None]]] = []
        self._closed = False

    def push(self, name: str, fn: Callable[[], None]) -> None:
        """Register a teardown step. `name` appears in the failure report,
        so make it the thing an operator would look for in a log: "arco",
        "simulator", "devicelink-server"."""
        if self._closed:
            raise RuntimeError(
                f"cannot push {name!r}: this TeardownStack is already closed, "
                f"so the step would never run and whatever it owns would be "
                f"orphaned silently")
        self._steps.append((name, fn))

    def close(self) -> list[tuple[str, BaseException]]:
        """Unwind every step in reverse push order and return the failures.

        Returns rather than raises: the caller nearly always has a more
        important exception in flight (the BootFailure that triggered
        teardown), and cleanup must never mask it.

        Catches BaseException per step, which means a KeyboardInterrupt
        raised INSIDE a step is captured rather than propagating. That is
        deliberate: a second Ctrl-C during teardown must not abandon the
        remaining steps and orphan a subprocess. Teardown is bounded now
        (control/process.py's stop_process), so completing it is safe.

        Idempotent. A second call is a no-op and returns an empty list, so
        boot()'s failure path and the caller's normal teardown can both call
        it without coordinating.
        """
        if self._closed:
            return []
        self._closed = True
        failures: list[tuple[str, BaseException]] = []
        while self._steps:
            name, fn = self._steps.pop()
            try:
                fn()
            except BaseException as exc:   # noqa: BLE001 (guarded by design)
                failures.append((name, exc))
        return failures
