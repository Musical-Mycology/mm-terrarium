"""ConsoleAgent: translates between the console wire protocol and GameServer
calls, and pushes live state to connected browsers. The local, inbound
sibling of uplink.UplinkAgent -- transport-agnostic (it talks to a server
object, see console/server.py), so it is fully testable offline against an
in-process fake. Driven from the engine tick loop via poll().
"""

import logging
import time

from console import protocol
from control.bit_config import ManifestError
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.roles import RoleClass
from control.room_view import room_view
from control.rooms import non_room_counts, room_role_name
from control.state import State
from control.terrarium import TerrariumState
from control.terrarium_config import validate_rooms
from control.function_view import (
    function_fired_view, functions_view, instrument_functions_view)
from control.functions import FIRED_BY_ADMIN_MANUAL
from control.builtins import builtin_functions
from control.instrument import TUNESHROOM

logger = logging.getLogger(__name__)

# How often a Room frame may be broadcast. The Room renders at 44 Hz; the
# Console is a monitor, so it gets roughly 10 Hz and intermediate frames are
# DROPPED rather than queued. Boundary rule 2: nothing here may become
# something gameplay waits on.
ROOM_FRAME_INTERVAL = 0.1

# Same decimation idea as ROOM_FRAME_INTERVAL, applied to a live design-bench
# session: the bench can tick faster than any panel needs to render.
BENCH_FRAME_INTERVAL = 0.1


