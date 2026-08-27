"""Terrarium: the room-level state machine sitting above GameServer. Owns
load_room/unload_room -- the sequence that used to be control/boot.py's
one-shot boot() -- as a machine with its own state and observer list, so a
Console-driven room switch mid-installation (Task 6) has somewhere to live.
See docs/superpowers/specs/
2026-08-26-terrarium-lifecycle-and-config-rooms-design.md.
"""

from __future__ import annotations

import logging
import os
import time
from enum import Enum

from control.arco_process import ArcoProcess
from control.boot import (_bind_room_fast_path, _canonical_room_dev,
                          wait_for_room_binding, RoomBindingTimeout)
from control.boot_config import BootConfig
from control.engine import BitLoadError, GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import RoomBridge
from control.rooms import Room
from control.run_record import RunRecorder, sweep_stale, _default_spawn_time
from control.state import State
from control.teardown import TeardownStack
from control.terrarium_config import TerrariumConfig, validate_rooms

logger = logging.getLogger(__name__)


class TerrariumState(Enum):
    NO_ROOM = "no_room"
    ROOM_LOADING = "room_loading"
    ROOM_READY = "room_ready"
    ROOM_UNLOADING = "room_unloading"


class RoomLoadError(Exception):
    """Every load_room failure, raised only internally to short-circuit the
    sequence -- load_room itself never lets this escape to its caller; it
    catches it, unwinds, and returns the reason string instead. Mirrors
    GameServer.fire_trigger's never-raises contract."""


