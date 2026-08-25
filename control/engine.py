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
from control.cues import ROOM, FireTrigger, LightCue, PlayCue
from control.device_pool import DevicePool
from control.registration import JoinResult, RegistrationState
from control.role_config import compose_role_config, validate_role_declarations
from control.roles import RoleClass
from control.room_profile import room_profile
from control.state import State
from control.triggers import (
    FIRED_BY_BIT_ADJUDICATED,
    FIRED_BY_GESTURE_VERB,
    SOURCE_WIRE,
    TriggerFired,
    TriggerTarget,
    expand_script,
    validate_trigger_table,
)

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
        self._warned_no_room = False     # once-per-Bit-load ROOM drop warning

    def hello(self, dev: str, name: str, protoversion: str) -> None:
        self.devices.hello(dev, name, protoversion, self._clock())
        self._notify("on_devices_change")

    def reap_stale(self, timeout: float) -> list[str]:
        """Remove every DevicePool entry silent for `timeout` seconds,
        freeing any role slot it held. See docs/superpowers/specs/
        2026-08-25-device-liveness-detection-design.md sections 4-5.

        A dev currently bound to a Room fixture is left untouched
        entirely -- Room liveness is a separate, not-yet-designed
        question (section 5 of that spec): reaping it would clear
        registration.assignments but not room.bound, leaving RoomBridge
        feeding a fixture whose device no longer exists.

        Never raises: on_release is guarded exactly like _unload()
        already guards it, so a failing transport cannot wedge this call
        or strand the remaining stale devices. Notifications are batched
        once per call, not once per device, matching _unload()'s existing
        shape.
        """
        now = self._clock()
        room_devs = (set(self.room.bound.values())
                    if self.room is not None else set())
        reaped: list[str] = []
        released_any = False
        for dev in self.devices.stale(now, timeout):
            if dev in room_devs:
                continue
            if self.registration is not None and \
                    dev in self.registration.assignments:
                self.registration.release(dev)
                released_any = True
                if self.on_release:
                    try:
                        self.on_release(dev)
                    except Exception:
                        logger.exception(
                            "on_release raised for %s during reap; "
                            "continuing", dev)
            self.devices.remove(dev)
            reaped.append(dev)
        # on_devices_change BEFORE on_registration_change, deliberately: see
        # harness/terrarium_boot.py's _LifecycleLogger. Its "device
        # released" line runs in on_devices_change, diffed against the
        # registration.assignments snapshot on_registration_change last
        # left behind -- if on_registration_change fired first, its "join
        # granted" bookkeeping would overwrite that snapshot to the
        # post-release state before the devices-diff ever ran, and the
        # released line would silently never print for a reaped
        # role-holding device. This order is what makes a timed-out role
        # holder print BOTH "released" and "timed out" (design spec section
        # 7), matching a graceful release/rejoin, where the two hooks never
        # race like this in the first place.
        if reaped:
            self._notify("on_devices_change")
        if released_any:
            self._notify("on_registration_change")
        return reaped

    def load_bit(self, name: str, config=None) -> None:
        if self.state != State.IDLE:
            raise InvalidTransition(
                f"load_bit requires IDLE, current state is {self.state}")
        self._set_state(State.LOADING)
        try:
            bit_cls = self.bit_registry[name]
            bit = bit_cls(config) if config is not None else bit_cls()
            role_table = bit.role_table
            validate_role_declarations(role_table)
            validate_trigger_table(bit.trigger_table, set(bit.verb_handlers()))
            registration = RegistrationState(role_table)
        except Exception as exc:
            self._set_state(State.IDLE)
            raise BitLoadError(f"failed to load Bit {name!r}: {exc}") from exc
        self.bit = bit
        self._warned_no_room = False
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
            try:
                self.bit.on_join(dev, result.role)
            except Exception:
                logger.exception("Bit.on_join failed; continuing")
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
        fixture = None
        if self.room_binding is not None and self.room is not None:
            fixture = self.room_binding.armed_fixture(self.room.room_type)
        if fixture is not None:
            if self.room_binding is not None:
                self.room_binding.bind(self.room.room_type, fixture, dev)
            self.room.bound[fixture] = dev
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
        self._dispatch_cues(cues, at, FIRED_BY_GESTURE_VERB)
        return None

    def _canonical_room_dev(self) -> str | None:
        """The Room's one dev for MIDI-feed purposes: the first bound
        fixture in the profile's declaration order. Room light/audio is one
        shared session (design spec section 2), so every path that feeds it
        -- the ROOM cue sentinel and TARGET-fanout across Room fixtures --
        must resolve to exactly this one dev, never to whichever fixture
        happened to bind first or most recently."""
        if self.room is None or not self.room.bound:
            return None
        profile = room_profile(self.room.room_type)
        for fixture in profile.fixtures:
            dev = self.room.bound.get(fixture.name)
            if dev is not None:
                return dev
        return None

    def _resolve_dev(self, dev: str) -> str | None:
        """cues.ROOM -> the Room's canonical dev; anything else passes
        through.

        Returns None when a ROOM cue has no Room to go to, which the caller
        treats as a drop, never a raise. Warned once per Bit load rather than
        once per cue: a 20 Hz gesture stream would otherwise flood the log.
        """
        if dev != ROOM:
            return dev
        canonical = self._canonical_room_dev()
        if canonical is None:
            if not self._warned_no_room:
                self._warned_no_room = True
                logger.warning("Bit emitted a ROOM cue with no Room bound; "
                               "dropping (logged once per Bit load)")
            return None
        return canonical

    def _resolve_target(self, target, dev: str | None) -> list[str]:
        """A trigger's declared target, resolved to the devs it lands on.

        Returns every bound Room fixture dev for ROOM, in declaration order
        -- this is the one-method change the N-fixture Room slice makes; no
        Bit's trigger declaration changes alongside it (design spec section
        5). This full list is what TriggerFired.devs reports; a script's
        TARGET fanout is collapsed separately, see _collapse_room_fanout.
        """
        if target is TriggerTarget.DEVICE:
            return [dev] if dev else []
        room_devs: list[str] = []
        if self.room is not None and self.room.bound:
            profile = room_profile(self.room.room_type)
            room_devs = [self.room.bound[f.name] for f in profile.fixtures
                        if f.name in self.room.bound]
        if target is TriggerTarget.ROOM:
            return room_devs
        out = list(room_devs)
        assignments = (self.registration.assignments
                       if self.registration is not None else {})
        for player, (_node, _role, role_class) in assignments.items():
            if role_class != RoleClass.ROOM and player not in out:
                out.append(player)
        return out

    def _collapse_room_fanout(self, devs: list[str]) -> list[str]:
        """A script step addressed at cues.TARGET fans out to every dev in
        `devs` (control/triggers.py's expand_script), one independent cue
        per dev. That is correct for player devices, each with its own
        LightSession, but wrong for the Room: every Room fixture dev in
        `devs` shares ONE session (design spec section 2), so feeding it
        once per fixture would double-apply the same relative MIDI. Collapse
        every Room-fixture dev down to the Room's single canonical dev, keep
        every other dev untouched and in order."""
        room_devs = set(self.room.bound.values()) if self.room is not None else set()
        if not room_devs:
            return devs
        canonical = self._canonical_room_dev()
        out: list[str] = []
        seen_room = False
        for d in devs:
            if d not in room_devs:
                out.append(d)
            elif not seen_room:
                out.append(canonical)
                seen_room = True
        return out

    def fire_trigger(self, name: str, *, fired_by: str,
                     dev: str | None = None,
                     at: float | None = None) -> str | None:
        """Fire one declared trigger: expand its script, dispatch it, and tell
        every observer it happened.

        Returns None when fired, else a refusal reason, and NEVER raises, for
        the same reason data() does not: neither a device nor a browser may be
        able to wedge Control.

        `fired_by` is what actually fired it THIS time, which is deliberately
        not the same field as the condition's declared source: an operator may
        fire a gesture-verb trigger by hand, and the record has to keep those
        two distinguishable or a manual action reads as gameplay.

        `at` is supplied by _dispatch_cues when a Bit fired this from a verb
        handler or from cues(), so the whole script shares that gesture's
        single presentation time. A manual fire has no origin, so it takes
        Control's clock plus the installation's horizon, exactly as a
        self-driven cue does.
        """
        if self.state not in (State.SETUP, State.RUNNING):
            return "no Bit running"
        try:
            table = self.bit.trigger_table
        except Exception:
            logger.exception("Bit.trigger_table raised; refusing to fire %r",
                             name)
            return "trigger table error"
        try:
            trigger = table.triggers.get(name)
            if trigger is None:
                return f"unknown trigger {name!r}"
            if trigger.target is TriggerTarget.DEVICE and not dev:
                return (f"trigger {name!r} targets the firing device; "
                        f"no device given")
            if at is None:
                at = self._clock() + self._horizon
            devs = self._resolve_target(trigger.target, dev)
            cues = expand_script(trigger, at, self._collapse_room_fanout(devs))
        except Exception:
            # trigger_table is a property: load_bit validated whatever it
            # returned on THAT one call, and the validated object is never
            # retained (the same hazard RegistrationState's role_table
            # snapshot exists to close for role_table). A later access can
            # return something else, so everything this trigger touches
            # between lookup and expansion is guarded here, not just the
            # property access above.
            logger.exception("trigger %r script expansion failed; refusing "
                             "to fire", name)
            return "trigger script error"
        # No fired_by passed on: expand_script only ever yields LightCue and
        # PlayCue, never FireTrigger, so this cannot recurse and a trigger
        # cannot chain into another. The guard above already contains
        # anything a divergent trigger_table could throw while producing
        # `cues`, so this call sees only well-formed cues.
        self._dispatch_cues(cues, at)
        self._notify("on_trigger_fired", TriggerFired(
            name=trigger.name,
            condition=trigger.condition.name,
            fired_by=fired_by,
            declared_source=SOURCE_WIRE[trigger.condition.source],
            dev=dev,
            devs=tuple(devs),
            at=at,
            steps=len(cues),
        ))
        return None

    def _dispatch_cues(self, cues, at: float | None,
                       fired_by: str | None = None) -> None:
        """Route a Bit's cues to the transport-owned sinks.

        Two things happen to every cue on the way out. A cue addressed to
        cues.ROOM is resolved to the Room's bound dev. And a cue that
        declares no time of its own gets `at`, the presentation time Control
        computed for whatever produced it -- which is what makes "one
        gesture, one T" hold without every Bit having to remember to say so.
        A Bit that DID name a time keeps it, because that is a deliberate
        derived offset (an echo at at+0.5), not an omission.

        Never raises. The whole per-cue block is guarded, not just the sink
        call: the 4-tuple unpack below is partial, so an arity-wrong cue from
        a buggy Bit would otherwise break data()'s documented "never raises"
        contract, and devicelink/agent.py's _on_verb has no handler around
        the call.
        """
        for cue in cues or ():
            try:
                if isinstance(cue, FireTrigger):
                    # A Bit reporting one of its own conditions satisfied.
                    # fire_trigger re-enters this method with the expanded
                    # script, carrying the same `at`, so a trigger fired from a
                    # gesture lands on the same frame as the ordinary cues
                    # returned beside it.
                    reason = self.fire_trigger(
                        cue.name,
                        fired_by=fired_by or FIRED_BY_BIT_ADJUDICATED,
                        dev=cue.dev, at=at)
                    if reason is not None:
                        logger.warning("Bit fired trigger %r: %s",
                                       cue.name, reason)
                    continue
                if isinstance(cue, PlayCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    sink, args = self.on_play_cue, (dev, cue.name, cue.params)
                elif isinstance(cue, LightCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    when = at if cue.when is None else cue.when
                    sink, args = self.on_light_cue, (dev, cue.status,
                                                     cue.data1, cue.data2,
                                                     when)
                else:
                    # The historic plain 4-tuple. It used to mean "apply on
                    # arrival"; it now means "apply at the time Control
                    # computed for this cue's origin".
                    dev_, status, d1, d2 = cue
                    dev = self._resolve_dev(dev_)
                    if dev is None:
                        continue
                    sink, args = self.on_light_cue, (dev, status, d1, d2, at)
                if sink is None:
                    continue
                sink(*args)
            except Exception:
                logger.exception("cue dispatch failed; continuing")

    def tick(self, dt: float) -> None:
        if self.state != State.RUNNING:
            return
        if self.bit.update(dt):
            self._complete()
            return
        self._dispatch_bit_cues()

    def _dispatch_bit_cues(self) -> None:
        """Drain Bit.cues() once per RUNNING tick. A self-driven cue has no
        gesture behind it, so its origin is Control's own clock.

        Guarded exactly like every other Bit hook: a raising cues() must not
        stop this Bit reaching COMPLETING.
        """
        at = self._clock() + self._horizon
        try:
            cues = self.bit.cues(at)
        except Exception:
            logger.exception("Bit.cues raised; ignoring this tick")
            return
        self._dispatch_cues(cues, at, FIRED_BY_BIT_ADJUDICATED)

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
