"""GameServer: the Control+GameServer's lifecycle orchestrator. Owns the
state machine described in design spec section 3. O2-agnostic by design --
callers (a future O2lite transport layer) drive it through hello/load_bit/
run/join/tick and observe device releases via on_release.
"""

import logging

from control.bit import Bit
from control.device_pool import DevicePool
from control.registration import JoinResult, RegistrationState
from control.state import State

logger = logging.getLogger(__name__)


class InvalidTransition(Exception):
    """Raised when a trigger is called from a state that doesn't allow it."""


class BitLoadError(Exception):
    """Raised when load_bit fails to construct the named Bit."""


class GameServer:
    def __init__(self, bit_registry: dict):
        self.bit_registry = bit_registry
        self.state = State.IDLE
        self.devices = DevicePool()
        self.bit: Bit | None = None
        self.registration: RegistrationState | None = None
        # Set by a transport layer: called once per device released during
        # UNLOADING, so it can send that device's /ie<N>/release message.
        self.on_release = None

    def hello(self, dev: str, name: str, protoversion: str) -> None:
        self.devices.hello(dev, name, protoversion)

    def load_bit(self, name: str) -> None:
        if self.state != State.IDLE:
            raise InvalidTransition(
                f"load_bit requires IDLE, current state is {self.state}")
        self.state = State.LOADING
        try:
            bit_cls = self.bit_registry[name]
            bit = bit_cls()
        except Exception as exc:
            self.state = State.IDLE
            raise BitLoadError(f"failed to load Bit {name!r}: {exc}") from exc
        self.bit = bit
        self.registration = RegistrationState(bit.role_table)
        self.state = State.LOADED
        self._enter_setup()

    def _enter_setup(self) -> None:
        self.state = State.SETUP
        self.bit.on_setup_enter()

    def run(self) -> None:
        if self.state != State.SETUP:
            raise InvalidTransition(
                f"run requires SETUP, current state is {self.state}")
        self.state = State.RUNNING
        self.bit.on_run_start()

    def join(self, dev: str, node: str) -> JoinResult:
        if self.state not in (State.SETUP, State.RUNNING):
            return JoinResult(granted=False,
                               reason="no Bit accepting registrations")
        return self.registration.join(dev, node, self.state)

    def tick(self, dt: float) -> None:
        if self.state != State.RUNNING:
            return
        if self.bit.update(dt):
            self._complete()

    def _complete(self) -> None:
        self.state = State.COMPLETING
        try:
            self.bit.on_complete()
        except Exception:
            logger.exception("Bit.on_complete raised; unloading anyway")
        self._unload()

    def _unload(self) -> None:
        self.state = State.UNLOADING
        released = self.registration.release_all()
        if self.on_release:
            for dev in released:
                self.on_release(dev)
        try:
            self.bit.on_unload()
        except Exception:
            logger.exception("Bit.on_unload raised; returning to IDLE anyway")
        self.bit = None
        self.registration = None
        self.state = State.IDLE
