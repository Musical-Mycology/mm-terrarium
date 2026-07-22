"""GameServer: the Control+GameServer's lifecycle orchestrator. Owns the
state machine described in design spec section 3. O2-agnostic by design --
callers (a future O2lite transport layer) drive it through hello/load_bit/
run/join/tick and observe device releases via on_release. Also observable
by any number of add_observer() observers (the Terrarium uplink
and the Terrarium Console both attach) via on_state_change/
on_registration_change/on_devices_change, and remotely abortable via
abort() -- GameServer stays agnostic to who's watching or calling either.
"""

import logging

from control.bit import Bit
from control.device_pool import DevicePool
from control.registration import JoinResult, RegistrationState
from control.role_config import validate_role_declarations
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
        # Registry key of the loaded Bit; provenance for /ie<N>/role blobs
        # and the Console. Set in load_bit, cleared in _unload.
        self.bit_name: str | None = None
        self.registration: RegistrationState | None = None
        # Set by a transport layer: called once per device released during
        # UNLOADING, so it can send that device's /ie<N>/release message.
        self.on_release = None
        # Observers registered via add_observer(). Each may implement any of
        # on_state_change(old, new), on_registration_change(),
        # on_devices_change(); missing methods are skipped. Both the uplink
        # and the Terrarium Console attach here and run simultaneously.
        self._observers: list = []

    def hello(self, dev: str, name: str, protoversion: str) -> None:
        self.devices.hello(dev, name, protoversion)
        self._notify("on_devices_change")

    def load_bit(self, name: str) -> None:
        if self.state != State.IDLE:
            raise InvalidTransition(
                f"load_bit requires IDLE, current state is {self.state}")
        self._set_state(State.LOADING)
        try:
            bit_cls = self.bit_registry[name]
            bit = bit_cls()
            role_table = bit.role_table
            validate_role_declarations(role_table)
            registration = RegistrationState(role_table)
        except Exception as exc:
            self._set_state(State.IDLE)
            raise BitLoadError(f"failed to load Bit {name!r}: {exc}") from exc
        self.bit = bit
        self.bit_name = name
        self.registration = registration
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
        if result.granted:
            self._notify("on_registration_change")
            self._notify("on_devices_change")
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
        self._notify("on_devices_change")
        try:
            self.bit.on_unload()
        except Exception:
            logger.exception("Bit.on_unload raised; returning to IDLE anyway")
        self.bit = None
        self.bit_name = None
        self.registration = None
        self._set_state(State.IDLE)

    def add_observer(self, observer) -> None:
        """Register an observer object. The engine calls, when present,
        observer.on_state_change(old, new), observer.on_registration_change(),
        and observer.on_devices_change(). Notification is in registration
        order; a raising observer is logged and never interrupts the engine
        or its peers.
        """
        self._observers.append(observer)

    def _notify(self, method: str, *args) -> None:
        for observer in self._observers:
            callback = getattr(observer, method, None)
            if callback is None:
                continue
            try:
                callback(*args)
            except Exception:
                logger.exception("observer %r %s raised; continuing",
                                 observer, method)

    def _set_state(self, new_state: State) -> None:
        old_state = self.state
        self.state = new_state
        self._notify("on_state_change", old_state, new_state)
