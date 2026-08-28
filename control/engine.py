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
from control.cues import ROOM, FireFunction, LightCue, MuteCue, PlayCue, SolidCue
from control.device_pool import DevicePool
from control.generator_runner import GeneratorRunner
from control.instrument import (TUNESHROOM, InstrumentRequirement, cue_kind,
                                 satisfies)
from control.registration import JoinResult, RegistrationState
from control.role_config import compose_role_config, validate_role_declarations
from control.roles import RoleClass
from control.rooms import room_role
from control.state import State
from control.functions import (
    FIRED_BY_BIT_ADJUDICATED,
    FIRED_BY_GESTURE_VERB,
    SOURCE_WIRE,
    Function,
    FunctionFired,
    FunctionKind,
    FunctionTarget,
    expand_script,
    stream_cues,
    stream_input,
    validate_function_table,
)

logger = logging.getLogger(__name__)

# How far ahead of Control's own clock a device-supplied gesture timestamp may
# be before it is refused. A device whose clock is wrong could otherwise park a
# cue hours into the future and hold a TimedQueue entry through teardown.
# Refused stamps fall back to Control's clock and are counted -- see
# GameServer.rejected_stamps.
_MAX_GESTURE_LEAD = 5.0


class InvalidTransition(Exception):
    """Raised when a function is called from a state that doesn't allow it."""


class BitLoadError(Exception):
    """Raised when load_bit fails to construct the named Bit."""