class Terrarium:
    """Owns the Room-level lifecycle: which Room (if any) is loaded, Arco,
    the Room bridge, and the room-scoped TeardownStack. `game_server` is
    constructed by the caller (its bit_registry, cue_horizon, and clock are
    installation-wide, not per-room) and handed in already; Terrarium sets
    and clears its `.room` across load/unload but never replaces it."""

    def __init__(self, config: TerrariumConfig, game_server: GameServer,
                 room_binding: RoomBindingRegistry, *,
                 boot_config: BootConfig, arco_command: list,
                 arco_process_cls=ArcoProcess, simulator_factory=None,
                 known_device_connected=lambda dev: False, tick=None,
                 sweep=None, ownership_probe=None,
                 binding_store_path: str | None = None,
                 stack_factory=TeardownStack,
                 runs_dir: str | None = None,
                 run_id: str | None = None) -> None:
        self.config = config
        self.gs = game_server
        self.room_binding = room_binding
        self.boot_config = boot_config
        self.arco_command = arco_command
        self.arco_process_cls = arco_process_cls
        self.simulator_factory = simulator_factory
        self.known_device_connected = known_device_connected
        self.tick = tick
        self.ownership_probe = ownership_probe
        self.binding_store_path = binding_store_path
        self.runs_dir = runs_dir
        self.run_id = run_id

        # runs_dir=None (every existing caller/test) means no recording and
        # no default sweep -- zero behavior change. When set, every process
        # this load spawns is recorded (design spec section 5) so a later
        # load_room's sweep can prove ownership of anything left running by
        # a crashed prior run.
        self._run_recorder = (
            RunRecorder(os.path.join(runs_dir, run_id, "procs.jsonl"))
            if runs_dir is not None else None)
        self.sweep = sweep
        if self.sweep is None and runs_dir is not None:
            self.sweep = lambda: sweep_stale(runs_dir)
        # Called with no arguments to produce each load_room's room-scoped
        # stack; defaults to a fresh TeardownStack. control/boot.py's
        # compat wrapper overrides this to hand back a caller-supplied
        # stack (harness/terrarium_boot.py starts its DeviceLinkServer
        # before boot() and needs its own step on the same stack).
        self.stack_factory = stack_factory

        self.state = TerrariumState.NO_ROOM
        self.room: Room | None = None
        self.room_stack: TeardownStack | None = None
        self.arco = None
        self.room_bridge = None
        self._observers: list = []

    def add_observer(self, observer) -> None:
        self._observers.append(observer)

    def _notify(self, method: str, *args) -> None:
        """Mirrors GameServer._notify (control/engine.py): a raising
        observer is logged and never interrupts the remaining observers or
        the load_room/unload_room sequence that triggered the notification.
        """
        for observer in self._observers:
            fn = getattr(observer, method, None)
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception:
                logger.exception("observer %r %s raised; continuing",
                                 observer, method)

    def _set_state(self, new_state: TerrariumState) -> None:
        old_state = self.state
        self.state = new_state
        self._notify("on_terrarium_state_change", old_state, new_state)

    def _progress(self, stage: str) -> None:
        self._notify("on_room_load_progress", stage)

    def _record_for(self, role: str):
        """A callable(pid) that appends a SpawnRecord for `role`, or None
        when no runs_dir was configured (the zero-recording default).
        Spawn time is re-read via the same helper sweep_stale's default
        uses, so a later sweep's pid-reuse comparison is apples to apples;
        falls back to "now" if that probe fails."""
        if self._run_recorder is None:
            return None
        recorder = self._run_recorder

        def _record(pid: int) -> None:
            spawn_time = _default_spawn_time(pid)
            if spawn_time is None:
                spawn_time = time.time()
            recorder.record(pid, role, spawn_time=spawn_time)
        return _record

    def _simulator_factory_with_recording(self):
        """self.simulator_factory, unchanged, when no runs_dir was
        configured -- the zero-recording default calls it with the exact
        same (teardown, fixture_name) signature every existing caller
        already uses. When runs_dir IS configured, wraps it so each
        fixture's spawn is recorded under its own "simulator:<fixture>"
        role, via a `record=` keyword the factory is expected to accept and
        thread into SimulatorProcess (control/simulator_process.py)."""
        factory = self.simulator_factory
        if factory is None or self._run_recorder is None:
            return factory

        def _wrapped(teardown, fixture_name):
            record = self._record_for(f"simulator:{fixture_name}")
            return factory(teardown, fixture_name, record=record)
        return _wrapped

    def load_room(self, name: str) -> str | None:
        """Load `name` as the active Room. Returns None on success, else a
        refusal reason -- this never raises to the caller. Any mid-load
        failure closes whatever teardown steps this attempt registered and
        returns to NO_ROOM, leaving a subsequent load_room free to try
        again with a fresh stack."""
        if self.state != TerrariumState.NO_ROOM:
            return f"cannot load {name!r}: Terrarium is {self.state.value}, not no_room"

        self._set_state(TerrariumState.ROOM_LOADING)
        stack = None
        try:
            self._progress("validating")
            spec = self.config.rooms.get(name)
            if spec is None:
                known = sorted(self.config.rooms)
                raise RoomLoadError(
                    f"unknown room {name!r}; known rooms: {known}")
            reasons = validate_rooms(
                self.config,
                array_backend_configured=self.boot_config.array_backend_configured)
            reason = reasons.get(name)
            if reason is not None:
                raise RoomLoadError(reason)

            if self.sweep is not None:
                self._progress("sweeping")
                self.sweep()

            if self.ownership_probe is not None:
                claimant = self.ownership_probe()
                if claimant:
                    raise RoomLoadError(str(claimant))

            stack = self.stack_factory()
            self.room_stack = stack

            self._progress("spawning arco")
            arco_record = self._record_for("arco")
            if arco_record is not None:
                arco = self.arco_process_cls(self.arco_command, record=arco_record)
            else:
                arco = self.arco_process_cls(self.arco_command)
            try:
                arco.start()
            except Exception as exc:
                raise RoomLoadError(f"Arco failed to start: {exc}") from exc
            stack.push("arco", arco.shutdown)
            try:
                arco.wait_ready(spec.arco_ready_timeout)
            except Exception as exc:
                raise RoomLoadError(f"Arco failed to start: {exc}") from exc
            self.arco = arco

            room = Room(name=spec.name, profile=spec.profile,
                       node_id=spec.node_id)
            self.gs.room = room

            self._progress("binding fixtures")
            if self.binding_store_path is not None:
                self.room_binding.load(self.binding_store_path)
            _bind_room_fast_path(room, self.room_binding,
                                 self._simulator_factory_with_recording(),
                                 self.known_device_connected, stack)

            if not room.fully_bound(room.profile):
                try:
                    wait_for_room_binding(
                        self.gs, self.room_binding,
                        self.boot_config.room_setup_timeout,
                        tick=self.tick or (lambda: self.gs.tick(0.05)))
                except RoomBindingTimeout as exc:
                    raise RoomLoadError(str(exc)) from exc

            room_bridge = RoomBridge()
            canonical = _canonical_room_dev(room.profile, room.bound)
            if canonical is not None:
                room_bridge.bind(canonical)
            stack.push("room-bridge", room_bridge.shutdown)

            self.room = room
            self.room_bridge = room_bridge
            self._progress("room ready")
            self._set_state(TerrariumState.ROOM_READY)
            return None
        except BaseException as exc:
            if stack is not None:
                stack.close()
            self.gs.room = None
            self.room = None
            self.room_bridge = None
            self.arco = None
            self.room_stack = None
            self._set_state(TerrariumState.NO_ROOM)
            if isinstance(exc, RoomLoadError):
                return str(exc)
            return str(exc)

    def unload_room(self, force: bool = False) -> str | None:
        """Tear the active Room down. Returns None on success, else a
        refusal reason. Refuses unless ROOM_READY; refuses if the Bit is
        not IDLE unless force is given, in which case it aborts the Bit
        first."""
        if self.state != TerrariumState.ROOM_READY:
            return f"cannot unload: Terrarium is {self.state.value}, not room_ready"
        if self.gs.state != State.IDLE:
            if not force:
                return (f"cannot unload: Bit is {self.gs.state}, not IDLE "
                        "(pass force to abort it)")
            self.gs.abort()

        self._set_state(TerrariumState.ROOM_UNLOADING)
        if self.binding_store_path is not None:
            self.room_binding.save(self.binding_store_path)
        if self.room_stack is not None:
            self.room_stack.close()
        self.gs.clear_devices()
        self.gs.room = None
        self.room = None
        self.room_bridge = None
        self.arco = None
        self.room_stack = None
        self._set_state(TerrariumState.NO_ROOM)
        return None