class ConsoleAgent:
    def __init__(self, game_server: GameServer, server, room_bridge=None,
                 clock=time.monotonic, registry=None, canvas_urls=None,
                 terrarium=None, catalog_root=None, bench_session_factory=None,
                 captures_root=None):
        self.game_server = game_server
        self.server = server
        self.registry = registry
        # Optional Path to the instrument catalog root (instruments/), for
        # the design panel's list/get/save/publish/clone commands. None
        # means no catalog is wired up -- every design command answers
        # error_event rather than crashing on a missing root.
        self.catalog_root = catalog_root
        # Optional Callable[[dict], BenchSession] building the session a
        # DesignBench drives -- see control/design_bench.py. None means no
        # bench backend is wired up: every bench command answers error_event
        # rather than crashing.
        self.bench_session_factory = bench_session_factory
        # Optional Path to the gesture-capture store root, for
        # list_captures/capture_stats/replay_trace. None means no capture
        # store is wired up.
        self.captures_root = captures_root
        self._bench = None  # DesignBench | None, the one live bench session
        self._pending_bench_frame = None
        self._last_bench_frame_at = 0.0
        # Optional Terrarium (control/terrarium.py), the room-level lifecycle
        # sitting above GameServer. None (every pre-Task-6 caller) means no
        # room commands, no terrarium-state gating, and an all-None/empty
        # rooms snapshot -- zero behavior change.
        self.terrarium = terrarium
        # Set only while a room_load_failed broadcast has already been sent
        # by _load_room(); on_terrarium_state_change checks this to skip its
        # own room_unloaded broadcast for that same NO_ROOM entry. See
        # on_terrarium_state_change's docstring.
        self._unloading_room_name: str | None = None
        # Optional Callable[[], dict] of dev -> reported canvas URL, from
        # DeviceLinkAgent.canvas_urls(). None (a GameServer built without a
        # DeviceLinkAgent) yields no URLs anywhere in the Console's views.
        self._canvas_urls = canvas_urls
        # The Room's live MIDI fan-out, for its controllers read-out. Optional:
        # a GameServer built the pre-Room way has none, and the panel then
        # shows the Room's declarations with no live values rather than
        # failing.
        self._room_bridge = room_bridge
        self._last_status: dict | None = None
        self._last_room: dict | None = None
        self._last_functions: list | None = None
        self._last_instrument_functions: dict | None = None
        self._last_surface_instruments: dict | None = None
        self._last_builtins: dict | None = None
        self._clock = clock
        # The latest not-yet-broadcast frame per dev. Each dev's entry is
        # overwritten, not queued: see _broadcast_room_frame and
        # ROOM_FRAME_INTERVAL above. Keyed by dev, not a single slot --
        # _render_room() can call on_room_frame for more than one fixture
        # within one tick, and a single slot silently starves every fixture
        # but the last one to call in that tick.
        self._pending_room_frames: dict[str, bytes] = {}
        self._last_room_frame_at = 0.0
        game_server.add_observer(self)
        if terrarium is not None:
            terrarium.add_observer(self)

    # --- driven once per tick-loop iteration -------------------------------
    def poll(self) -> None:
        for client in self.server.drain_new_clients():
            self.server.send(client, self.snapshot())
            # The Bits panel needs no request round-trip: it renders on
            # bits_listed only, and this is the one place that event fires
            # (see console/static/console.js's renderBits). No registry (a
            # GameServer built the pre-Bits-registry way) means no Bits
            # panel, silently -- not an error, since nothing asked for one.
            if self.registry is not None:
                self.server.send(client, protocol.bits_listed_event(
                    self.registry.list_view(), self.registry.errors_view()))
        for client, msg in self.server.drain_inbound():
            error = self._handle_command(msg)
            if error is not None:
                self.server.send(client, error)
        self._broadcast_status_if_changed()
        self._broadcast_room_if_changed()
        self._broadcast_room_frame()
        self._broadcast_functions_if_changed()
        self._tick_bench()

    def _tick_bench(self) -> None:
        if self._bench is None:
            return
        if self.server.client_count() == 0:
            logger.info("no console clients connected; closing live bench")
            self._bench.close()
            self._bench = None
            self._pending_bench_frame = None
            return
        frame = self._bench.tick()
        if frame is not None:
            self._pending_bench_frame = frame
        if self._pending_bench_frame is None:
            return
        now = self._clock()
        if now - self._last_bench_frame_at < BENCH_FRAME_INTERVAL:
            return
        frame, self._pending_bench_frame = self._pending_bench_frame, None
        self._last_bench_frame_at = now
        self.server.broadcast(protocol.bench_frame_event(frame))

    # --- inbound command dispatch ------------------------------------------
    def _handle_command(self, msg: dict) -> dict | None:
        name = msg.get("command")
        if name in ("arm_room", "release_room", "fire_function",
                    "list_designs", "get_design", "save_design",
                    "publish_design", "clone_design",
                    "bench_start", "bench_stop", "bench_fire", "bench_lane",
                    "list_captures", "capture_stats", "replay_trace"):
            return self._handle_admin_command(msg)
        try:
            command = protocol.parse_command(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable console message: %s", exc)
            return None
        if isinstance(command, protocol.ListBitsCommand):
            if self.registry is None:
                return protocol.error_event(name, "no registry")
            return protocol.bits_listed_event(
                self.registry.list_view(), self.registry.errors_view())
        if isinstance(command, protocol.LoadRoomCommand):
            if self.terrarium is None:
                return protocol.error_event(name, "no terrarium")
            reason = self._load_room(command.name)
            if reason is not None:
                return protocol.error_event(name, reason)
            return None
        if isinstance(command, protocol.UnloadRoomCommand):
            if self.terrarium is None:
                return protocol.error_event(name, "no terrarium")
            reason = self.terrarium.unload_room(force=command.force)
            if reason is not None:
                return protocol.error_event(name, reason)
            return None
        try:
            if isinstance(command, protocol.LoadBitCommand):
                if (self.terrarium is not None
                        and self.terrarium.state is not TerrariumState.ROOM_READY):
                    return protocol.error_event(name, "no room loaded")
                if self.registry is None:
                    self.game_server.load_bit(command.name)
                else:
                    try:
                        cfg = self.registry.resolve_config(
                            command.name, command.overrides)
                    except (ManifestError, KeyError) as exc:
                        return protocol.error_event(name, str(exc))
                    self.game_server.load_bit(command.name, config=cfg)
            elif isinstance(command, protocol.RunCommand):
                self.game_server.run()
            elif isinstance(command, protocol.AbortCommand):
                self.game_server.abort()
        except (InvalidTransition, BitLoadError) as exc:
            return protocol.error_event(name, str(exc))
        return None

    def _load_room(self, name: str) -> str | None:
        """Drives terrarium.load_room and, on refusal, broadcasts
        room_load_failed_event itself -- see on_terrarium_state_change's
        docstring for why this can't be left to the generic observer path.
        Returns the refusal reason (None on success) for the caller to turn
        into an error_event sent to the requesting client only."""
        reason = self.terrarium.load_room(name)
        if reason is not None:
            self.server.broadcast(protocol.room_load_failed_event(name, reason))
        return reason

    def _handle_admin_command(self, msg: dict) -> dict | None:
        name = msg.get("command")
        try:
            command = protocol.parse_admin_command(msg)
        except ValueError as exc:
            return protocol.error_event(name, str(exc))
        if isinstance(command, (protocol.ListDesignsCommand,
                                protocol.GetDesignCommand,
                                protocol.SaveDesignCommand,
                                protocol.PublishDesignCommand,
                                protocol.CloneDesignCommand)):
            return self._handle_design_command(name, command)
        if isinstance(command, (protocol.BenchStartCommand,
                                protocol.BenchStopCommand,
                                protocol.BenchFireCommand,
                                protocol.BenchLaneCommand)):
            return self._handle_bench_command(name, command)
        if isinstance(command, (protocol.ListCapturesCommand,
                                protocol.CaptureStatsCommand,
                                protocol.ReplayTraceCommand)):
            return self._handle_capture_command(name, command)
        if isinstance(command, protocol.FireFunctionCommand):
            # An operator action, tagged as one so the event log never reads it
            # as gameplay. GameServer.fire_function never raises, so a refusal
            # comes back as a reason string rather than an exception.
            reason = self.game_server.fire_function(
                command.name, fired_by=FIRED_BY_ADMIN_MANUAL, dev=command.dev)
            if reason is not None:
                return protocol.error_event(name, reason)
            return None
        room_name = command.room_type
        gs = self.game_server
        if gs.room_binding is None or gs.room is None or gs.room.name != room_name:
            return protocol.error_event(
                name, f"no {room_name} Room configured")
        if isinstance(command, protocol.ArmRoomCommand):
            gs.room_binding.arm(room_name, command.fixture, command.window_seconds)
        elif isinstance(command, protocol.ReleaseRoomCommand):
            gs.room_binding.release(room_name, command.fixture)
        return None

    def _design_rows(self) -> list:
        from control.catalog import load_catalog
        cat = load_catalog(self.catalog_root)
        return [protocol.design_row(e) for e in sorted(
            cat.entries.values(), key=lambda e: (e.name, e.state))]

    def _handle_design_command(self, name: str, command) -> dict | None:
        if self.catalog_root is None:
            return protocol.error_event(name, "no instrument catalog")
        from control.catalog import (clone_entry, load_catalog,
                                     publish_entry, save_draft)
        if isinstance(command, protocol.ListDesignsCommand):
            return protocol.designs_listed_event(self._design_rows())
        if isinstance(command, protocol.GetDesignCommand):
            entry = load_catalog(self.catalog_root).get(
                command.state, command.name)
            if entry is None:
                return protocol.error_event(
                    name, f"no {command.state} design {command.name!r}")
            text = entry.path.read_text(encoding="utf-8")
            errors = [entry.error] if entry.error else []
            return protocol.design_event(entry.name, entry.state, text, errors)
        if isinstance(command, protocol.SaveDesignCommand):
            refusal, _errors = save_draft(
                self.catalog_root, command.name, command.text)
        elif isinstance(command, protocol.PublishDesignCommand):
            refusal = publish_entry(self.catalog_root, command.name)
        else:
            refusal = clone_entry(self.catalog_root, command.source_state,
                                  command.source_name, command.new_name)
        if refusal is not None:
            return protocol.error_event(name, refusal)
        rows = self._design_rows()
        # Mutations reply designs_changed to the caller (below, via the
        # normal reply path) AND broadcast it to every other connected
        # client, mirroring _broadcast_functions_if_changed's fan-out --
        # the design catalog is shared state, so every panel must re-render
        # on a mutation, not just the one that made it.
        self.server.broadcast(protocol.designs_changed_event(rows))
        return protocol.designs_changed_event(rows)

    def _handle_bench_command(self, name: str, command) -> dict | None:
        if isinstance(command, protocol.BenchStopCommand):
            if self._bench is not None:
                self._bench.close()
                self._bench = None
                self._pending_bench_frame = None
            return None
        if isinstance(command, protocol.BenchFireCommand):
            if self._bench is None:
                return protocol.error_event(name, "no bench running")
            reason = self._bench.fire(command.name)
            if reason is not None:
                return protocol.error_event(name, reason)
            return None
        if isinstance(command, protocol.BenchLaneCommand):
            if self._bench is None:
                return protocol.error_event(name, "no bench running")
            self._bench.lane(command.verb, command.value, command.status,
                             command.data1)
            return None
        # BenchStartCommand
        if self.bench_session_factory is None:
            return protocol.error_event(name, "no bench backend")
        from control.catalog import load_catalog
        from control.design_bench import DesignBench
        entry = (load_catalog(self.catalog_root).get(command.state, command.name)
                 if self.catalog_root is not None else None)
        if entry is None:
            return protocol.error_event(
                name, f"no {command.state} design {command.name!r}")
        if entry.instrument is None:
            return protocol.error_event(
                name, entry.error or f"design {command.name!r} failed to parse")
        if self._bench is not None:
            self._bench.close()
            self._pending_bench_frame = None
        session = self.bench_session_factory(entry.instrument.light_manifest)
        self._bench = DesignBench(entry.instrument, session, clock=self._clock)
        return protocol.bench_started_event(self._bench.fireable())

    def _captures_listed(self) -> list:
        root = self.captures_root
        sessions = []
        if root.is_dir():
            for session_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                labels = {}
                for label_dir in sorted(
                        p for p in session_dir.iterdir() if p.is_dir()):
                    labels[label_dir.name] = len(
                        list(label_dir.glob("[0-9]*.json")))
                sessions.append({"session": session_dir.name, "labels": labels})
        return sessions

    def _first_unreadable_trace(self, session_dir) -> "object | None":
        """Returns the path of the first trace file under session_dir that
        fails to parse as valid JSON with the expected trace shape, or None
        if every file is readable. Named separately from session_rows so a
        malformed capture file names itself in the returned error_event
        rather than crashing gesture_eval's own parse."""
        import json

        for path in sorted(session_dir.glob("*/[0-9]*.json")):
            try:
                trace = json.loads(path.read_text())
                trace["samples"]["t_ms"]
                trace["label"]
                trace["capture_id"]
                trace["series"]
            except Exception:
                return path
        return None

    def _handle_capture_command(self, name: str, command) -> dict | None:
        if self.captures_root is None:
            return protocol.error_event(name, "no capture store")
        if isinstance(command, protocol.ListCapturesCommand):
            return protocol.captures_listed_event(self._captures_listed())
        if isinstance(command, (protocol.CaptureStatsCommand,
                                protocol.ReplayTraceCommand)):
            from control.catalog import CATALOG_NAME_RE
            if not CATALOG_NAME_RE.match(command.session):
                return protocol.error_event(name, f"invalid session {command.session!r}")
            if not CATALOG_NAME_RE.match(command.label):
                return protocol.error_event(name, f"invalid label {command.label!r}")
        if isinstance(command, protocol.CaptureStatsCommand):
            from control.gesture_eval import propose_thresholds, session_rows
            session_dir = self.captures_root / command.session
            bad_path = self._first_unreadable_trace(session_dir)
            if bad_path is not None:
                return protocol.error_event(name, f"bad capture at {bad_path}")
            try:
                rows = [r for r in session_rows(session_dir)
                        if r["label"] == command.label]
            except Exception as exc:
                return protocol.error_event(name, f"bad capture data: {exc}")
            return protocol.capture_stats_event(rows, propose_thresholds(rows))
        # ReplayTraceCommand
        import json

        from control.catalog import load_catalog
        from control.gesture_eval import _accel_g, evaluate_trace
        entry = (load_catalog(self.catalog_root).get(command.state, command.name)
                 if self.catalog_root is not None else None)
        if entry is None:
            return protocol.error_event(
                name, f"no {command.state} design {command.name!r}")
        if entry.instrument is None:
            return protocol.error_event(
                name, entry.error or f"design {command.name!r} failed to parse")
        trigger = next((t for t in entry.instrument.event_triggers
                        if t.name == command.trigger), None)
        if trigger is None:
            return protocol.error_event(
                name, f"no event trigger {command.trigger!r} on "
                     f"{command.name!r}")
        path = (self.captures_root / command.session / command.label
                / f"{command.series}.json")
        if not path.is_file():
            return protocol.error_event(name, f"no capture at {path}")
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
            result = evaluate_trace(trace, trigger.thresholds)
            result["trace"] = {"t_ms": list(trace["samples"]["t_ms"]),
                               "accel_g": _accel_g(trace["samples"])}
        except Exception as exc:
            return protocol.error_event(name, f"bad capture at {path}: {exc}")
        return protocol.replay_result_event(result)

    # --- snapshot (connect-time full read model) ---------------------------
    def snapshot(self) -> dict:
        gs = self.game_server
        loaded_bit = None
        roles: list = []
        registration: list = []
        if gs.registration is not None:
            loaded_bit = self._loaded_bit_name()
            roles = [protocol.role_view(
                        r, gs.slot_requirement(r.requires) if r.requires else None)
                     for r in gs.registration.role_table.roles.values()
                     if r.role_class != RoleClass.ROOM]
            registration = protocol.registration_changed_event(
                self._non_room_counts())["roles"]
        self._last_room = self._current_room()
        self._last_functions = self._current_functions()
        self._last_instrument_functions = self._current_instrument_functions()
        self._last_surface_instruments = self._current_surface_instruments()
        self._last_builtins = self._current_builtins()
        return protocol.snapshot_event(
            state=gs.state.name,
            installed_bits=list(gs.bit_registry.keys()),
            loaded_bit=loaded_bit,
            roles=roles,
            registration=registration,
            devices=self._devices_view(),
            bit_status=self._current_status(),
            room=self._last_room,
            functions=self._last_functions,
            terrarium_state=(
                self.terrarium.state.name if self.terrarium is not None else None),
            rooms=self._rooms_view(),
            instrument_functions=self._last_instrument_functions,
            surface_instruments=self._last_surface_instruments,
            builtins=self._last_builtins,
            designs=self._design_rows() if self.catalog_root else [],
        )

    def _rooms_view(self) -> list:
        """The rooms panel's read model: every configured room, its
        boot-time loadability from validate_rooms, and whether it is the
        one currently active. Empty when no Terrarium is wired up."""
        if self.terrarium is None:
            return []
        reasons = validate_rooms(
            self.terrarium.config,
            array_backend_configured=self.terrarium.boot_config.array_backend_configured)
        active_name = (
            self.terrarium.room.name if self.terrarium.room is not None else None)
        return [
            {"name": name, "description": spec.description,
             "status": reasons.get(name), "active": name == active_name}
            for name, spec in self.terrarium.config.rooms.items()
        ]

    def _current_room(self) -> dict | None:
        """Build the Room panel payload, or None when no Room is configured.

        Deliberately scoped: see control/room_view.py's module docstring. The
        Room-hiding filters this class already applies to `roles` and
        `registration` are NOT relaxed by this method; it is a separate view.
        """
        gs = self.game_server
        if gs.room is None:
            return None
        profile = gs.room.profile
        role = None
        if gs.bit is not None and gs.registration is not None:
            role = gs.registration.role_table.roles.get(room_role_name(gs.room.name))
        # Live off `terrarium.room_bridge` when a Terrarium is wired, not
        # the frozen `self._room_bridge` snapshot from __init__: a Room
        # loaded AFTER construction (a NO_ROOM boot's Console `load_room`)
        # leaves `self._room_bridge` at whatever it was then -- None, for a
        # NO_ROOM boot -- and this panel's controllers read-out would stay
        # permanently empty otherwise. Falls back to the __init__ snapshot
        # for a caller with no Terrarium (pre-Task-6 construction shape).
        room_bridge = (self.terrarium.room_bridge if self.terrarium is not None
                      else self._room_bridge)
        controllers = getattr(room_bridge, "controllers", {}) or {}
        urls = self._canvas_urls() if self._canvas_urls else {}
        return room_view(gs.room, profile, role, controllers, urls)

    def _broadcast_room_if_changed(self) -> None:
        room = self._current_room()
        if room != self._last_room:
            self._last_room = room
            self.server.broadcast(protocol.room_changed_event(room))

    def on_room_frame(self, dev: str, frame: bytes) -> None:
        """DeviceLinkAgent's display-only frame sink. Called on the tick
        thread, once per changed fixture slice -- so possibly several times
        per tick, one per dev. Stores the LATEST frame per dev; anything not
        yet broadcast for that dev is overwritten, never queued."""
        self._pending_room_frames[dev] = frame

    def _broadcast_room_frame(self) -> None:
        if not self._pending_room_frames:
            return
        now = self._clock()
        if now - self._last_room_frame_at < ROOM_FRAME_INTERVAL:
            return
        pending, self._pending_room_frames = self._pending_room_frames, {}
        self._last_room_frame_at = now
        for dev, frame in pending.items():
            self.server.broadcast(protocol.room_frame_event(dev, frame))

    def _current_functions(self) -> list:
        bit = self.game_server.bit
        if bit is None:
            return []
        try:
            return functions_view(bit.function_table)
        except Exception:
            logger.exception("Bit.function_table raised; reporting no functions")
            return []

    def _present_instruments(self) -> dict:
        """Room fixture instruments plus TUNESHROOM, keyed by instrument
        name -- exactly the set GameServer.load_bit checks name-fires
        against (control/engine.py's carried_instruments/room_instruments
        blend)."""
        gs = self.game_server
        instruments = {}
        if gs.room is not None:
            for fixture in gs.room.profile.fixtures:
                instruments[fixture.instrument.name] = fixture.instrument
        instruments[TUNESHROOM.name] = TUNESHROOM
        return instruments

    def _current_instrument_functions(self) -> dict:
        return instrument_functions_view(self._present_instruments())

    def _current_surface_instruments(self) -> dict:
        """dev/"room" -> instrument name, for every bound Room fixture and
        every connected device: a bound fixture's dev maps to that
        fixture's instrument, and every other connected device maps to its
        carried instrument (TUNESHROOM's name when uncarried). The literal
        "room" key -- consumed by the diagnostics row's Room option -- maps
        to the FIRST bound fixture's instrument, and is absent entirely when
        no Room is loaded or no fixture is bound."""
        gs = self.game_server
        # Live off `terrarium.room_binding` when a Terrarium is wired, not
        # `gs.room_binding` -- the same reason _current_room reads
        # `terrarium.room_bridge` rather than a frozen __init__ snapshot:
        # the Terrarium owns the RoomBindingRegistry that actually records
        # fixture binds (see control/terrarium.py's load_room), and a
        # GameServer built without one (most test doubles) leaves
        # `gs.room_binding` at None or a separate, never-bound registry.
        room_binding = (self.terrarium.room_binding if self.terrarium is not None
                       else gs.room_binding)
        out: dict[str, str] = {}
        if gs.room is not None and room_binding is not None:
            for fixture in gs.room.profile.fixtures:
                dev = room_binding.bound_device(gs.room.name, fixture.name)
                if dev is not None:
                    out[dev] = fixture.instrument.name
                    # "room" (the diagnostics row's Room option) takes the
                    # FIRST bound fixture's instrument: TEST/DEMO rooms carry
                    # homogeneous fixture instruments today, and this mirrors
                    # the engine's canonical-room-dev convention. No "room"
                    # key at all when nothing is bound.
                    if "room" not in out:
                        out["room"] = fixture.instrument.name
        for info in gs.devices.all():
            carried = getattr(info, "carried", None)
            out[info.dev] = carried.name if carried is not None else TUNESHROOM.name
        return out

    def _current_builtins(self) -> dict:
        return {name: sorted(builtin_functions(inst).keys())
                for name, inst in self._present_instruments().items()}

    def _broadcast_functions_if_changed(self) -> None:
        functions = self._current_functions()
        instrument_functions = self._current_instrument_functions()
        surface_instruments = self._current_surface_instruments()
        builtins = self._current_builtins()
        current = (functions, instrument_functions, surface_instruments, builtins)
        previous = (self._last_functions, self._last_instrument_functions,
                    self._last_surface_instruments, self._last_builtins)
        if current != previous:
            (self._last_functions, self._last_instrument_functions,
             self._last_surface_instruments, self._last_builtins) = current
            self.server.broadcast(protocol.functions_changed_event(
                functions, instrument_functions, surface_instruments, builtins))

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
        urls = self._canvas_urls() if self._canvas_urls else {}
        out = []
        for info in gs.devices.all():
            assigned = assignments.get(info.dev)
            role_name = None
            if assigned is not None and assigned[2] != RoleClass.ROOM:
                role_name = assigned[1]
            out.append(protocol.device_view(
                info, role_name, urls.get(info.dev), info.dev in gs.muted))
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
        terrarium_state = (
            self.terrarium.state.name if self.terrarium is not None else None)
        self.server.broadcast(protocol.state_changed_event(
            new_state.name, self.game_server.bit_name,
            terrarium_state=terrarium_state))
        if new_state == State.UNLOADING:
            self._broadcast_bit_completed()

    # --- terrarium observer callbacks ---------------------------------------
    def on_terrarium_state_change(self, old_state: TerrariumState,
                                  new_state: TerrariumState) -> None:
        """Terrarium observer hook (control/terrarium.py). Broadcasts the
        engine-shaped state_changed_event (stamped with the new terrarium
        state) plus a room lifecycle event on entering ROOM_READY or
        NO_ROOM.

        A load FAILURE also lands back in NO_ROOM (ROOM_LOADING ->
        NO_ROOM), but that must broadcast room_load_failed_event, not
        room_unloaded_event -- and this callback fires synchronously from
        inside terrarium.load_room(), before that call has returned the
        refusal reason to its caller, so it cannot tell load-failure and
        load-success-then-unload apart from state alone. _load_room()
        (the command path's only caller of terrarium.load_room) broadcasts
        room_load_failed_event itself once the reason comes back, so this
        callback only broadcasts room_unloaded_event for a NO_ROOM entry
        reached via ROOM_UNLOADING (a normal unload), never via
        ROOM_LOADING (a load failure)."""
        gs = self.game_server
        self.server.broadcast(protocol.state_changed_event(
            gs.state.name, gs.bit_name, terrarium_state=new_state.name))
        if new_state == TerrariumState.ROOM_READY:
            if self.terrarium.room is not None:
                self.server.broadcast(
                    protocol.room_loaded_event(self.terrarium.room.name))
        elif new_state == TerrariumState.ROOM_UNLOADING:
            self._unloading_room_name = (
                self.terrarium.room.name if self.terrarium.room is not None else None)
        elif new_state == TerrariumState.NO_ROOM:
            if old_state == TerrariumState.ROOM_UNLOADING:
                name, self._unloading_room_name = self._unloading_room_name, None
                if name is not None:
                    self.server.broadcast(protocol.room_unloaded_event(name))
            else:
                self._unloading_room_name = None

    def on_room_load_progress(self, stage: str) -> None:
        self.server.broadcast(protocol.room_load_progress_event(stage))

    def on_registration_change(self) -> None:
        self.server.broadcast(
            protocol.registration_changed_event(self._non_room_counts()))

    def on_devices_change(self) -> None:
        self.server.broadcast(protocol.devices_changed_event(
            self._devices_view()))

    def on_load_warnings(self, warnings) -> None:
        """Engine observer hook (control/engine.py's load_bit): one or more
        name-fire-with-no-script warnings raised at load. Surfaced as `log`
        events rather than folded into functions_changed -- these are
        operator-facing diagnostics, not part of the read model."""
        for warning in warnings:
            self.server.broadcast(protocol.log_event("warn", warning))

    def on_function_fired(self, record) -> None:
        """Engine observer hook. A fire is engine-produced and has no device
        destination, which is why it rides the multi-observer list rather than
        a transport-owned sink -- see the design spec's section 8.1."""
        self.server.broadcast(
            protocol.function_fired_event(function_fired_view(record)))

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
            self.server.broadcast(protocol.bit_completed_event(
                result, self.game_server.bit_name or "", bit.version,
                room_name=self.game_server.provenance.get("room_name"),
                terrarium_config_version=self.game_server.provenance.get(
                    "terrarium_config_version")))