def _resolve_room_requirements(requirements, room) -> None:
    """Check each non-optional requirement against the active Room's whole
    profile, aggregated across fixtures (spec section 4): a requirement's
    capabilities may be satisfied by DIFFERENT fixtures, not just one, and
    min_pixels checks the profile's total pixel_count, not any one fixture's.
    Raises ValueError (wrapped by load_bit into BitLoadError) naming the
    unmet slot and every fixture's own satisfies() reason."""
    profile = room.profile
    for req in requirements:
        if req.optional:
            continue
        reasons = []
        advertised: set = set()
        for fixture in profile.fixtures:
            advertised |= fixture.instrument.capabilities
            reason = satisfies(fixture.instrument, req,
                                pixel_count=profile.pixel_count)
            if reason is not None:
                reasons.append(reason)
        missing = req.capabilities - advertised
        if missing or req.min_pixels > profile.pixel_count:
            raise ValueError(
                f"no fixture satisfies slot {req.slot!r}: "
                + "; ".join(reasons))


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
        # Device-local solid-color override, as on_solid_cue(dev, rgb, level,
        # duration, when). Same boundary rule as on_light_cue/on_play_cue:
        # set by a transport, applied at the frame-building seam, never by a
        # Bit directly.
        self.on_solid_cue = None
        # Called whenever a device's mute latch changes, as
        # on_mute_change(dev, muted). Set by a transport; the Console reads
        # `muted` (below) to learn latch state.
        self.on_mute_change = None
        # Resolved dev ids currently latched dark/silent by a MuteCue (the
        # Stop function). Cleared per-dev by any non-mute fire at that surface
        # (see fire_function/_clear_mutes) and wholesale on _unload.
        self.muted: set[str] = set()
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
        # Provenance stamp for the active Room, set by control/terrarium.py's
        # load_room on success and cleared by unload_room (also on a failed
        # load's unwind). {} outside a Room. join() and fire_function() read
        # this so role blobs and function records carry room_name/
        # terrarium_config_version without GameServer knowing anything about
        # TerrariumConfig itself.
        self.provenance: dict = {}
        # Snapshot of the loaded Bit's declared instrument-requirement slots
        # (control/instrument.py's InstrumentRequirement), keyed by slot
        # name. Set in load_bit on success, cleared on _unload. Consumed by
        # join() to gate a Role.requires slot against its resolved
        # requirement (see spec section 4) -- empty outside a loaded Bit.
        self._slot_requirements: dict[str, InstrumentRequirement] = {}
        # This Bit's declared GENERATOR functions, evaluated once per
        # RUNNING tick by _dispatch_generator_cues. Built in load_bit from
        # the validated table (per-lane uniqueness already enforced there);
        # None outside a loaded Bit. See control/generator_runner.py.
        self._generators: GeneratorRunner | None = None
        # This Bit's declared STREAM functions, keyed by the verb they read
        # gesture args from, declaration order preserved per verb. Built in
        # load_bit from the same validated table _generators is built from;
        # empty outside a loaded Bit. Consumed by data() to map a gesture's
        # args onto MIDI lanes without a Bit handler in the loop at all.
        self._stream_functions: dict[str, list[Function]] = {}
        # Seconds elapsed since run() started, accumulated in tick() and
        # reset there. The clock GeneratorRunner.cues() samples -- distinct
        # from self._clock() (wall/O2 time, used for `at`) so a generator's
        # phase is deterministic in how long the Bit has been RUNNING, not
        # in wall-clock time.
        self._run_elapsed: float = 0.0

    def slot_requirement(self, slot: str) -> "InstrumentRequirement | None":
        """Public read of the loaded Bit's requirement for `slot`, or None
        (no such slot, or no Bit loaded). Exists so callers outside this
        module (the Console's role_view) can show a role's `requires`
        contract without reaching into the private `_slot_requirements`
        snapshot directly."""
        return self._slot_requirements.get(slot)

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
            requirements = tuple(bit.instrument_requirements())
            declared_slots = {r.slot for r in requirements}
            # A slot some Role.requires names (other than the reserved
            # "room" slot) is a role slot (spec section 4): it is resolved
            # only at join, against the JOINING DEVICE's carried instrument
            # (see join() below), never against the room's own fixtures --
            # a fixture has no gestures to offer. "room" itself stays a
            # room slot even when a Role happens to require it (deviation:
            # implicit-room-slot join handling, spec Status section).
            role_slots = {role.requires for role in role_table.roles.values()
                          if role.requires not in (None, "room")}
            if self.room is not None:
                light_m, ugen_m = bit.room_manifests()
                if light_m or ugen_m:
                    rname, role, node = room_role(self.room, ugen_manifest=ugen_m,
                                                  light_manifest=light_m)
                    role_table.roles[rname] = role
                    role_table.node_map[node] = [rname]
                room_reqs = [r for r in requirements if r.slot not in role_slots]
                if (light_m or ugen_m) and "room" not in declared_slots:
                    caps = set()
                    if light_m:
                        caps.add("light.surface")
                    if ugen_m:
                        caps.add("audio.flsyn")
                    room_reqs.append(InstrumentRequirement(
                        slot="room", capabilities=frozenset(caps)))
                _resolve_room_requirements(room_reqs, self.room)
            # "room" always counts as a declared slot for Role.requires,
            # whether resolved just above (an active Room) or not (a
            # roomless boot / a Bit with no Room manifests) -- this is a
            # static naming check, not a resolution check.
            known_slots = declared_slots | {"room"}
            for role in role_table.roles.values():
                if role.requires is not None and role.requires not in known_slots:
                    raise ValueError(
                        f"role {role.name!r} requires undeclared slot "
                        f"{role.requires!r}; declared: {sorted(known_slots)}")
            validate_role_declarations(role_table)
            function_table = bit.function_table
            validate_function_table(function_table, set(bit.verb_handlers()))
            registration = RegistrationState(role_table)
        except Exception as exc:
            self._set_state(State.IDLE)
            raise BitLoadError(f"failed to load Bit {name!r}: {exc}") from exc
        self.bit = bit
        self._warned_no_room = False
        self.bit_name = name
        self.registration = registration
        self._slot_requirements = {r.slot: r for r in requirements}
        # Built from the SAME table object validate_function_table just
        # checked, not a fresh read of the property -- function_table is a
        # property (see the class below), so a Bit that builds a new object
        # per access could otherwise hand this an unvalidated table, exactly
        # the hazard fire_function's own re-read already has to guard
        # against (see _FlipFunctionTableBit in tests/test_engine_functions.py).
        self._generators = GeneratorRunner(
            [f for f in function_table.functions.values()
             if f.kind is FunctionKind.GENERATOR])
        stream_functions: dict[str, list[Function]] = {}
        for f in function_table.functions.values():
            if f.kind is FunctionKind.STREAM:
                stream_functions.setdefault(f.stream.verb, []).append(f)
        self._stream_functions = stream_functions
        self._set_state(State.LOADED)
        self._enter_setup()

    def _enter_setup(self) -> None:
        self._set_state(State.SETUP)
        self.bit.on_setup_enter()

    def run(self) -> None:
        if self.state != State.SETUP:
            raise InvalidTransition(
                f"run requires SETUP, current state is {self.state}")
        self._run_elapsed = 0.0
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
            if role.requires is not None:
                # requires names a declared or implicit slot (Task 5's
                # load-time validation guarantees this). The implicit
                # "room" slot has no entry in _slot_requirements -- it's
                # deliberately excluded there because it binds the room's
                # own fixtures (already resolved at load_bit), not the
                # carrier device joining this role. req is None means
                # exactly that case, so treat it as satisfied.
                req = self._slot_requirements.get(role.requires)
                info = self.devices.get(dev)
                carried = getattr(info, "carried", None) or TUNESHROOM
                reason = satisfies(carried, req) if req is not None else None
                if req is not None and reason is not None:
                    self.registration.release(dev)
                    return JoinResult(granted=False, reason=reason)
                result.slot = role.requires
                result.instrument = carried.name
            result.config = compose_role_config(
                self.bit_name, self.bit.version, role,
                room_name=self.provenance.get("room_name"),
                terrarium_config_version=self.provenance.get(
                    "terrarium_config_version"),
                slot=result.slot, instrument=result.instrument)
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
        return self.room_binding.is_armed(self.room.name)

    def _bind_room(self, dev: str) -> None:
        fixture = None
        if self.room_binding is not None and self.room is not None:
            fixture = self.room_binding.armed_fixture(self.room.name)
        if fixture is not None:
            if self.room_binding is not None:
                self.room_binding.bind(self.room.name, fixture, dev)
            self.room.bound[fixture] = dev
        self._notify("on_devices_change")

    def clear_devices(self) -> None:
        """Drop every known device and notify observers. Called by
        control/terrarium.py's unload_room -- every device's clock died
        with the hub (design spec section 6), so the whole pool is stale,
        not just the ones bound to the departed Room."""
        self.devices.clear()
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
        streams = self._stream_functions.get(verb, ())
        if handler is None and not streams:
            return f"unknown verb {verb!r}"
        at = self._origin(gesture_time) + self._horizon
        stream_cue_list = self._collect_stream_cues(streams, dev, args)
        if handler is None:
            # Legal: a verb with declared streams and no Bit handler at all.
            self._dispatch_cues(stream_cue_list, at, FIRED_BY_GESTURE_VERB)
            return None
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
            # One gesture, one verdict: a refusal suppresses the stream
            # cues collected above too, so nothing from this gesture reaches
            # a device.
            return cues or "handler refused"
        self._dispatch_cues(list(stream_cue_list) + list(cues or ()), at,
                            FIRED_BY_GESTURE_VERB)
        return None

    def _collect_stream_cues(self, streams, dev: str, args: list) -> list[tuple]:
        """Mapped cues for every STREAM function on one verb whose domain
        contains this gesture's arg, in declaration order, first write wins
        per output lane -- the boundary rule for two domains that touch at a
        shared point (control/functions.py's stream_cues docstring)."""
        written_lanes: set[tuple[str, int, int]] = set()
        out: list[tuple] = []
        for fn in streams:
            spec = fn.stream
            x = stream_input(spec, args)
            if x is None or not (spec.in_lo <= x <= spec.in_hi):
                continue
            for cue, output in zip(stream_cues(fn, dev, args), spec.outputs):
                lane = (output.dev, output.status, output.data1)
                if lane in written_lanes:
                    continue
                written_lanes.add(lane)
                out.append(cue)
        return out

    def _canonical_room_dev(self) -> str | None:
        """The Room's one dev for MIDI-feed purposes: the first bound
        fixture in the profile's declaration order. Room light/audio is one
        shared session (design spec section 2), so every path that feeds it
        -- the ROOM cue sentinel and TARGET-fanout across Room fixtures --
        must resolve to exactly this one dev, never to whichever fixture
        happened to bind first or most recently."""
        if self.room is None or not self.room.bound:
            return None
        profile = self.room.profile
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
        """A function's declared target, resolved to the devs it lands on.

        Returns every bound Room fixture dev for ROOM, in declaration order
        -- this is the one-method change the N-fixture Room slice makes; no
        Bit's function declaration changes alongside it (design spec section
        5). This full list is what FunctionFired.devs reports; a script's
        TARGET fanout is collapsed separately, see _collapse_room_fanout.
        """
        if target is FunctionTarget.DEVICE:
            return [dev] if dev else []
        if target is FunctionTarget.SURFACE and dev != ROOM:
            return [dev] if dev else []
        room_devs: list[str] = []
        if self.room is not None and self.room.bound:
            profile = self.room.profile
            room_devs = [self.room.bound[f.name] for f in profile.fixtures
                        if f.name in self.room.bound]
        if target in (FunctionTarget.ROOM, FunctionTarget.SURFACE):
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
        `devs` (control/functions.py's expand_script), one independent cue
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

    def _check_cue_kinds(self, cues) -> str | None:
        """Refuse the whole fire, all-or-nothing (spec section 7), if any
        expanded cue's kind is not in its destination's instrument's
        accepted_cues.

        A device dev is checked against DeviceInfo.carried (TUNESHROOM by
        default). A dev the Room owns is checked against every Room fixture's
        instrument -- the Room is one logical surface, so any fixture
        accepting the kind is enough (the canonical fixture is named in the
        refusal when none do). An unknown dev (no pool entry, e.g. the Room
        simulator path) is treated as accepting, matching today's behavior:
        this gate must never invent a refusal for a dev nothing declared an
        instrument for."""
        room_devs = (set(self.room.bound.values())
                     if self.room is not None and self.room.bound else set())
        for cue in cues:
            resolved = self._resolve_dev(cue.dev)
            if resolved is None:
                continue
            kind = cue_kind(cue)
            if resolved in room_devs:
                fixtures = self.room.profile.fixtures
                if any(kind in f.instrument.accepted_cues
                       for f in fixtures):
                    continue
                return (f"instrument {fixtures[0].instrument.name!r} does "
                        f"not accept {kind!r} cues")
            info = self.devices.get(resolved)
            if info is None:
                continue
            carried = getattr(info, "carried", None) or TUNESHROOM
            if kind not in carried.accepted_cues:
                return (f"instrument {carried.name!r} does not accept "
                        f"{kind!r} cues")
        return None

    def fire_function(self, name: str, *, fired_by: str,
                     dev: str | None = None,
                     at: float | None = None) -> str | None:
        """Fire one declared function: expand its script, dispatch it, and tell
        every observer it happened.

        Returns None when fired, else a refusal reason, and NEVER raises, for
        the same reason data() does not: neither a device nor a browser may be
        able to wedge Control.

        `fired_by` is what actually fired it THIS time, which is deliberately
        not the same field as the condition's declared source: an operator may
        fire a gesture-verb function by hand, and the record has to keep those
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
            table = self.bit.function_table
        except Exception:
            logger.exception("Bit.function_table raised; refusing to fire %r",
                             name)
            return "function table error"
        try:
            function_decl = table.functions.get(name)
            if function_decl is None:
                return f"unknown function {name!r}"
            if function_decl.kind is not FunctionKind.SCRIPTED:
                return f"function {name!r} is not scripted"
            if function_decl.target in (FunctionTarget.DEVICE,
                                  FunctionTarget.SURFACE) and not dev:
                if function_decl.target is FunctionTarget.DEVICE:
                    return (f"function {name!r} targets the firing device; "
                            f"no device given")
                return (f"function {name!r} targets a surface; "
                        f"no surface given")
            if at is None:
                at = self._clock() + self._horizon
            devs = self._resolve_target(function_decl.target, dev)
            cues = expand_script(function_decl, at, self._collapse_room_fanout(devs))
            refusal = self._check_cue_kinds(cues)
            if refusal is not None:
                return refusal
            if not any(isinstance(c, MuteCue) for c in cues):
                self._clear_mutes(devs)
        except Exception:
            # function_table is a property: load_bit validated whatever it
            # returned on THAT one call, and the validated object is never
            # retained (the same hazard RegistrationState's role_table
            # snapshot exists to close for role_table). A later access can
            # return something else, so everything this function touches
            # between lookup and expansion is guarded here, not just the
            # property access above.
            logger.exception("function %r script expansion failed; refusing "
                             "to fire", name)
            return "function script error"
        # No fired_by passed on: expand_script only ever yields LightCue and
        # PlayCue, never FireFunction, so this cannot recurse and a function
        # cannot chain into another. The guard above already contains
        # anything a divergent function_table could throw while producing
        # `cues`, so this call sees only well-formed cues.
        if self._generators is not None:
            self._suppress_generator_lanes(function_decl, cues, at)
        self._dispatch_cues(cues, at)
        self._notify("on_function_fired", FunctionFired(
            name=function_decl.name,
            condition=function_decl.condition.name,
            fired_by=fired_by,
            declared_source=SOURCE_WIRE[function_decl.condition.source],
            dev=dev,
            devs=tuple(devs),
            at=at,
            steps=len(cues),
            room_name=self.provenance.get("room_name"),
        ))
        return None

    def _suppress_generator_lanes(self, function_decl, cues, at: float) -> None:
        """After a scripted fire's cues are expanded, overlay-suppress any
        generator lane the script itself writes, until at + span (span =
        the script's last step offset). Never kills the generator -- its
        phase keeps advancing underneath (spec section 4)."""
        if not function_decl.script:
            return
        span = float(function_decl.script[-1].offset)
        canonical_room = self._canonical_room_dev()
        lanes = set()
        for cue in cues:
            if isinstance(cue, LightCue):
                dev, status, data1 = cue.dev, cue.status, cue.data1
            elif isinstance(cue, tuple) and len(cue) == 4:
                dev, status, data1, _ = cue
            else:
                continue
            # A GENERATOR's own dev is always the ROOM sentinel or the
            # unresolved TARGET sentinel (control/functions.py's
            # _LEGAL_GENERATOR_DEVS) -- it never names a concrete device.
            # A script step written literally as cues.ROOM stays that
            # sentinel through expand_script and matches directly; a step
            # written as cues.TARGET at a Room-targeting Function has
            # already fanned out to the Room's actual canonical dev by the
            # time it reaches here (_collapse_room_fanout), so it is folded
            # back to the same sentinel for lane comparison -- otherwise a
            # TARGET-authored script could never overlay a ROOM generator on
            # the shared lane it visibly writes.
            if canonical_room is not None and dev == canonical_room:
                dev = ROOM
            lanes.add((dev, status, data1))
        if lanes:
            self._generators.suppress(lanes, at + span)

    def _clear_mutes(self, devs) -> None:
        """Any non-mute fire at a surface un-latches it (spec section 4)."""
        cleared_any = False
        for d in devs:
            if d in self.muted:
                self.muted.discard(d)
                cleared_any = True
                if self.on_mute_change is not None:
                    try:
                        self.on_mute_change(d, False)
                    except Exception:
                        logger.exception("on_mute_change failed for %s", d)
        if cleared_any:
            self._notify("on_devices_change")

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
                if isinstance(cue, FireFunction):
                    # A Bit reporting one of its own conditions satisfied.
                    # fire_function re-enters this method with the expanded
                    # script, carrying the same `at`, so a function fired from a
                    # gesture lands on the same frame as the ordinary cues
                    # returned beside it.
                    reason = self.fire_function(
                        cue.name,
                        fired_by=fired_by or FIRED_BY_BIT_ADJUDICATED,
                        dev=cue.dev,
                        at=(cue.at if cue.at is not None else at))
                    if reason is not None:
                        logger.warning("Bit fired function %r: %s",
                                       cue.name, reason)
                    continue
                if isinstance(cue, SolidCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    when = at if cue.when is None else cue.when
                    sink, args = self.on_solid_cue, (dev, cue.rgb, cue.level,
                                                     cue.duration, when)
                elif isinstance(cue, MuteCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    self.muted.add(dev)
                    self._notify("on_devices_change")
                    sink, args = self.on_mute_change, (dev, True)
                elif isinstance(cue, PlayCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    if dev in self.muted:
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
        self._run_elapsed += dt
        if self.bit.update(dt):
            self._complete()
            return
        self._dispatch_generator_cues()
        self._dispatch_bit_fires()

    def _dispatch_generator_cues(self) -> None:
        """Evaluate the loaded Bit's declared GENERATOR functions once per
        RUNNING tick, in elapsed-run time, and dispatch their non-suppressed
        lanes exactly where _dispatch_bit_cues used to drain Bit.cues()."""
        if self._generators is None:
            return
        at = self._clock() + self._horizon
        cues = self._generators.cues(self._run_elapsed, at)
        self._dispatch_cues(cues, at, FIRED_BY_BIT_ADJUDICATED)

    def _dispatch_bit_fires(self) -> None:
        """Drain Bit.fires() once per RUNNING tick. A self-reported fire has
        no gesture behind it, so its origin is Control's own clock.

        Guarded exactly like _dispatch_bit_cues was: a raising fires() must
        not stop this Bit reaching COMPLETING. Anything other than a
        FireFunction is logged and dropped -- fires() may only report fires,
        never drive a lane directly (that is what generators are for).
        """
        at = self._clock() + self._horizon
        try:
            fires = self.bit.fires(at)
        except Exception:
            logger.exception("Bit.fires raised; ignoring this tick")
            return
        clean = []
        for item in fires or ():
            if isinstance(item, FireFunction):
                clean.append(item)
            else:
                logger.warning(
                    "Bit.fires returned a non-FireFunction %r; dropping",
                    item)
        self._dispatch_cues(clean, at, FIRED_BY_BIT_ADJUDICATED)

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
        self._clear_mutes(list(self.muted))
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
        self._slot_requirements = {}
        self._generators = None
        self._stream_functions = {}
        self._run_elapsed = 0.0
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
