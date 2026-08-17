"""ConsoleAgent: translates between the console wire protocol and GameServer
calls, and pushes live state to connected browsers. The local, inbound
sibling of uplink.UplinkAgent -- transport-agnostic (it talks to a server
object, see console/server.py), so it is fully testable offline against an
in-process fake. Driven from the engine tick loop via poll().
"""

import logging
import time

from console import protocol
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.roles import RoleClass
from control.room_profile import room_profile
from control.room_view import room_view
from control.rooms import RoomType, non_room_counts, room_role_name
from control.state import State

logger = logging.getLogger(__name__)

# How often a Room frame may be broadcast. The Room renders at 44 Hz; the
# Console is a monitor, so it gets roughly 10 Hz and intermediate frames are
# DROPPED rather than queued. Boundary rule 2: nothing here may become
# something gameplay waits on.
ROOM_FRAME_INTERVAL = 0.1


class ConsoleAgent:
    def __init__(self, game_server: GameServer, server, room_bridge=None,
                 clock=time.monotonic):
        self.game_server = game_server
        self.server = server
        # The Room's live MIDI fan-out, for its controllers read-out. Optional:
        # a GameServer built the pre-Room way has none, and the panel then
        # shows the Room's declarations with no live values rather than
        # failing.
        self._room_bridge = room_bridge
        self._last_status: dict | None = None
        self._last_room: dict | None = None
        self._clock = clock
        # The latest Room frame not yet broadcast, or None. Overwritten, not
        # queued: see _broadcast_room_frame and ROOM_FRAME_INTERVAL above.
        self._pending_room_frame: tuple[str, bytes] | None = None
        self._last_room_frame_at = 0.0
        game_server.add_observer(self)

    # --- driven once per tick-loop iteration -------------------------------
    def poll(self) -> None:
        for client in self.server.drain_new_clients():
            self.server.send(client, self.snapshot())
        for client, msg in self.server.drain_inbound():
            error = self._handle_command(msg)
            if error is not None:
                self.server.send(client, error)
        self._broadcast_status_if_changed()
        self._broadcast_room_if_changed()
        self._broadcast_room_frame()

    # --- inbound command dispatch ------------------------------------------
    def _handle_command(self, msg: dict) -> dict | None:
        name = msg.get("command")
        if name in ("arm_room", "release_room"):
            return self._handle_admin_command(msg)
        try:
            command = protocol.parse_command(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable console message: %s", exc)
            return None
        try:
            if isinstance(command, protocol.LoadBitCommand):
                self.game_server.load_bit(command.name)
            elif isinstance(command, protocol.RunCommand):
                self.game_server.run()
            elif isinstance(command, protocol.AbortCommand):
                self.game_server.abort()
        except (InvalidTransition, BitLoadError) as exc:
            return protocol.error_event(name, str(exc))
        return None

    def _handle_admin_command(self, msg: dict) -> dict | None:
        name = msg.get("command")
        try:
            command = protocol.parse_admin_command(msg)
        except ValueError as exc:
            return protocol.error_event(name, str(exc))
        try:
            room_type = RoomType[command.room_type]
        except KeyError:
            return protocol.error_event(
                name, f"unknown room_type {command.room_type!r}")
        gs = self.game_server
        if gs.room_binding is None or gs.room is None or gs.room.room_type != room_type:
            return protocol.error_event(
                name, f"no {command.room_type} Room configured")
        if isinstance(command, protocol.ArmRoomCommand):
            gs.room_binding.arm(room_type, command.window_seconds)
        elif isinstance(command, protocol.ReleaseRoomCommand):
            gs.room_binding.release(room_type)
        return None

    # --- snapshot (connect-time full read model) ---------------------------
    def snapshot(self) -> dict:
        gs = self.game_server
        loaded_bit = None
        roles: list = []
        registration: list = []
        if gs.registration is not None:
            loaded_bit = self._loaded_bit_name()
            roles = [protocol.role_view(r)
                     for r in gs.registration.role_table.roles.values()
                     if r.role_class != RoleClass.ROOM]
            registration = protocol.registration_changed_event(
                self._non_room_counts())["roles"]
        self._last_room = self._current_room()
        return protocol.snapshot_event(
            state=gs.state.name,
            installed_bits=list(gs.bit_registry.keys()),
            loaded_bit=loaded_bit,
            roles=roles,
            registration=registration,
            devices=self._devices_view(),
            bit_status=self._current_status(),
            room=self._last_room,
        )

    def _current_room(self) -> dict | None:
        """Build the Room panel payload, or None when no Room is configured.

        Deliberately scoped: see control/room_view.py's module docstring. The
        Room-hiding filters this class already applies to `roles` and
        `registration` are NOT relaxed by this method; it is a separate view.
        """
        gs = self.game_server
        if gs.room is None:
            return None
        try:
            profile = room_profile(gs.room.room_type)
        except NotImplementedError:
            logger.warning("no room profile for %s; Room panel disabled",
                           gs.room.room_type.name)
            return None
        role = None
        if gs.bit is not None:
            role = gs.bit.role_table.roles.get(room_role_name(gs.room.room_type))
        controllers = getattr(self._room_bridge, "controllers", {}) or {}
        return room_view(gs.room, profile, role, controllers)

    def _broadcast_room_if_changed(self) -> None:
        room = self._current_room()
        if room != self._last_room:
            self._last_room = room
            self.server.broadcast(protocol.room_changed_event(room))

    def on_room_frame(self, dev: str, frame: bytes) -> None:
        """DeviceLinkAgent's display-only frame sink. Called on the tick
        thread. Stores the LATEST frame only; anything not yet broadcast is
        overwritten, never queued."""
        self._pending_room_frame = (dev, frame)

    def _broadcast_room_frame(self) -> None:
        if self._pending_room_frame is None:
            return
        now = self._clock()
        if now - self._last_room_frame_at < ROOM_FRAME_INTERVAL:
            return
        dev, frame = self._pending_room_frame
        self._pending_room_frame = None
        self._last_room_frame_at = now
        self.server.broadcast(protocol.room_frame_event(dev, frame))

    def _non_room_counts(self):
        """Never surface the Room's occupancy on any Console view -- design
        spec section 7. Thin wrapper around the shared filter in
        control/rooms.py, also used by uplink/link.py."""
        return non_room_counts(self.game_server.registration)

    def _loaded_bit_name(self) -> str | None:
        return self.game_server.bit_name

    def _devices_view(self) -> list:
        gs = self.game_server
        assignments = gs.registration.assignments if gs.registration else {}
        out = []
        for info in gs.devices.all():
            assigned = assignments.get(info.dev)
            role_name = None
            if assigned is not None and assigned[2] != RoleClass.ROOM:
                role_name = assigned[1]
            out.append(protocol.device_view(info, role_name))
        return out

    def _current_status(self) -> dict:
        bit = self.game_server.bit
        if bit is None:
            return {}
        try:
            return bit.status()
        except Exception:
            logger.exception("Bit.status raised; reporting empty status")
            return {}

    def _broadcast_status_if_changed(self) -> None:
        status = self._current_status()
        if status != self._last_status:
            self._last_status = status
            self.server.broadcast(protocol.bit_status_event(status))

    # --- engine observer callbacks -----------------------------------------
    def on_state_change(self, old_state: State, new_state: State) -> None:
        self.server.broadcast(protocol.state_changed_event(new_state.name))
        if new_state == State.UNLOADING:
            self._broadcast_bit_completed()

    def on_registration_change(self) -> None:
        self.server.broadcast(
            protocol.registration_changed_event(self._non_room_counts()))

    def on_devices_change(self) -> None:
        self.server.broadcast(protocol.devices_changed_event(
            self._devices_view()))

    def _broadcast_bit_completed(self) -> None:
        bit = self.game_server.bit
        if bit is None:
            return
        try:
            result = bit.result()
        except Exception:
            logger.exception("Bit.result raised; not broadcasting bit_completed")
            return
        if result is not None:
            self.server.broadcast(protocol.bit_completed_event(result))
