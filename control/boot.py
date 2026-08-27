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
from control.rooms import Room
from control.teardown import TeardownStack
from control.terrarium_config import RoomSpec

logger = logging.getLogger(__name__)


class BootFailure(Exception):
    """Wraps any load-sequence failure. No partial/silent-downgrade success
    -- every failure tears down whatever was already started."""


def boot(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, room_spec: RoomSpec,
         arco_process_cls=ArcoProcess,
         simulator_factory=None, known_device_connected=lambda dev: False,
         tick=None, teardown=None, clock=time.monotonic):
    """Compat wrapper over control.terrarium.Terrarium: one-shot load_room
    + load_bit, returning the old 4-tuple. Deleted in Task 7, once
    harness/terrarium_boot.py drives Terrarium directly instead.

    `teardown`, if given, is ignored for registration purposes (Terrarium
    owns its own room-scoped stack); it is accepted only so existing
    callers that pass one don't break -- see harness/terrarium_boot.py.

    Raises BootFailure on any stage failure, exactly as before: load_room
    refusals, an unknown/unsupported Bit, or a Bit load error.
    """
    # Local imports: control.terrarium imports this module, so importing it
    # at module level here would be circular.
    from control.terrarium import Terrarium
    from control.terrarium_config import TerrariumConfig

    terrarium_config = TerrariumConfig(
        schema=1, name="boot-compat", bit_paths=(),
        rooms={room_spec.name: room_spec}, version="boot-compat")

    stack_factory = (lambda: teardown) if teardown is not None else TeardownStack

    terrarium = Terrarium(
        terrarium_config, GameServer(bit_registry, room_binding=room_binding,
                                     cue_horizon=config.cue_horizon,
                                     clock=clock),
        room_binding, boot_config=config, arco_command=arco_command,
        arco_process_cls=arco_process_cls, simulator_factory=simulator_factory,
        known_device_connected=known_device_connected, tick=tick,
        stack_factory=stack_factory)

    reason = terrarium.load_room(room_spec.name)
    if reason is not None:
        raise BootFailure(reason)

    gs = terrarium.gs
    try:
        bit_cls = bit_registry.get(config.bit_name)
        if bit_cls is None:
            raise BootFailure(f"unknown Bit {config.bit_name!r}")
        if terrarium.room.name not in bit_cls.room_types:
            raise BootFailure(
                f"Bit {config.bit_name!r} does not support {terrarium.room.name}")
        try:
            gs.load_bit(config.bit_name, config=config.bit_config)
        except BitLoadError as exc:
            raise BootFailure(f"Bit load failed: {exc}") from exc
    except BaseException:
        # Mirrors the old boot()'s outer handler: once Arco/the Room are up,
        # ANY failure here (a bad Bit name, a Bit load error, a
        # KeyboardInterrupt raised while constructing the Bit -- see
        # control/engine.py's load_bit, whose own `except Exception` does
        # not catch KeyboardInterrupt) must unwind the room stack before
        # propagating.
        terrarium.room_stack.close()
        gs.room = None
        raise

    terrarium.room_stack.push("bit", lambda: _abort_if_running(gs))

    return gs, terrarium.room_bridge, terrarium.arco, terrarium.room_stack


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
    profile = room.profile
    for fixture in profile.fixtures:
        if simulator_factory is not None:
            dev = simulator_factory(teardown, fixture.name)
            room.bound[fixture.name] = dev
            room_binding.bind(room.name, fixture.name, dev)
            continue
        recorded = room_binding.bound_device(room.name, fixture.name)
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
    profile = gs.room.profile
    if gs.room.fully_bound(profile):
        return
    deadline = clock() + timeout
    for fixture in profile.fixtures:
        if fixture.name in gs.room.bound:
            continue
        remaining = deadline - clock()
        if remaining <= 0:
            break
        room_binding.arm(gs.room.name, fixture.name, remaining)
        while clock() < deadline and fixture.name not in gs.room.bound:
            tick()
            sleep(0.05)
        room_binding.disarm(gs.room.name)
    if not gs.room.bound:
        raise RoomBindingTimeout(
            f"no device joined as {gs.room.name} Room within {timeout}s")
    missing = [f.name for f in profile.fixtures if f.name not in gs.room.bound]
    if missing:
        logger.warning("Room %s partially bound; missing fixtures: %s",
                       gs.room.name, missing)
