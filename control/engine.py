"""GameServer: the Control+GameServer's lifecycle orchestrator. Owns the
state machine described in design spec section 3. O2-agnostic by design --
callers (a future O2lite transport layer) drive it through hello/load_bit/
run/join/tick and observe device releases via on_release. Also observable
by the Terrarium uplink
(docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md) via
on_state_change/on_registration_change, and remotely abortable via
abort() -- GameServer stays agnostic to who's watching or calling either.
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
        # Set by UplinkAgent: called with (old_state, new_state) on every
        # state transition.
        self.on_state_change = None
        # Set by UplinkAgent: called with no arguments after a join grants a
        # role. Callers read self.registration.counts() for the snapshot.
        self.on_registration_change = None

    def hello(self, dev: str, name: str, protoversion: str) -> None:
        self.devices.hello(dev, name, protoversion)

    def load_bit(self, name: str) -> None:
        if self.state != State.IDLE:
            raise InvalidTransition(
                f"load_bit requires IDLE, current state is {self.state}")
        self._set_state(State.LOADING)
        try:
            bit_cls = self.bit_registry[name]
            bit = bit_cls()
        except Exception as exc:
            self._set_state(State.IDLE)
            raise BitLoadError(f"failed to load Bit {name!r}: {exc}") from exc
        self.bit = bit
        self.registration = RegistrationState(bit.role_table)
        self._set_state(State.LOADED)
        self._enter_setup()

    def _enter_setup(self) -> None:
        self._set_state(State.SETUP)
        self.bit.on_setup_enter()

    def run(self) -> None:
        if self.state != State.SETUP:
            raise InvalidTransition(
                f"run requires SETUP, current state is {self.state}")
        self._set_state(State.RUNNING)
        self.bit.on_run_start()

    def join(self, dev: str, node: str) -> JoinResult:
        if self.state not in (State.SETUP, State.RUNNING):
            return JoinResult(granted=False,
                               reason="no Bit accepting registrations")
        result = self.registration.join(dev, node, self.state)
        if result.granted and self.on_registration_change:
            self.on_registration_change()
        return result

    def tick(self, dt: float) -> None:
        if self.state != State.RUNNING:
            return
        if self.bit.update(dt):
            self._complete()

    def abort(self) -> None:
        """Force an early end to the current Bit from any non-IDLE state.
        Runs the same best-effort on_complete/on_unload cleanup as a normal
        completion, then unloads. Safe from LOADING/LOADED/SETUP/RUNNING/
        COMPLETING/UNLOADING -- load_bit() is fully synchronous, so
        self.bit and self.registration are always set together by the time
        any external caller can observe a non-IDLE state.
        """
        if self.state == State.IDLE:
            raise InvalidTransition("abort requires an active Bit")
        self._run_on_complete()
        self._unload()

    def _complete(self) -> None:
        self._set_state(State.COMPLETING)
        self._run_on_complete()
        self._unload()

    def _run_on_complete(self) -> None:
        try:
            self.bit.on_complete()
        except Exception:
            logger.exception("Bit.on_complete raised; unloading anyway")

    def _unload(self) -> None:
        self._set_state(State.UNLOADING)
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
        self._set_state(State.IDLE)

    def _set_state(self, new_state: State) -> None:
        old_state = self.state
        self.state = new_state
        if self.on_state_change:
            self.on_state_change(old_state, new_state)
