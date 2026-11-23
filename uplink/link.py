"""UplinkAgent: translates between the wire protocol and GameServer calls.
See design spec sections 3-5.
"""

import logging
import time
import uuid
from datetime import datetime, timezone

from websockets.exceptions import InvalidStatus

from control.bit_config import ManifestError
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.lobby import TERRARIUM_ADMIN
from control.rooms import non_room_counts
from control.state import State
from control.terrarium import TerrariumState, unload_room_refusal
from uplink import protocol
from uplink.transport import frame_size

logger = logging.getLogger(__name__)


class UplinkAgent:
    INITIAL_BACKOFF_SECONDS = 1.0
    MAX_BACKOFF_SECONDS = 30.0
    # Box contract (mm-fairyring spec 2026-11-23 sections 4.4 and 4.8): the
    # broker closes a connection silent for longer than its idle timeout,
    # and closes one that sends a frame over its size limit.
    HEARTBEAT_SECONDS = 60.0
    MAX_FRAME_BYTES = 32000
    # Another box connected with this identity and the broker told this one
    # to stand down (spec 4.4). Reconnecting at once would displace it and
    # start a fight, so wait long, doubling per repeat, and start over once a
    # connection has held for STABLE_SECONDS.
    REPLACED_INITIAL_SECONDS = 60.0
    REPLACED_MAX_SECONDS = 600.0
    STABLE_SECONDS = 300.0

    def __init__(self, game_server: GameServer, transport, *,
                 time_source=time.monotonic, registry=None, terrarium=None,
                 identity=None, lan_ip=None, journal=None,
                 clients_live=None, boot_id: str | None = None,
                 id_source=uuid.uuid4,
                 wall_clock=lambda: datetime.now(timezone.utc),
                 admin_version=None, notify=None, admin_store=None):
        self.game_server = game_server
        self.transport = transport
        self.registry = registry
        # Optional Terrarium (control/terrarium.py); None (every pre-Task-6
        # caller) means no room commands and no terrarium-state stamping --
        # zero behavior change.
        self.terrarium = terrarium
        # The box's UplinkIdentity (None: a test-only or pre-config agent).
        # It travels in the transport's handshake headers, never as a frame;
        # kept here for the tenant and terrarium names. lan_ip is an
        # injected callable so uplink/ never imports harness/.
        self.identity = identity
        self._lan_ip = lan_ip
        self._time_source = time_source
        self._next_attempt_at = 0.0
        self._backoff = self.INITIAL_BACKOFF_SECONDS
        # Every up-frame but the heartbeat carries boot_id (one per process)
        # and seq (from 1 within it, gapless over frames that reached the
        # wire or the journal; spec 4.2). _seq is the last seq handed out.
        self.boot_id = boot_id if boot_id is not None else uuid.uuid4().hex
        self._seq = 0
        # completion_id source (uuid4) and the wall clock read only for
        # completed_at; every timer uses time_source (monotonic).
        self._id_source = id_source
        self._wall_clock = wall_clock
        # control/admin_devices_store.py AdminDevicesStore or None: where an
        # applied set_admin_devices manifest is kept (spec 4.10). With none,
        # every manifest is refused.
        self.admin_store = admin_store
        # Version of the applied admin-device manifest, sent in every
        # resync's admin_devices_applied (0: none ever applied). Defaults
        # to the store's version when there is a store.
        if admin_version is None:
            if admin_store is not None:
                admin_version = lambda: admin_store.version
            else:
                admin_version = lambda: 0
        self._admin_version = admin_version
        # time_source() of the last successful connect, and of the last
        # heartbeat (or the connect) the heartbeat timer counts from.
        self._connected_at: float | None = None
        self._heartbeat_from: float | None = None
        # The wait the next session_replaced will impose, and the optional
        # one-string operator callback (never given the secret).
        self._replaced_wait = self.REPLACED_INITIAL_SECONDS
        self._notify = notify
        # See ConsoleAgent.on_terrarium_state_change's docstring: a load
        # failure lands back in NO_ROOM via ROOM_LOADING and must NOT be
        # reported as room_unloaded; only a NO_ROOM entry via ROOM_UNLOADING
        # (a normal unload) does. Captured on ROOM_UNLOADING entry, before
        # Terrarium clears .room.
        self._unloading_room_name: str | None = None
        # uplink/journal.py Journal or None: bit_completed is appended,
        # stamped, before any send, replayed after every resync over a
        # durable transport, and leaves only by completions_stored or the
        # cap (box contract spec 4.6). Entries from before completion_id
        # can never be confirmed, so they are dropped once, here.
        self.journal = journal
        # completion_ids confirmed during the current drain, removed from
        # the journal in one rewrite when the drain ends.
        self._confirmed: list[str] = []
        if journal is not None:
            try:
                purged = journal.purge_unidentified()
            except (OSError, ValueError):
                logger.exception("could not purge unidentified journal entries")
            else:
                if purged:
                    logger.warning("dropped %d journal entries with no "
                                   "completion_id", purged)
        # Same hook ConsoleAgent takes: a callable (None = no hook) that is
        # True while Control's Arco clients are up. unload_room is refused
        # while it holds (control.terrarium.unload_room_refusal, D7).
        self._clients_live = clients_live
        game_server.add_observer(self)
        if terrarium is not None:
            terrarium.add_observer(self)

    def maintain_connection(self) -> None:
        """Call once per tick-loop iteration, alongside poll(). Attempts to
        (re)connect on a backoff schedule; never blocks or raises to the
        caller if an attempt fails. While connected it keeps the heartbeat."""
        now = self._time_source()
        if self.transport.connected:
            self._tick_connected(now)
            return
        if now < self._next_attempt_at:
            return
        # Frames the broker sent just before closing (session_replaced,
        # completions_stored) may still be buffered on the old socket when a
        # send was the first to notice the close: handle them before
        # reconnecting. A session_replaced among them sets the long wait.
        self._drain()
        if now < self._next_attempt_at:
            return
        try:
            self.transport.connect()
        except Exception as exc:
            self._log_connect_failure(exc)
            self._next_attempt_at = now + self._backoff
            self._backoff = min(self._backoff * 2, self.MAX_BACKOFF_SECONDS)
            return
        logger.info("uplink connected (%s)", self._who())
        self._connected_at = now
        self._heartbeat_from = now
        try:
            self._send_resync()
            self._replay_journal()
        except Exception:
            # A refused identity never gets here: the broker answers it with
            # HTTP 401 inside connect(). This is a socket that closed right
            # after opening, a failed attempt like any other: same schedule,
            # same doubling, so the box never reconnects every tick.
            logger.warning("uplink send failed right after connect; "
                            "retrying in %.1fs", self._backoff)
            self._next_attempt_at = now + self._backoff
            self._backoff = min(self._backoff * 2, self.MAX_BACKOFF_SECONDS)
            return
        # Only a fully established link resets the schedule.
        self._backoff = self.INITIAL_BACKOFF_SECONDS
        self._next_attempt_at = 0.0

    def _log_connect_failure(self, exc: Exception) -> None:
        """One WARNING per failed connect: the exception type and, for a
        refused handshake, the HTTP status. Never the request, its headers
        or the secret."""
        if isinstance(exc, InvalidStatus):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            hint = ("; check [uplink] tenant_slug and secret, and [terrarium] "
                    "name" if status == 401 else "")
            logger.warning("uplink connect refused: HTTP %s (%s)%s; retrying "
                           "in %.1fs", status, type(exc).__name__, hint,
                           self._backoff)
            return
        logger.warning("uplink connect failed (%s); retrying in %.1fs",
                       type(exc).__name__, self._backoff)

    def _who(self) -> str:
        if self.identity is None:
            return "this box"
        return (f"tenant {self.identity.tenant_slug}, terrarium "
                f"{self.identity.terrarium_name}")

    def _tick_connected(self, now: float) -> None:
        """Heartbeat every HEARTBEAT_SECONDS while connected, the first one
        HEARTBEAT_SECONDS after the connect. A link connected by someone
        else (tests connect the transport directly) starts the timer at the
        first tick that sees it."""
        if (self._connected_at is not None
                and now - self._connected_at >= self.STABLE_SECONDS):
            self._replaced_wait = self.REPLACED_INITIAL_SECONDS
        if self._heartbeat_from is None:
            self._heartbeat_from = now
            return
        if now - self._heartbeat_from < self.HEARTBEAT_SECONDS:
            return
        self._heartbeat_from = now
        try:
            # Exactly {"event": "heartbeat"}: not stamped (fairyring never
            # stores it, so a seq would leave a gap) and never journalled.
            self.transport.send(protocol.heartbeat_event())
        except Exception:
            logger.warning("uplink heartbeat send failed")

    def _on_replaced(self, now: float) -> None:
        """Another box took this identity: close, tell the operator, and hold
        off reconnecting for the replaced wait (then double it, to the cap).
        The ordinary back-off is untouched; it only runs after this hold."""
        wait = self._replaced_wait
        self._replaced_wait = min(wait * 2, self.REPLACED_MAX_SECONDS)
        self.transport.close()
        self._next_attempt_at = now + wait
        message = (f"another box is using this identity ({self._who()}); not "
                   f"reconnecting for {wait:.0f}s")
        logger.warning("%s", message)
        if self._notify is not None:
            try:
                self._notify(message)
            except Exception:
                logger.exception("session_replaced notify callback raised")

    def _admin_manifest_refusal(self, manifest: dict) -> str | None:
        """Why manifest may not be applied to this box, or None. Every check
        but the version comparison: a malformed or misaddressed manifest is
        refused whatever its version (spec 4.10)."""
        if self.identity is None:
            return "no uplink identity"
        schema = manifest.get("schema")
        if (not isinstance(schema, int) or isinstance(schema, bool)
                or schema != 1):
            return "unsupported schema"
        if manifest.get("tenant_slug") != self.identity.tenant_slug:
            return "tenant_slug is not this box's"
        if manifest.get("terrarium_name") != self.identity.terrarium_name:
            return "terrarium_name is not this box's"
        version = manifest.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            return "version must be an integer"
        devices = manifest.get("devices")
        if not isinstance(devices, list):
            return "devices must be a list"
        seen = set()
        for device in devices:
            if not isinstance(device, dict):
                return "each device must be an object"
            gem_uid = device.get("gem_uid")
            if not isinstance(gem_uid, str) or not gem_uid:
                return "each device needs a non-empty string gem_uid"
            if not isinstance(device.get("status"), str):
                return "each device needs a string status"
            if gem_uid == TERRARIUM_ADMIN:
                return f"{TERRARIUM_ADMIN!r} is reserved and cannot be listed"
            # Whatever the statuses: an id listed twice is ambiguous, and
            # reading it either way could grant a right the manifest denied.
            if gem_uid in seen:
                return "duplicate gem_uid"
            seen.add(gem_uid)
        if self.admin_store is None:
            return "no admin store"
        return None

    def _on_set_admin_devices(self, manifest: dict) -> None:
        """Apply a MycoQuest admin-device manifest (spec 4.10): validate,
        save, replace the engine's list, report the version. A version not
        above the applied one is a silent no-op; any other failure sends
        an error and changes nothing."""
        reason = self._admin_manifest_refusal(manifest)
        if reason is not None:
            logger.warning("set_admin_devices refused: %s", reason)
            self._send(protocol.error_event("set_admin_devices", reason))
            return
        version = manifest["version"]
        if version <= self.admin_store.version:
            logger.debug("set_admin_devices version %d not above applied %d; "
                         "ignored", version, self.admin_store.version)
            return
        active = tuple(dict.fromkeys(
            d["gem_uid"] for d in manifest["devices"]
            if d["status"] == "active"))
        try:
            self.admin_store.save(
                active, version, tenant_slug=self.identity.tenant_slug,
                terrarium_name=self.identity.terrarium_name)
        except OSError:
            logger.exception("could not save admin devices version %d", version)
            self._send(protocol.error_event(
                "set_admin_devices", "could not save the admin list"))
            return
        self.game_server.set_admin_devices(active)
        logger.info("admin devices version %d applied (%d active)",
                    version, len(active))
        self._send(protocol.admin_devices_applied_event(version))

    def _replay_journal(self) -> None:
        """Send every journal entry exactly as stored (its original boot_id
        and seq). Sending removes nothing: only completions_stored does."""
        if self.journal is None or not getattr(self.transport, "durable", False):
            return
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
        self._send(protocol.admin_devices_applied_event(self._admin_version()))

    def poll(self) -> None:
        """Drain and handle any inbound commands. Call once per tick-loop
        iteration, alongside GameServer.tick() -- independent of it."""
        if not self.transport.connected:
            return
        self._drain()

    def _drain(self) -> None:
        """Handle every frame the transport has waiting. Ends at the first
        None, at a receive error or after a session_replaced; every
        completion confirmed on the way is removed from the journal in one
        rewrite at the end, however the drain ended."""
        try:
            while True:
                try:
                    msg = self.transport.receive()
                except Exception:
                    logger.warning("uplink receive failed; will retry next tick")
                    return
                if msg is None:
                    return
                try:
                    replaced = self._handle_message(msg)
                except Exception as exc:
                    # Typically a reply send on a link that just closed. The
                    # rest of the buffer is still read: it may hold a
                    # session_replaced or confirmations.
                    logger.warning("uplink %r command failed (%s); continuing",
                                   msg.get("command"), type(exc).__name__)
                    continue
                if replaced:
                    return
        finally:
            self._remove_confirmed()

    def _remove_confirmed(self) -> None:
        ids, self._confirmed = self._confirmed, []
        if not ids or self.journal is None:
            return
        try:
            removed = self.journal.remove(ids)
        except (OSError, ValueError):
            # The entries stay and are replayed and confirmed again: nothing
            # is lost.
            logger.exception("could not remove confirmed journal entries")
            return
        logger.debug("completions_stored: %d of %d journal entries removed",
                     removed, len(ids))

    def _handle_message(self, msg: dict) -> bool:
        """Handle one frame; True when it was session_replaced."""
        try:
            command = protocol.parse_command(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable uplink message: %s", exc)
            return False
        self._dispatch(msg.get("command"), command)
        return isinstance(command, protocol.SessionReplacedCommand)

    def _dispatch(self, command_name: str, command) -> None:
        if isinstance(command, protocol.CompletionsStoredCommand):
            # Collected; _drain removes them in one rewrite when it ends.
            self._confirmed.extend(command.completion_ids)
            return
        if isinstance(command, protocol.SetAdminDevicesCommand):
            self._on_set_admin_devices(command.manifest)
            return
        if isinstance(command, protocol.SessionReplacedCommand):
            self._on_replaced(self._time_source())
            return
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
            reason = unload_room_refusal(self._clients_live)
            if reason is not None:
                self._send(protocol.error_event(command_name, reason))
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
            elif isinstance(command, protocol.RestartCommand):
                # Same soft cycle as ConsoleAgent's RESTART (2026-09-01
                # spec section 6): same bit, same resolved config, Room
                # untouched. It composes abort + load_bit, both of which
                # the uplink already accepts, so it grants no new reach.
                gs = self.game_server
                if gs.bit_name is None:
                    self._send(protocol.error_event(command_name, "no bit loaded"))
                    return
                bit_name = gs.bit_name
                cfg = getattr(gs.bit, "config", None)
                gs.abort()
                gs.load_bit(bit_name, config=cfg)
        except (InvalidTransition, BitLoadError) as exc:
            self._send(protocol.error_event(command_name, str(exc)))

    def on_state_change(self, old_state: State, new_state: State) -> None:
        terrarium_state = (
            self.terrarium.state.name if self.terrarium is not None else None)
        try:
            self._send(protocol.state_changed_event(
                new_state.name, self.game_server.bit_name,
                terrarium_state=terrarium_state))
        finally:
            # COMPLETING is reached only by the tick-triggered completion
            # path; abort() skips it, so an aborted round is never reported
            # as completed (spec 2026-09-13 section 5.3). Registration is
            # still populated here; it is released during UNLOADING. In a
            # finally: a failed state_changed send must not cost the
            # completion its journal entry.
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
        completed_at = self._wall_clock().astimezone(timezone.utc)
        event = protocol.bit_completed_event(
            result, gs.bit_name or "", bit.version,
            room_name=gs.provenance.get("room_name"),
            terrarium_config_version=gs.provenance.get("terrarium_config_version"),
            players=protocol.players_view(granted),
            completion_id=str(self._id_source()),
            completed_at=completed_at.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self._emit_bit_completed(event)

    def _emit_bit_completed(self, event: dict) -> None:
        """Stamp once, journal the stamped frame, then send that same frame:
        a replay carries the boot_id and seq it was first given. With no
        journal and no link the frame goes nowhere, so it takes no seq."""
        if self.journal is None and not self.transport.connected:
            return
        frame = self._stamp(event)
        if frame is None:
            return
        journalled = False
        if self.journal is not None:
            try:
                self.journal.append(frame)
                journalled = True
            except (OSError, ValueError):
                logger.exception("could not journal bit_completed; sending live only")
        if self.transport.connected:
            try:
                self.transport.send(frame)
            except Exception:
                if journalled:
                    logger.warning("bit_completed %s live send failed; kept in "
                                   "the journal for the next connect",
                                   frame.get("completion_id"))
                else:
                    logger.warning("bit_completed %s live send failed and it "
                                   "is not journalled; it is lost",
                                   frame.get("completion_id"))

    def on_registration_change(self) -> None:
        counts = non_room_counts(self.game_server.registration)
        self._send(protocol.registration_changed_event(counts))

    def _stamp(self, msg: dict) -> dict | None:
        """A copy of msg with boot_id and the next seq, or None when it is
        over MAX_FRAME_BYTES (spec 4.8). The size is measured with the seq
        the frame would get; the counter advances only when a frame is
        returned. An oversize bit_completed loses its result first."""
        seq = self._seq + 1
        frame = dict(msg, boot_id=self.boot_id, seq=seq)
        size = frame_size(frame)
        if size > self.MAX_FRAME_BYTES:
            event = msg.get("event")
            if event != "bit_completed":
                logger.warning("dropping oversize %s frame (%d bytes > %d)",
                               event, size, self.MAX_FRAME_BYTES)
                return None
            frame = dict(msg, result=None, result_dropped=True,
                         boot_id=self.boot_id, seq=seq)
            stripped = frame_size(frame)
            if stripped > self.MAX_FRAME_BYTES:
                logger.warning("dropping oversize bit_completed %s (%d bytes "
                               "> %d even without its result)",
                               msg.get("completion_id"), stripped,
                               self.MAX_FRAME_BYTES)
                return None
            logger.warning("bit_completed %s result dropped (%d bytes > %d)",
                           msg.get("completion_id"), size, self.MAX_FRAME_BYTES)
        self._seq = seq
        return frame

    def _send(self, msg: dict) -> None:
        """Stamp and send. While disconnected the frame is dropped before
        stamping, so it takes no seq. Heartbeats bypass this."""
        if not self.transport.connected:
            return
        frame = self._stamp(msg)
        if frame is not None:
            self.transport.send(frame)
