"""UplinkAgent: translates between the wire protocol and GameServer calls.
See design spec sections 3-5.
"""

import logging
import time

from control.engine import BitLoadError, GameServer, InvalidTransition
from control.state import State
from uplink import protocol

logger = logging.getLogger(__name__)


class UplinkAgent:
    INITIAL_BACKOFF_SECONDS = 1.0
    MAX_BACKOFF_SECONDS = 30.0

    def __init__(self, game_server: GameServer, transport, *,
                 time_source=time.monotonic):
        self.game_server = game_server
        self.transport = transport
        self._time_source = time_source
        self._next_attempt_at = 0.0
        self._backoff = self.INITIAL_BACKOFF_SECONDS
        game_server.on_state_change = self._on_state_change
        game_server.on_registration_change = self._on_registration_change

    def maintain_connection(self) -> None:
        """Call once per tick-loop iteration, alongside poll(). Attempts to
        (re)connect on a backoff schedule; never blocks or raises to the
        caller if an attempt fails."""
        if self.transport.connected:
            return
        now = self._time_source()
        if now < self._next_attempt_at:
            return
        try:
            self.transport.connect()
        except Exception:
            logger.warning("uplink connect failed; retrying in %.1fs",
                            self._backoff)
            self._next_attempt_at = now + self._backoff
            self._backoff = min(self._backoff * 2, self.MAX_BACKOFF_SECONDS)
            return
        self._backoff = self.INITIAL_BACKOFF_SECONDS
        self._next_attempt_at = 0.0
        self._send_resync()

    def _send_resync(self) -> None:
        self._send(protocol.state_changed_event(self.game_server.state.name))
        if self.game_server.registration is not None:
            counts = self.game_server.registration.counts()
            self._send(protocol.registration_changed_event(counts))

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
