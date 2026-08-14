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


class BootFailure(Exception):
    """Wraps any load-sequence failure. No partial/silent-downgrade success
    -- every failure tears down whatever was already started."""


def boot(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, arco_process_cls=ArcoProcess,
         simulator_factory=None, known_device_connected=lambda dev: False,
         tick=None):
    """Run the full load sequence. Returns (game_server, room_bridge,
    arco_process) once the Bit is loaded and either the Room is already
    bound (fast path) or a fresh tap has bound it (see wait_for_room_binding
    below).

    Raises BootFailure on any stage failure. Once Arco has actually
    started, EVERY failure -- wait_ready timing out, an unknown/unsupported
    Bit, a Bit load error, a Ctrl-C, or anything unanticipated -- shuts
    down both Arco AND any simulator subprocess the factory spawned,
    before propagating. That's a structural guarantee (one try/except
    around the whole post-start section) rather than a shutdown call
    enumerated at each failure site, so a future failure mode added to
    this section can't accidentally orphan either subprocess by forgetting
    one."""
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

    try:
        try:
            arco.wait_ready(config.arco_ready_timeout)
        except Exception as exc:
            raise BootFailure(f"Arco failed to start: {exc}") from exc

        _bind_room_fast_path(room, room_binding, simulator_factory,
                             known_device_connected)

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
    except BaseException:
        # Arco is a live subprocess by this point, and _bind_room_fast_path
        # may have spawned a simulator subprocess too -- any failure below
        # here must orphan neither. An orphaned Room simulator never exits
        # on its own, reconnects to the NEXT Arco and re-claims its dev
        # name there, so that run's own simulator is refused by O2
        # (o2/src/bridge.cpp:231-237) and renders nothing, silently. See
        # docs/superpowers/specs/
        # 2026-08-14-room-simulator-service-collision-design.md.
        #
        # BaseException, not Exception: a Ctrl-C during boot used to leak
        # both subprocesses, since KeyboardInterrupt is not an Exception.
        # Re-raise unchanged: the inner handlers above already produced a
        # well-labeled BootFailure for every stage.
        _shutdown_simulator(simulator_factory)
        arco.shutdown()
        raise

    return gs, room_bridge, arco


def _bind_room_fast_path(room: Room, room_binding: RoomBindingRegistry,
                         simulator_factory, known_device_connected) -> None:
    """Attempt the no-tap-needed path: a Terrarium-spawned simulator, or a
    reconnect to a previously recorded physical device. Leaves the Room
    unbound (room.bound_dev stays None) if neither applies -- wait_for_room_
    binding below is what holds for a fresh admin-armed tap, not this
    function's job."""
    if simulator_factory is not None:
        dev = simulator_factory()
        room.bound_dev = dev
        room_binding.bind(room.room_type, dev)
        return
    recorded = room_binding.bound_device(room.room_type)
    if recorded is not None and known_device_connected(recorded):
        room.bound_dev = recorded


def _shutdown_simulator(simulator_factory) -> None:
    """Shut down a simulator subprocess the factory spawned, if any.

    A simulator_factory is a bare Callable[[], str] by contract, but one
    that SPAWNS a process exposes the handle as `.process` with a
    shutdown() -- harness/terrarium_boot.py's factories already do exactly
    that, and its build() already reads the attribute back off the same
    object. A factory that spawns nothing has no such attribute and this
    is a no-op.

    Swallows a failing shutdown deliberately: this runs on the way out of
    a boot that is already failing, and neither masking that failure nor
    skipping arco.shutdown() below it is acceptable.
    """
    process = getattr(simulator_factory, "process", None)
    if process is None:
        return
    try:
        process.shutdown()
    except Exception:
        pass


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


def shutdown(gs: GameServer, room_bridge: RoomBridge, arco: ArcoProcess) -> None:
    """Tear down in the order design spec section 5 step 9 requires: the
    running Bit first (mirroring AudioBridge.shutdown()'s "free everything
    before the pool goes away"), then the Room bridge, then Arco last since
    everything else may still want to address it during teardown."""
    from control.state import State
    if gs.state != State.IDLE:
        gs.abort()
    room_bridge.shutdown()
    arco.shutdown()
