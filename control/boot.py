"""Orchestrates the Terrarium load sequence: config -> Arco -> Room
resolution -> Room binding -> Bit load. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5. Backend-agnostic: simulator creation is an injected factory --
Spec 2 (the Terrarium Visualization Simulator) supplies a real one; a real
hardware harness supplies known_device_connected against actual DevicePool
state.
"""

from __future__ import annotations

import logging
import time

from control.arco_process import ArcoProcess
from control.boot_config import BootConfig
from control.engine import BitLoadError, GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import RoomBridge
from control.room_profile import room_profile
from control.rooms import Room, RoomResolutionError, resolve_room_type
from control.teardown import TeardownStack

logger = logging.getLogger(__name__)


class BootFailure(Exception):
    """Wraps any load-sequence failure. No partial/silent-downgrade success
    -- every failure tears down whatever was already started."""


def boot(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, arco_process_cls=ArcoProcess,
         simulator_factory=None, known_device_connected=lambda dev: False,
         tick=None, teardown=None, clock=time.monotonic):
    """Run the full load sequence. Returns (game_server, room_bridge,
    arco_process, teardown) once the Bit is loaded and either the Room is
    already bound (fast path) or a fresh tap has bound it.

    TEARDOWN IS THE RETURNED STACK. There is no boot.shutdown() any more:
    the caller closes the stack, and every step this function registered
    unwinds in reverse. That deleted function's docstring used to say "Arco
    last since everything else may still want to address it during
    teardown", which was true within this module's scope and wrong composed
    with harness/terrarium_boot.py, which owns o2lite CLIENT subprocesses
    that talk to that hub. Reverse-of-registration gets it right in both
    scopes without either having to know about the other.

    Push order here is deliberate, and unwinds as: the Bit, then the Room
    bridge (which frees the Room's Arco voice), then any simulator
    subprocess, then Arco. The Bit goes before the bridge because its
    on_unload may still cue into it.

    `teardown` lets a caller that started something BEFORE boot() register
    it first and have it torn down last. harness/terrarium_boot.py starts
    its DeviceLinkServer before calling boot(), deliberately, because the
    simulator this function spawns connects immediately.

    `simulator_factory` is Callable[[TeardownStack, str], str]: (teardown,
    fixture_name) -> dev, called once per fixture. A factory that spawns a
    process registers its own teardown on the stack it is handed.

    `clock` is threaded into GameServer and must be the same callable the
    caller hands DeviceLinkAgent (harness/terrarium_boot.py's build() does
    exactly that). On the o2lite transport it is o2lite.time_get.

    Raises BootFailure on any stage failure. Once Arco has actually
    started, EVERY failure -- wait_ready timing out, an unknown or
    unsupported Bit, a Bit load error, a Ctrl-C, or anything unanticipated
    -- closes the stack before propagating, so nothing this function
    started is orphaned and no cleanup exception masks the real one.
    """
    if teardown is None:
        teardown = TeardownStack()

    try:
        room_type = resolve_room_type(
            config.room_type,
            array_backend_configured=config.array_backend_configured)
    except RoomResolutionError as exc:
        raise BootFailure(str(exc)) from exc
    room = Room(room_type=room_type)

    arco = arco_process_cls(arco_command)
    try:
        arco.start()
    except Exception as exc:
        # Nothing was actually spawned, so there's nothing to shut down.
        raise BootFailure(f"Arco failed to start: {exc}") from exc
    teardown.push("arco", arco.shutdown)

    try:
        try:
            arco.wait_ready(config.arco_ready_timeout)
        except Exception as exc:
            raise BootFailure(f"Arco failed to start: {exc}") from exc

        _bind_room_fast_path(room, room_binding, simulator_factory,
                             known_device_connected, teardown)

        bit_cls = bit_registry.get(config.bit_name)
        if bit_cls is None:
            raise BootFailure(f"unknown Bit {config.bit_name!r}")
        if room.room_type not in bit_cls.room_types:
            raise BootFailure(
                f"Bit {config.bit_name!r} does not support {room.room_type.name}")

        # cue_horizon and clock go in together and MUST match the ones
        # DeviceLinkAgent is built with: GameServer computes every cue's
        # target time (origin + horizon) and reads this clock for a
        # self-driven cue's origin and for the no-stamp fallback. Two clock
        # bases is the 2026-08-13 live-run bug.
        gs = GameServer(bit_registry, room_binding=room_binding,
                        cue_horizon=config.cue_horizon, clock=clock)
        gs.room = room
        try:
            gs.load_bit(config.bit_name, config=config.bit_config)
        except BitLoadError as exc:
            raise BootFailure(f"Bit load failed: {exc}") from exc

        profile_for_wait = None
        try:
            profile_for_wait = room_profile(room.room_type)
        except NotImplementedError:
            pass
        if profile_for_wait is not None and not room.fully_bound(profile_for_wait):
            try:
                wait_for_room_binding(
                    gs, room_binding, config.room_setup_timeout,
                    tick=tick or (lambda: gs.tick(0.05)))
            except RoomBindingTimeout as exc:
                gs.abort()
                raise BootFailure(str(exc)) from exc

        room_bridge = RoomBridge()
        canonical = (_canonical_room_dev(profile_for_wait, room.bound)
                    if profile_for_wait is not None else None)
        if canonical is not None:
            room_bridge.bind(canonical)
        teardown.push("room-bridge", room_bridge.shutdown)
        teardown.push("bit", lambda: _abort_if_running(gs))
    except BaseException:
        # Arco is a live subprocess by this point, and _bind_room_fast_path
        # may have spawned a simulator subprocess too. Closing the stack
        # unwinds whatever got as far as being registered, in the right
        # order, with each step guarded so cleanup cannot mask this
        # failure. Re-raise unchanged: the inner handlers above already
        # produced a well-labeled BootFailure for every stage.
        teardown.close()
        raise

    return gs, room_bridge, arco, teardown


