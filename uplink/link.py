"""UplinkAgent: translates between the wire protocol and GameServer calls.
See design spec sections 3-5.
"""

import logging
import time

from control.bit_config import ManifestError
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.rooms import non_room_counts
from control.state import State
from control.terrarium import TerrariumState
from uplink import protocol

logger = logging.getLogger(__name__)


class UplinkAgent:
    INITIAL_BACKOFF_SECONDS = 1.0
    MAX_BACKOFF_SECONDS = 30.0

    def __init__(self, game_server: GameServer, transport, *,
                 time_source=time.monotonic, registry=None, terrarium=None):
        self.game_server = game_server
        self.transport = transport
        self.registry = registry
        # Optional Terrarium (control/terrarium.py); None (every pre-Task-6
        # caller) means no room commands and no terrarium-state stamping --
        # zero behavior change.
        self.terrarium = terrarium
        self._time_source = time_source
        self._next_attempt_at = 0.0
        self._backoff = self.INITIAL_BACKOFF_SECONDS
        # See ConsoleAgent.on_terrarium_state_change's docstring: a load
        # failure lands back in NO_ROOM via ROOM_LOADING and must NOT be
        # reported as room_unloaded; only a NO_ROOM entry via ROOM_UNLOADING
        # (a normal unload) does. Captured on ROOM_UNLOADING entry, before
        # Terrarium clears .room.
        self._unloading_room_name: str | None = None
        game_server.add_observer(self)
        if terrarium is not None:
            terrarium.add_observer(self)

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
        terrarium_state = (
            self.terrarium.state.name if self.terrarium is not None else None)
        self._send(protocol.state_changed_event(
            self.game_server.state.name, self.game_server.bit_name,
            terrarium_state=terrarium_state))
        if self.terrarium is not None and self.terrarium.room is not None:
            # Active room name for a reconnecting peer, on the same
            # room_loaded event on_terrarium_state_change would have sent
            # had the connection been up when the room actually loaded.
            self._send(protocol.room_loaded_event(self.terrarium.room.name))
        if self.game_server.registration is not None:
            counts = non_room_counts(self.game_server.registration)
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
        if isinstance(command, protocol.ListBitsCommand):
            if self.registry is None:
                self._send(protocol.error_event(command_name, "no registry"))
                return
            self._send(protocol.bits_listed_event(
                self.registry.list_view(), self.registry.errors_view()))
            return
        if isinstance(command, protocol.LoadRoomCommand):
            if self.terrarium is None:
                self._send(protocol.error_event(command_name, "no terrarium"))
                return
            reason = self.terrarium.load_room(command.name)
            if reason is not None:
                self._send(protocol.room_load_failed_event(command.name, reason))
                self._send(protocol.error_event(command_name, reason))
            return
        if isinstance(command, protocol.UnloadRoomCommand):
            if self.terrarium is None:
                self._send(protocol.error_event(command_name, "no terrarium"))
                return
            reason = self.terrarium.unload_room(force=command.force)
            if reason is not None:
                self._send(protocol.error_event(command_name, reason))
            return
        try:
            if isinstance(command, protocol.LoadBitCommand):
                if (self.terrarium is not None
                        and self.terrarium.state is not TerrariumState.ROOM_READY):
                    self._send(protocol.error_event(command_name, "no room loaded"))
                    return
                if self.registry is None:
                    self.game_server.load_bit(command.name)
                else:
                    try:
                        cfg = self.registry.resolve_config(
                            command.name, command.overrides)
                    except (ManifestError, KeyError) as exc:
                        self._send(protocol.error_event(
                            command_name, str(exc)))
                        return
                    self.game_server.load_bit(command.name, config=cfg)
            elif isinstance(command, protocol.RunCommand):
                self.game_server.run()
            elif isinstance(command, protocol.AbortCommand):
                self.game_server.abort()
        except (InvalidTransition, BitLoadError) as exc:
            self._send(protocol.error_event(command_name, str(exc)))

    def on_state_change(self, old_state: State, new_state: State) -> None:
        terrarium_state = (
            self.terrarium.state.name if self.terrarium is not None else None)
        self._send(protocol.state_changed_event(
            new_state.name, self.game_server.bit_name,
            terrarium_state=terrarium_state))
        if new_state == State.UNLOADING:
            self._send_bit_completed()

    # --- terrarium observer callbacks ---------------------------------------
    def on_terrarium_state_change(self, old_state: TerrariumState,
                                  new_state: TerrariumState) -> None:
        """Terrarium observer hook (control/terrarium.py). Mirrors
        ConsoleAgent.on_terrarium_state_change -- see its docstring for why
        a load failure (ROOM_LOADING -> NO_ROOM) must NOT be reported as
        room_unloaded: that failure path is instead reported directly by
        _dispatch's LoadRoomCommand handling, once terrarium.load_room()
        has returned the refusal reason to it."""
        gs = self.game_server
        self._send(protocol.state_changed_event(
            gs.state.name, gs.bit_name, terrarium_state=new_state.name))
        if new_state == TerrariumState.ROOM_READY:
            if self.terrarium.room is not None:
                self._send(protocol.room_loaded_event(self.terrarium.room.name))
        elif new_state == TerrariumState.ROOM_UNLOADING:
            self._unloading_room_name = (
                self.terrarium.room.name if self.terrarium.room is not None else None)
        elif new_state == TerrariumState.NO_ROOM:
            if old_state == TerrariumState.ROOM_UNLOADING:
                name, self._unloading_room_name = self._unloading_room_name, None
                if name is not None:
                    self._send(protocol.room_unloaded_event(name))
            else:
                self._unloading_room_name = None

    def on_room_load_progress(self, stage: str) -> None:
        self._send(protocol.room_load_progress_event(stage))

    def _send_bit_completed(self) -> None:
        bit = self.game_server.bit
        if bit is None:
            return
        try:
            result = bit.result()
        except Exception:
            logger.exception("Bit.result raised; not sending bit_completed")
            return
        if result is not None:
            self._send(protocol.bit_completed_event(
                result, self.game_server.bit_name or "", bit.version))

    def on_registration_change(self) -> None:
        counts = non_room_counts(self.game_server.registration)
        self._send(protocol.registration_changed_event(counts))

    def _send(self, msg: dict) -> None:
        if self.transport.connected:
            self.transport.send(msg)
