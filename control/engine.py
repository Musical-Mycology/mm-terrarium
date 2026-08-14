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
import time

from control.bit import Bit
from control.cues import LightCue, PlayCue
from control.device_pool import DevicePool
from control.registration import JoinResult, RegistrationState
from control.role_config import compose_role_config, validate_role_declarations
from control.roles import RoleClass
from control.state import State

logger = logging.getLogger(__name__)

# How far ahead of Control's own clock a device-supplied gesture timestamp may
# be before it is refused. A device whose clock is wrong could otherwise park a
# cue hours into the future and hold a TimedQueue entry through teardown.
# Refused stamps fall back to Control's clock and are counted -- see
# GameServer.rejected_stamps.
_MAX_GESTURE_LEAD = 5.0


class InvalidTransition(Exception):
    """Raised when a trigger is called from a state that doesn't allow it."""


class BitLoadError(Exception):
    """Raised when load_bit fails to construct the named Bit."""


class GameServer:
    def __init__(self, bit_registry: dict, room_binding=None,
                 cue_horizon: float = 0.0, clock=time.monotonic):
        self.bit_registry = bit_registry
        self.state = State.IDLE
        self.devices = DevicePool()
        # Control-global Room state (see control/rooms.py, control/
        # room_binding.py). Both may be None for a GameServer that predates
        # the Room concept -- join() below treats that exactly as "no Room
        # node exists," leaving normal player joins untouched.
        self.room_binding = room_binding
        self.room = None
        self.bit: Bit | None = None
        # Registry key of the loaded Bit; provenance for /ie<N>/role blobs
        # and the Console. Set in load_bit, cleared in _unload.
        self.bit_name: str | None = None
        self.registration: RegistrationState | None = None
        # Set by a transport layer: called once per device released during
        # UNLOADING, so it can send that device's /ie<N>/release message.
        self.on_release = None
        # Set by a transport layer: called when a Bit's verb handler emits a
        # light cue, as on_light_cue(dev, status, data1, data2, when).
        # Boundary rule 3 -- the Bit decides the light consequence, the
        # transport only delivers it to that device's renderer.
        self.on_light_cue = None
        # Device-local sample cue, as on_play_cue(dev, name, params). Same
        # boundary rule as on_light_cue: set by a transport, never by a Bit.
        self.on_play_cue = None
        # Observers registered via add_observer(). Each may implement any of
        # on_state_change(old, new), on_registration_change(),
        # on_devices_change(); missing methods are skipped. Both the uplink
        # and the Terrarium Console attach here and run simultaneously.
        self._observers: list = []
        # BootConfig.cue_horizon. ONE installation-wide constant: every cue's
        # target time is origin + horizon, computed here and nowhere else, so
        # a Bit receives a finished time rather than the ingredients. A
        # per-cue horizon would let two cues from one gesture land on
        # different frames and would make the clamp counters uninterpretable.
        self._horizon = cue_horizon
        # MUST be the same callable DeviceLinkAgent was built with. Two clock
        # bases is the bug that made the 2026-08-13 live run dark: Control
        # stamped frames off time.monotonic (~518,000) while the device
        # ticked on the O2 clock (~45). See harness/terrarium_boot.py build().
        self._clock = clock
        # Gesture stamps refused for being implausibly far ahead (see
        # _MAX_GESTURE_LEAD). A rising count means a device's clock is wrong.
        self.rejected_stamps = 0

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
        if self._is_room_node(node) and not self._room_armed():
            return JoinResult(granted=False, reason="no such node")
        result = self.registration.join(dev, node, self.state)
        if result.granted and result.role_class == RoleClass.ROOM:
            self._bind_room(dev)
            return result
        if result.granted:
            # Compose from the registration's role-table snapshot -- Bits
            # build role_table per property access, so a fresh call could
            # return different Role objects than the ones counts track.
            role = self.registration.role_table.roles[result.role]
            result.config = compose_role_config(
                self.bit_name, self.bit.version, role)
            self._notify("on_registration_change")
            self._notify("on_devices_change")
        return result

    def _is_room_node(self, node: str) -> bool:
        for role_name in self.registration.role_table.node_map.get(node, ()):
            role = self.registration.role_table.roles.get(role_name)
            if role is not None and role.role_class == RoleClass.ROOM:
                return True
        return False

    def _room_armed(self) -> bool:
        if self.room_binding is None or self.room is None:
            return False
        return self.room_binding.is_armed(self.room.room_type)

    def _bind_room(self, dev: str) -> None:
        if self.room_binding is not None and self.room is not None:
            self.room_binding.bind(self.room.room_type, dev)
        if self.room is not None:
            self.room.bound_dev = dev
        self._notify("on_devices_change")

    def _origin(self, gesture_time: float | None) -> float:
        """Resolve a cue's origin time: the device's own stamp when it is
        usable, else Control's clock.

        Three ways a stamp is unusable, all real. The websocket transport
        never stamps at all (devicelink/protocol.py's _event defaults
        timestamp=0.0). o2lite returns -1 until clock sync completes. And a
        device with a broken clock can send something implausible.
        """
        now = self._clock()
        if gesture_time is None or gesture_time <= 0:
            return now
        if gesture_time > now + _MAX_GESTURE_LEAD:
            self.rejected_stamps += 1
            logger.warning("refusing gesture stamp %.3f: more than %.1fs "
                           "ahead of %.3f", gesture_time, _MAX_GESTURE_LEAD,
                           now)
            return now
        return gesture_time

    def data(self, dev: str, verb: str, args: list,
             gesture_time: float | None = None) -> str | None:
        """Route a /game/<verb> message to the loaded Bit's verb handler.

        Returns None when handled, else a refusal reason a transport can
        surface as /<dev>/error. The reason is either engine-level (no Bit
        running, device not registered, unknown verb, handler raised) or
        handler-declared: a handler returning a str is refusing. Never
        raises: a device must never be able to wedge Control, exactly as a
        Bit must never be able to.

        `gesture_time` is the inbound envelope's timestamp: the device's own
        reading of the O2 clock at the instant of the gesture. Control adds
        the installation's cue_horizon to it to get `at`, the time the
        consequence should be PRESENTED, and hands that to the handler.
        """
        if self.state not in (State.SETUP, State.RUNNING):
            return "no Bit running"
        if dev not in self.registration.assignments:
            return "device not registered"
        try:
            handler = self.bit.verb_handlers().get(verb)
        except Exception:
            logger.exception("Bit.verb_handlers raised; refusing %r", verb)
            return "handler error"
        if handler is None:
            return f"unknown verb {verb!r}"
        at = self._origin(gesture_time) + self._horizon
        try:
            cues = handler(dev, args, at)
        except Exception:
            logger.exception("Bit verb handler %r raised; ignoring", verb)
            return "handler error"
        if isinstance(cues, str):
            # A handler-declared refusal. Checked BEFORE the truthiness test
            # below, which would otherwise iterate the string character by
            # character and try to unpack each character as a cue tuple.
            # `or` guards a blank reason so /<dev>/error is never empty.
            return cues or "handler refused"
        for cue in cues or ():
            # The whole per-cue block is guarded, not just the sink call.
            # The old code was `sink, args = self.on_light_cue, tuple(cue)`,
            # which is total: it never raised for any-length iterable. The
            # 4-tuple unpack below is partial, so an arity-wrong cue from a
            # buggy Bit would otherwise raise straight out of data() and
            # break its documented "never raises" contract -- and
            # devicelink/agent.py's _on_verb has no handler around the call.
            try:
                if isinstance(cue, PlayCue):
                    sink, args = self.on_play_cue, (cue.dev, cue.name,
                                                    cue.params)
                elif isinstance(cue, LightCue):
                    sink, args = self.on_light_cue, (cue.dev, cue.status,
                                                     cue.data1, cue.data2,
                                                     cue.when)
                else:
                    # The historic plain 4-tuple: no declared time.
                    dev_, status, d1, d2 = cue
                    sink, args = self.on_light_cue, (dev_, status, d1, d2,
                                                     None)
                if sink is None:
                    continue
                sink(*args)
            except Exception:
                logger.exception("cue dispatch failed; continuing")
        return None

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
                try:
                    self.on_release(dev)
                except Exception:
                    logger.exception(
                        "on_release raised for %s; continuing", dev)
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
