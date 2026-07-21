"""UplinkAgent: translates between the wire protocol and GameServer calls.
See design spec sections 3-5.
"""

import logging

from control.engine import BitLoadError, GameServer, InvalidTransition
from control.state import State
from uplink import protocol

logger = logging.getLogger(__name__)


class UplinkAgent:
    def __init__(self, game_server: GameServer, transport):
        self.game_server = game_server
        self.transport = transport
        game_server.on_state_change = self._on_state_change
        game_server.on_registration_change = self._on_registration_change

    def poll(self) -> None:
        """Drain and handle any inbound commands. Call once per tick-loop
        iteration, alongside GameServer.tick() -- independent of it."""
        if not self.transport.connected:
            return
        while True:
            msg = self.transport.receive()
            if msg is None:
                return
            self._handle_message(msg)

    def _handle_message(self, msg: dict) -> None:
        try:
            command = protocol.parse_command(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable uplink message: %s", exc)
            return
        self._dispatch(msg.get("command"), command)

    def _dispatch(self, command_name: str, command) -> None:
        try:
            if isinstance(command, protocol.LoadBitCommand):
                self.game_server.load_bit(command.name)
            elif isinstance(command, protocol.RunCommand):
                self.game_server.run()
            elif isinstance(command, protocol.AbortCommand):
                self.game_server.abort()
        except (InvalidTransition, BitLoadError) as exc:
            self._send(protocol.error_event(command_name, str(exc)))

    def _on_state_change(self, old_state: State, new_state: State) -> None:
        self._send(protocol.state_changed_event(new_state.name))
        if new_state == State.UNLOADING:
            self._send_bit_completed()

    def _send_bit_completed(self) -> None:
        bit = self.game_server.bit
        if bit is None:
            return
        result = bit.result()
        if result is not None:
            self._send(protocol.bit_completed_event(result))

    def _on_registration_change(self) -> None:
        counts = self.game_server.registration.counts()
        self._send(protocol.registration_changed_event(counts))

    def _send(self, msg: dict) -> None:
        if self.transport.connected:
            self.transport.send(msg)
