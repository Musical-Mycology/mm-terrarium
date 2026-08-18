"""Base interface every Bit implements. See design spec section 4."""

from abc import ABC, abstractmethod

from control.roles import RoleTable
from control.rooms import RoomType
from control.triggers import TriggerTable


class Bit(ABC):
    """A loadable game/experience module for the Control+GameServer.

    Subclasses must provide `role_table`. All lifecycle hooks below are
    no-ops by default; a Bit overrides only the ones it needs.
    """

    # Bit identity for provenance stamping (light-manifest v2 bit_version).
    # The bit *name* is the registry key GameServer loaded it under -- not
    # an attribute here, so there is nothing for an author to keep in sync.
    version: str = ""

    # Which RoomTypes this Bit can run in. Every Bit supports at least
    # RoomType.TEST (the universal baseline); a Bit declares more by
    # overriding this class attribute. Read off the class (not an instance)
    # by control/boot.py's Bit-gating check, before the Bit is constructed.
    # Treat as override-only -- do not mutate this set in place, since it is
    # shared across every instance of a Bit that doesn't override it.
    room_types: set[RoomType] = {RoomType.TEST}

    @property
    @abstractmethod
    def role_table(self) -> RoleTable:
        """This Bit's static role declarations (control.roles.RoleTable)."""

    @property
    def trigger_table(self) -> TriggerTable:
        """This Bit's declared triggers: the named things an operator can see
        coming, each with a description, a target, a condition this Bit
        evaluates itself, and a declarative cue script.

        A plain property with an empty default, deliberately not abstract the
        way role_table is, so every Bit written before triggers existed keeps
        working untouched. Validated at load_bit (control/triggers.py), so a
        trigger declared against a verb this Bit does not implement fails as a
        BitLoadError rather than mid-installation.
        """
        return TriggerTable(triggers={})

    def on_setup_enter(self) -> None:
        """Called once when Control enters SETUP for this Bit."""

    def on_run_start(self) -> None:
        """Called once when Control enters RUNNING for this Bit."""

    def update(self, dt: float) -> bool:
        """Called once per tick while RUNNING.

        Return True to signal this Bit is finished; Control transitions to
        COMPLETING on the next tick. Default: never completes on its own.
        """
        return False

    def cues(self, at: float) -> list:
        """Self-driven cues for this tick, in the same vocabulary a verb
        handler returns: plain (dev, status, data1, data2) tuples,
        control.cues.LightCue, control.cues.PlayCue, and the
        control.cues.ROOM target.

        Called once per RUNNING tick, after update(dt), and skipped on the
        tick update() signals completion. `at` is the absolute time at which
        these cues should be PRESENTED; Control has already added the
        installation's cue_horizon to its own clock.

        This is the only way a Bit can animate anything without a device
        doing something: verb_handlers() can only ever react to a gesture,
        which is why the Room's light used to reach its declared static hue
        once and hold it for a whole run. Default: nothing to emit.
        """
        return []

    def on_complete(self) -> None:
        """Called once when Control enters COMPLETING (scoring, closing actions)."""

    def result(self) -> dict | None:
        """Optional completion payload (e.g. score/outcome) for the uplink
        to relay upstream once this Bit finishes. Default: nothing to
        report.
        """
        return None

    def status(self) -> dict:
        """Optional generic key/value read-out for the Terrarium Console to
        render as a table. Default: nothing to report. A Bit overrides this
        to surface its own live state. This is also the seam a future
        Lux Aeterna / Arco health read-out rides on.
        """
        return {}

    def on_unload(self) -> None:
        """Called once when Control enters UNLOADING, after devices are released."""

    def verb_handlers(self) -> dict:
        """Extra /game/* verb handlers this Bit adds, beyond the fixed
        lifecycle verbs Control always handles. Empty by default.

        A handler is called as handler(dev, args, at), where `at` is the
        absolute O2 time at which this gesture's consequence should be
        PRESENTED -- Control has already added the installation's
        cue_horizon to the device's own gesture stamp, so a Bit never sees
        the horizon and never sees a raw stamp. A handler returns either a
        list
        of (dev, status, data1, data2) light cues, or a str refusal reason
        that Control surfaces to that device as /<dev>/error. Returning a
        str is how a Bit rejects a well-formed call for its own reasons;
        raising is for bugs and yields a generic "handler error".
        """
        return {}
