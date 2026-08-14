"""Orchestrates the Terrarium load sequence: config -> Arco -> Room
resolution -> Room binding -> Bit load. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5. Backend-agnostic: simulator creation is an injected factory --
Spec 2 (the Terrarium Visualization Simulator) supplies a real one; a real
hardware harness supplies known_device_connected against actual DevicePool
state.
"""

from __future__ import annotations

import time

from control.arco_process import ArcoProcess
from control.boot_config import BootConfig
from control.engine import BitLoadError, GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import RoomBridge
from control.rooms import Room, RoomResolutionError, resolve_room_type
from control.teardown import TeardownStack


class BootFailure(Exception):
    """Wraps any load-sequence failure. No partial/silent-downgrade success
    -- every failure tears down whatever was already started."""


def boot(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, arco_process_cls=ArcoProcess,
         simulator_factory=None, known_device_connected=lambda dev: False,
         tick=None, teardown=None):
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

    `simulator_factory` is Callable[[TeardownStack], str]: a factory that
    spawns a process registers its own teardown on the stack it is handed.

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

        gs = GameServer(bit_registry, room_binding=room_binding)
        gs.room = room
        try:
            gs.load_bit(config.bit_name)
        except BitLoadError as exc:
            raise BootFailure(f"Bit load failed: {exc}") from exc

        if room.bound_dev is None:
            try:
                wait_for_room_binding(
                    gs, room_binding, config.room_setup_timeout,
                    tick=tick or (lambda: gs.tick(0.05)))
            except RoomBindingTimeout as exc:
                gs.abort()
                raise BootFailure(str(exc)) from exc

        room_bridge = RoomBridge()
        if room.bound_dev is not None:
            room_bridge.bind(room.bound_dev)
        teardown.push("room-bridge", lambda: room_bridge.shutdown())
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


def _bind_room_fast_path(room: Room, room_binding: RoomBindingRegistry,
                         simulator_factory, known_device_connected,
                         teardown) -> None:
    """Attempt the no-tap-needed path: a Terrarium-spawned simulator, or a
    reconnect to a previously recorded physical device. Leaves the Room
    unbound (room.bound_dev stays None) if neither applies -- wait_for_room_
    binding below is what holds for a fresh admin-armed tap, not this
    function's job.

    The factory is handed the teardown stack and registers whatever it
    spawns, so an orphaned Room simulator is impossible by construction
    rather than by a getattr convention. An orphan matters: it never exits
    on its own, reconnects to the NEXT Arco and re-claims its dev name
    there, so that run's own simulator is refused by O2
    (o2/src/bridge.cpp:231-237) and renders nothing, silently.
    """
    if simulator_factory is not None:
        dev = simulator_factory(teardown)
        room.bound_dev = dev
        room_binding.bind(room.room_type, dev)
        return
    recorded = room_binding.bound_device(room.room_type)
    if recorded is not None and known_device_connected(recorded):
        room.bound_dev = recorded


class RoomBindingTimeout(Exception):
    """Raised when no device joins as the Room within the configured
    setup window."""


def wait_for_room_binding(gs: GameServer, room_binding: RoomBindingRegistry,
                          timeout: float, *, tick, clock=time.monotonic,
                          sleep=time.sleep) -> None:
    """Hold until the Room is bound (a fresh admin-armed tap grants the
    ROOM-class role) or timeout elapses. `tick` is called once per
    iteration -- driving whatever transport/tick loop might deliver that
    join -- so this function has no transport opinion of its own. Mirrors
    harness/devicelink_smoke.py's _wait_in_setup poll-loop shape."""
    if gs.room.bound_dev is not None:
        return
    room_binding.arm(gs.room.room_type, timeout)
    deadline = clock() + timeout
    while clock() < deadline:
        tick()
        if gs.room.bound_dev is not None:
            room_binding.disarm(gs.room.room_type)
            return
        sleep(0.05)
    room_binding.disarm(gs.room.room_type)
    raise RoomBindingTimeout(
        f"no device joined as {gs.room.room_type.name} Room within {timeout}s")