def _abort_if_running(gs) -> None:
    """The Bit teardown step. Guarded on state because the driver may have
    already run the Bit to completion, and abort() on an IDLE server is not
    meaningful."""
    from control.state import State
    if gs.state != State.IDLE:
        gs.abort()


def _canonical_room_dev(profile, bound: dict) -> str | None:
    """The Room's one dev for RoomBridge purposes: the first bound fixture
    in the profile's declaration order, not dict-insertion order -- the
    same algorithm as GameServer._canonical_room_dev and
    DeviceLinkAgent._canonical_room_dev. Extracted as its own function
    specifically so this guarantee is unit-testable directly, without
    needing to drive a full boot() through admin-tap timing to construct
    a bound dict whose insertion order differs from declaration order."""
    for fixture in profile.fixtures:
        dev = bound.get(fixture.name)
        if dev is not None:
            return dev
    return None


def _bind_room_fast_path(room: Room, room_binding: RoomBindingRegistry,
                         simulator_factory, known_device_connected,
                         teardown) -> None:
    """Attempt the no-tap-needed path per fixture: a Terrarium-spawned
    simulator, or a reconnect to a previously recorded physical device.
    Leaves any fixture unbound (absent from room.bound) if neither applies
    -- wait_for_room_binding below is what holds for a fresh admin-armed
    tap, not this function's job.

    The factory is handed the teardown stack and the fixture name, and
    registers whatever it spawns, so an orphaned Room simulator is
    impossible by construction rather than by a getattr convention. Called
    once per fixture -- each fixture is its own o2lite client with its own
    unique service name (design spec section 3).
    """
    try:
        profile = room_profile(room.room_type)
    except NotImplementedError:
        return   # no fixture declaration for this RoomType yet (e.g. DEMO)
    for fixture in profile.fixtures:
        if simulator_factory is not None:
            dev = simulator_factory(teardown, fixture.name)
            room.bound[fixture.name] = dev
            room_binding.bind(room.room_type, fixture.name, dev)
            continue
        recorded = room_binding.bound_device(room.room_type, fixture.name)
        if recorded is not None and known_device_connected(recorded):
            room.bound[fixture.name] = recorded


class RoomBindingTimeout(Exception):
    """Raised when no device joins as the Room within the configured
    setup window."""


def wait_for_room_binding(gs: GameServer, room_binding: RoomBindingRegistry,
                          timeout: float, *, tick, clock=time.monotonic,
                          sleep=time.sleep) -> None:
    """Hold until every fixture is bound (each admin-armed tap grants one
    fixture's ROOM-class join) or the shared timeout budget elapses,
    arming fixtures one at a time in the profile's declaration order.
    `tick` is called once per iteration -- driving whatever transport/tick
    loop might deliver that join -- so this function has no transport
    opinion of its own.

    Raises RoomBindingTimeout only when NO fixture ever binds. A Room that
    is SOME but not all fixtures bound after the timeout proceeds anyway --
    see design spec section 7: one unresponsive fixture must not fail the
    whole boot.
    """
    profile = room_profile(gs.room.room_type)
    if gs.room.fully_bound(profile):
        return
    deadline = clock() + timeout
    for fixture in profile.fixtures:
        if fixture.name in gs.room.bound:
            continue
        remaining = deadline - clock()
        if remaining <= 0:
            break
        room_binding.arm(gs.room.room_type, fixture.name, remaining)
        while clock() < deadline and fixture.name not in gs.room.bound:
            tick()
            sleep(0.05)
        room_binding.disarm(gs.room.room_type)
    if not gs.room.bound:
        raise RoomBindingTimeout(
            f"no device joined as {gs.room.room_type.name} Room within {timeout}s")
    missing = [f.name for f in profile.fixtures if f.name not in gs.room.bound]
    if missing:
        logger.warning("Room %s partially bound; missing fixtures: %s",
                       gs.room.room_type.name, missing)
