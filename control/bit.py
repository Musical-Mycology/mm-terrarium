"""Base interface every Bit implements. See design spec section 4."""

from abc import ABC, abstractmethod

from control.roles import RoleTable


class Bit(ABC):
    """A loadable game/experience module for the Control+GameServer.

    Subclasses must provide `role_table`. All lifecycle hooks below are
    no-ops by default; a Bit overrides only the ones it needs.
    """

    @property
    @abstractmethod
    def role_table(self) -> RoleTable:
        """This Bit's static role declarations (control.roles.RoleTable)."""

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
        """
        return {}
