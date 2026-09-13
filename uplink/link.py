"""UplinkAgent: translates between the wire protocol and GameServer calls.
See design spec sections 3-5.
"""

import logging
import time

from control.bit_config import ManifestError
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.lobby import TERRARIUM_ADMIN
from control.rooms import non_room_counts
from control.state import State
from control.terrarium import TerrariumState
from uplink import protocol

logger = logging.getLogger(__name__)


class UplinkAgent:
    INITIAL_BACKOFF_SECONDS = 1.0
    MAX_BACKOFF_SECONDS = 30.0

    def __init__(self, game_server: GameServer, transport, *,
                 time_source=time.monotonic, registry=None, terrarium=None,
                 identity=None, lan_ip=None, journal=None):
        self.game_server = game_server
        self.transport = transport
        self.registry = registry
        # Optional Terrarium (control/terrarium.py); None (every pre-Task-6
        # caller) means no room commands and no terrarium-state stamping --
        # zero behavior change.
        self.terrarium = terrarium
        # Identity presented first on every connect (None: a test-only or
        # pre-config agent sends none). lan_ip is an injected callable so
        # uplink/ never imports harness/.
        self.identity = identity
        self._lan_ip = lan_ip
        self._time_source = time_source
        self._next_attempt_at = 0.0
        self._backoff = self.INITIAL_BACKOFF_SECONDS
        # See ConsoleAgent.on_terrarium_state_change's docstring: a load
        # failure lands back in NO_ROOM via ROOM_LOADING and must NOT be
        # reported as room_unloaded; only a NO_ROOM entry via ROOM_UNLOADING
        # (a normal unload) does. Captured on ROOM_UNLOADING entry, before
        # Terrarium clears .room.
        self._unloading_room_name: str | None = None
        # uplink/journal.py Journal or None: bit_completed is appended before
        # any send and replayed after the resync over a durable transport
        # (spec 2026-09-13 section 6.4).
        self.journal = journal
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
        try:
            if self.identity is not None:
                self._send(protocol.identity_frame(self.identity))
            self._send_resync()
            self._replay_journal()
        except Exception:
            logger.warning("uplink send failed right after connect; "
                            "leaving the retry to the backoff schedule")
            return

    def _replay_journal(self) -> None:
        if self.journal is None or not getattr(self.transport, "durable", False):
            return
        had_lines = not self.journal.is_empty()
        entries = self.journal.entries()
        for event in entries:
            if not self.transport.connected:
                return
            try:
                self.transport.send(event)
            except Exception:
                logger.warning("uplink dropped mid-replay; %d journal entries "
                               "kept for the next connect", len(entries))
                return
        if had_lines:
            self.journal.clear()

    def _send_resync(self) -> None:
        terrarium_state = (
            self.terrarium.state.name if self.terrarium is not None else None)
        lan_ip = None
        if self._lan_ip is not None:
            try:
                lan_ip = self._lan_ip()
            except Exception:
                logger.exception("lan_ip probe raised; resync carries no address")
        self._send(protocol.state_changed_event(
            self.game_server.state.name, self.game_server.bit_name,
            terrarium_state=terrarium_state, lan_ip=lan_ip))
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
            try:
                msg = self.transport.receive()
            except Exception:
                logger.warning("uplink receive failed; will retry next tick")
                return
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
                reason = self.game_server.request_start(None, TERRARIUM_ADMIN,
                                                        "uplink")
                if reason is not None:
                    self._send(protocol.error_event(command_name, reason))
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
        # COMPLETING is reached only by the tick-triggered completion path;
        # abort() skips it, so an aborted round is never reported as
        # completed (spec 2026-09-13 section 5.3). Registration is still
        # populated here; it is released during UNLOADING.
        if new_state == State.COMPLETING:
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
        gs = self.game_server
        bit = gs.bit
        if bit is None:
            return
        try:
            result = bit.result()
        except Exception:
            logger.exception("Bit.result raised; sending bit_completed with a null result")
            result = None
        granted = gs.registration.granted() if gs.registration is not None else []
        event = protocol.bit_completed_event(
            result, gs.bit_name or "", bit.version,
            room_name=gs.provenance.get("room_name"),
            terrarium_config_version=gs.provenance.get("terrarium_config_version"),
            players=protocol.players_view(granted))
        self._emit_bit_completed(event)

    def _emit_bit_completed(self, event: dict) -> None:
        if self.journal is not None:
            try:
                self.journal.append(event)
            except OSError:
                logger.exception("could not journal bit_completed; sending live only")
        self._send(event)

    def on_registration_change(self) -> None:
        counts = non_room_counts(self.game_server.registration)
        self._send(protocol.registration_changed_event(counts))

    def _send(self, msg: dict) -> None:
        if self.transport.connected:
            self.transport.send(msg)
