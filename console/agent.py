"""ConsoleAgent: translates between the console wire protocol and GameServer
calls, and pushes live state to connected browsers. The local, inbound
sibling of uplink.UplinkAgent -- transport-agnostic (it talks to a server
object, see console/server.py), so it is fully testable offline against an
in-process fake. Driven from the engine tick loop via poll().
"""

import logging

from console import protocol
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.state import State

logger = logging.getLogger(__name__)


class ConsoleAgent:
    def __init__(self, game_server: GameServer, server):
        self.game_server = game_server
        self.server = server
        self._last_status: dict | None = None
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

    # --- inbound command dispatch ------------------------------------------
    def _handle_command(self, msg: dict) -> dict | None:
        try:
            command = protocol.parse_command(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable console message: %s", exc)
            return None
        name = msg.get("command")
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

    # --- snapshot (connect-time full read model) ---------------------------
    def snapshot(self) -> dict:
        gs = self.game_server
        loaded_bit = None
        roles: list = []
        registration: list = []
        if gs.registration is not None:
            loaded_bit = self._loaded_bit_name()
            roles = [protocol.role_view(r)
                     for r in gs.registration.role_table.roles.values()]
            registration = protocol.registration_changed_event(
                gs.registration.counts())["roles"]
        return protocol.snapshot_event(
            state=gs.state.name,
            installed_bits=list(gs.bit_registry.keys()),
            loaded_bit=loaded_bit,
            roles=roles,
            registration=registration,
            devices=self._devices_view(),
            bit_status=self._current_status(),
        )

    def _loaded_bit_name(self) -> str | None:
        return self.game_server.bit_name

    def _devices_view(self) -> list:
        gs = self.game_server
        assignments = gs.registration.assignments if gs.registration else {}
        out = []
        for info in gs.devices.all():
            assigned = assignments.get(info.dev)
            role_name = assigned[1] if assigned else None
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
        counts = self.game_server.registration.counts()
        self.server.broadcast(protocol.registration_changed_event(counts))

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
