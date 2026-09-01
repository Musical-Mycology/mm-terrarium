"""python -m harness.terrarium_boot -- boot a Terrarium into a TEST Room,
simulator included. The first real entry point for
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
and its follow-up,
docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md.

Ordering matters here and is why this script -- not control/boot.py --
constructs DeviceLinkServer: the server must already be listening before
boot() calls its simulator_factory, which spawns harness/room_simulator.py
and expects to connect immediately. See design spec section 6.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from control.arco_process import ArcoProcess
from control.bit_config import StartCondition
from control.bit_registry import BitRegistry
from control.boot_config import BootConfig
from control.engine import BitLoadError, GameServer
from control.room_binding import RoomBindingRegistry
from control.run_profile import RunProfile, deep_merge_overrides, parse_profile
from control.simulator_process import SimulatorProcess
from control.start_condition import scored_count, start_decision
from control.state import State
from control.teardown import TeardownStack
from control.terrarium import Terrarium, TerrariumState
from control.terrarium_config import (TerrariumConfig, load_terrarium_config,
                                      resolve_bit_roots)
from devicelink.agent import DeviceLinkAgent
from devicelink.server import DeviceLinkServer
from harness import markers
from harness.o2_shroom import parent_is_gone
from harness.signals import sigterm_as_keyboard_interrupt


def resolve_room_spec(room_name: str, config: TerrariumConfig | None = None, *,
                      config_path: str = "terrarium.toml"):
    """Look up room_name in `config` (a loaded TerrariumConfig), or, if
    `config` is omitted, in the shipped terrarium.toml (or `config_path`) --
    raising a located error listing the valid names on a miss. The --room
    flag's VALUE is a plain config-name string (there is no enum); this is
    where that string becomes a real RoomSpec."""
    if config is None:
        config = load_terrarium_config(config_path)
    try:
        return config.rooms[room_name]
    except KeyError:
        valid = ", ".join(sorted(config.rooms))
        raise SystemExit(
            f"unknown room {room_name!r}; available: {valid}") from None


def sim_dev(fixture: str) -> str:
    """Deterministic o2lite service name per fixture -- unique per fixture,
    which is the entire reason each is spawned as its own client (design
    spec section 3)."""
    return f"sim-room-{fixture}"


class _SimulatorFactory:
    """boot()'s simulator_factory contract is Callable[[TeardownStack, str],
    str]: (teardown, fixture_name) -> dev, called once per fixture. The
    factory registers whatever it spawns on the stack it is handed, so an
    orphaned simulator is impossible by construction. `self.processes` is
    kept only so tests can inspect the handles."""

    def __init__(self, server_url: str, *, popen=subprocess.Popen,
                 horizon: float | None = None,
                 room_type: str = "TEST") -> None:
        self._server_url = server_url
        self._popen = popen
        self._horizon = horizon
        self._room_type = room_type
        self.processes: list[SimulatorProcess] = []

    def __call__(self, teardown, fixture: str, *, record=None) -> str:
        dev = sim_dev(fixture)
        command = [sys.executable, "-u", "-m", "harness.room_simulator",
                   "--dev", dev, "--server", self._server_url,
                   "--fixture", fixture]
        command += ["--room-type", self._room_type]
        if self._horizon is not None:
            # So the Room reports frame latency in absolute terms on exit.
            command += ["--control-horizon", str(self._horizon)]
        process = SimulatorProcess(command, popen=self._popen, record=record)
        process.start()
        teardown.push(f"simulator-{fixture}", process.shutdown)
        self.processes.append(process)
        return dev


class _O2SimulatorFactory:
    """Spawns the Room simulator as an o2lite client rather than a
    websocket one. Reuses harness/o2_shroom.py with --no-join: Control has
    already recorded this dev as the bound Room before the process is
    spawned, so there is no Registration Node to tap -- the same rule
    harness/room_simulator.py follows. Called once per fixture, same as
    _SimulatorFactory."""

    def __init__(self, ensemble: str, *, popen=subprocess.Popen,
                 room_type: str = "TEST") -> None:
        self._ensemble = ensemble
        self._popen = popen
        self._room_type = room_type
        self.processes: list[SimulatorProcess] = []

    def __call__(self, teardown, fixture: str, *, record=None) -> str:
        # -u for the same reason _SimulatorFactory passes it: without it
        # this child's stdout is block-buffered, so its exit report is lost
        # on an ungraceful exit and harness/run_stack.py cannot watch it for
        # readiness markers.
        #
        # --exit-with-parent is what stops this subprocess outliving the
        # Terrarium. An orphan keeps its browser canvas open, reconnects to
        # the next Arco (o2litepy reconnects on its own) and claims this
        # fixture's dev name there, so the NEXT run's simulator for that
        # fixture is refused by O2 and renders nothing. Passing our own pid
        # covers the case teardown cannot: an external SIGKILL of this
        # process.
        dev = sim_dev(fixture)
        process = SimulatorProcess(
            [sys.executable, "-u", "-m", "harness.o2_shroom",
             "--dev", dev, "--ensemble", self._ensemble, "--no-join",
             "--exit-with-parent", str(os.getpid()),
             "--room-type", self._room_type, "--fixture", fixture],
            popen=self._popen, record=record)
        process.start()
        teardown.push(f"simulator-{fixture}", process.shutdown)
        self.processes.append(process)
        return dev


class TerrariumBuildFailure(Exception):
    """A load_room refusal surfaced by build(). Successor to
    control.boot.BootFailure -- boot() (and BootFailure with it) is gone as
    of this Task; build() now drives control.terrarium.Terrarium directly."""


def make_arco_process_cls(arco_popen, settle: float):
    """The harness's ArcoProcess factory: injects the pty popen and the
    post-start settle. Must accept every kwarg Terrarium.load_room passes --
    it threads record= (control/run_record.py) whenever run records are on,
    which is the default; a factory without it fails every live launch."""
    def arco_process_cls(command, *, record=None):
        proc = ArcoProcess(command, popen=arco_popen, record=record)
        if settle <= 0:
            return proc
        started = proc.start

        def start_then_settle():
            started()
            time.sleep(settle)

        proc.start = start_then_settle
        return proc

    return arco_process_cls


def build(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, room_spec=None,
         terrarium_config: TerrariumConfig | None = None,
         host: str = "127.0.0.1",
         port: int = 0, arco_process_cls=ArcoProcess,
         simulator_popen=subprocess.Popen, room_audio=None, transport=None,
         clock=time.monotonic, on_join_denied=None,
         binding_store_path: str | None = None,
         runs_dir: str | None = None, run_id: str | None = None):
    """Construct the whole stack, including a control.terrarium.Terrarium.
    Returns (game_server, devicelink_server, devicelink_agent, arco_process,
    teardown, terrarium).

    room_spec: the Room to load immediately, exactly like the old boot()
    always did -- given, this behaves as before (arco_process is the live
    ArcoProcess once load_room succeeds). None boots the Terrarium to
    NO_ROOM instead and returns arco_process=None: main()'s NO_ROOM wait
    loop is what loads a Room later, via terrarium.load_room() (Console- or
    CLI-driven, see harness/terrarium_boot.py's main()).

    terrarium_config: the full multi-room TerrariumConfig (every room in
    terrarium.toml, not just the one being loaded) -- what the Console's
    room list and resolve_room_spec's "unknown room" listing read. Falls
    back to a single-room config wrapping `room_spec` (or an empty one, if
    room_spec is also None) when omitted, so every existing caller that
    only ever passed room_spec is unaffected.

    room_audio: an already-constructed AudioBridge. Default None builds a
    real one backed by ArcoSynthPool -- lazily imported, exactly like
    harness/arco_synth.py already does elsewhere, so this module costs
    nothing when Arco/pyarco are absent. Tests inject an
    AudioBridge(FakePool()) instead, so no test ever attempts a real pyarco
    import or calls ArcoSynthPool.start() (which FakePool has no equivalent
    of -- it needs no live connection to fake). Audio is unconditionally on
    once real (design spec section 3): there is no --audio-style opt-out.

    transport: an already-adopted O2LiteTransport (see
    devicelink/o2_transport.py), or None for the default websocket
    DeviceLinkServer. o2lite mode has no socket to listen on -- the
    connection is pyarco's, already clock-synced by arco.initialize() and
    started by the caller before this transport was handed in here -- so
    this function never constructs or starts an O2LiteTransport itself.

    clock: threaded straight through to DeviceLinkAgent, whose default is
    the same time.monotonic -- so omitting this argument changes nothing.
    It exists so o2lite mode can hand in o2lite.time_get instead: Control
    stamps every frame's `when` off this clock (agent.py:253), and
    harness/o2_shroom.py ticks its device off the O2 clock, so the two
    must read the same clock or `when` is never reachable -- exactly the
    live-demo bug this parameter fixes. This function still never imports
    o2litepy itself; the caller (main(), only in the --transport o2lite
    branch) resolves o2lite.time_get and hands it in as a plain callable,
    the same way it already hands in the started transport. Also threaded
    into the default (room_audio=None) AudioBridge below, for the same
    reason: DeviceLinkAgent._tick_audio() ticks room_audio against this
    same clock (agent.py), so a welcome cue's due time (set at on_grant,
    against AudioBridge's own clock) and its expiry check (at tick, against
    the agent's) have to agree -- harness/led_smoke.py's own
    AudioBridge(pool, clock=clock) is the existing precedent for this.

    It is ALSO threaded straight into GameServer, because the engine now
    computes every cue's target time and reads this clock both for a
    self-driven cue's origin and for the fallback when a device did not
    stamp its gesture. The engine and the agent must read the same clock or
    a cue's time is unreachable -- the same failure this parameter was added
    to fix, one layer up."""
    teardown = TeardownStack()
    if transport is None:
        server = DeviceLinkServer(host=host, port=port)
        server.start()
        # Pushed BEFORE the Terrarium loads a Room so it is torn down LAST.
        # The Room simulator is a client of this server, and load_room()
        # spawns it, so registration order is what keeps client-before-
        # server true here. This is the process-level stack -- everything
        # a load_room() spawns lives on terrarium.room_stack instead, a
        # SEPARATE stack, so a mid-run unload_room() never touches this one.
        teardown.push("devicelink-server", server.stop)
    else:
        # o2lite mode: there is no socket to listen on. The connection is
        # pyarco's, already clock-synced by arco.initialize(), and the
        # caller started the transport on it -- and therefore the caller
        # registers its teardown, after this function returns, so it stops
        # before everything registered here.
        server = transport

    if transport is None:
        factory = _SimulatorFactory(f"ws://{host}:{server.port}/ws",
                                    popen=simulator_popen,
                                    horizon=config.cue_horizon,
                                    room_type=config.room_name or "")
    else:
        factory = _O2SimulatorFactory(config.o2_ensemble,
                                      popen=simulator_popen,
                                      room_type=config.room_name or "")

    if terrarium_config is None:
        rooms = {room_spec.name: room_spec} if room_spec is not None else {}
        terrarium_config = TerrariumConfig(
            schema=1, name="terrarium-boot", bit_paths=(), rooms=rooms,
            version="terrarium-boot")

    gs = GameServer(bit_registry, room_binding=room_binding,
                    cue_horizon=config.cue_horizon, clock=clock,
                    carried_instruments=terrarium_config.instruments)
    terrarium = Terrarium(
        terrarium_config, gs, room_binding, boot_config=config,
        arco_command=arco_command, arco_process_cls=arco_process_cls,
        simulator_factory=factory, binding_store_path=binding_store_path,
        runs_dir=runs_dir, run_id=run_id)

    if room_spec is not None:
        reason = terrarium.load_room(room_spec.name)
        if reason is not None:
            teardown.close()
            raise TerrariumBuildFailure(reason)
        # Room and Bit loaded together, exactly like the old boot() always
        # did -- every existing build() caller expects gs.state to already
        # be SETUP (via GameServer.load_bit) the instant build() returns
        # with a room_spec. A NO_ROOM build (room_spec is None) loads no
        # Bit either: main() defers that to whenever a Room actually
        # exists (see harness/terrarium_boot.py's main()).
        try:
            bit_cls = bit_registry.get(config.bit_name)
            if bit_cls is None:
                raise TerrariumBuildFailure(f"unknown Bit {config.bit_name!r}")
            if terrarium.room.name not in bit_cls.room_types:
                raise TerrariumBuildFailure(
                    f"Bit {config.bit_name!r} does not support "
                    f"{terrarium.room.name}")
            gs.load_bit(config.bit_name, config=config.bit_config)
        except BitLoadError as exc:
            if terrarium.state is TerrariumState.ROOM_READY:
                terrarium.unload_room(force=True)
            teardown.close()
            raise TerrariumBuildFailure(f"Bit load failed: {exc}") from exc
        except BaseException:
            if terrarium.state is TerrariumState.ROOM_READY:
                terrarium.unload_room(force=True)
            teardown.close()
            raise

    try:
        if room_audio is None:
            from control.audio import AudioBridge
            from harness.arco_synth import ArcoSynthPool
            pool = ArcoSynthPool() if config.arco_soundfont is None \
                else ArcoSynthPool(soundfont=config.arco_soundfont)
            pool.start()
            room_audio = AudioBridge(pool, clock=clock)

        agent = DeviceLinkAgent(gs, server, room_bridge=terrarium.room_bridge,
                                room_audio=room_audio,
                                horizon=config.cue_horizon, clock=clock,
                                on_join_denied=on_join_denied,
                                stale_timeout=config.stale_timeout)
    except BaseException:
        # A loaded Terrarium has already spawned Arco AND the simulator by
        # this point, and main() cannot clean either up: build() never
        # returns, so its `finally: shutdown(...)` has no handle at all.
        # Unwind terrarium.room_stack (via unload_room, if a Room is up)
        # THEN the process-level stack, each guarded so cleanup cannot mask
        # this failure. BaseException so a Ctrl-C during the ArcoSynthPool
        # connect -- which blocks for up to 30s -- is covered too.
        if terrarium.state is TerrariumState.ROOM_READY:
            terrarium.unload_room(force=True)
        teardown.close()
        raise

    return gs, server, agent, terrarium.arco, teardown, terrarium


def shutdown(teardown, terrarium=None, *, pre_room_teardown=None) -> None:
    """Unwind everything, in reverse registration order, and report.

    THREE unwind phases now, in this order:

      1. `pre_room_teardown`, if given -- in o2lite mode this is the o2lite
         transport ONLY. It must close BEFORE Arco: it is Control's own
         o2lite client connection to the SAME Arco hub the Room simulator
         also talks to, and the repo's actual invariant (see
         control/teardown.py's own docstring) is "no client outlives the
         hub it is a guest on", not "Arco last". A client that outlives its
         hub is exactly the PR #24 defect that invariant exists to
         prevent, and the transport is unambiguously a client of Arco here
         -- it must go first, ahead of `terrarium.room_stack` (which is
         where Arco itself lives). None in websocket mode: there is no
         o2lite transport, and the devicelink server (websocket mode's own
         hub, the Room simulator's client target there) belongs on
         `teardown` below, which already closes AFTER the room stack.

         STEADY STATE EXCEPTION: a mid-run `unload_room` (a Console
         `unload_room`, or `_serve_rounds`'/`_serve_roomless`'s own
         "no-room" handling) does NOT close the o2lite transport -- only
         this function, at final process shutdown, does. That is safe
         specifically because the transport is Control's OWN long-lived
         connection to Arco, not scoped to any one Room: pyarco's
         o2lite connection dies with Arco regardless of whether a Room is
         currently loaded, so there is nothing for an intermediate
         unload_room to orphan by leaving the transport running across a
         Room cycle -- it simply keeps serving Control's own o2lite
         traffic (registration, non-Room devices) until the next
         load_room, or until this function finally stops it.

      2. `terrarium.room_stack`, via terrarium.unload_room(force=True) --
         only if a Room is actually loaded (ROOM_READY). Its own sequence
         tears down the Bit, then the Room bridge (which frees the Room's
         Arco voice), then the Room simulator subprocess, then Arco.
         force=True mirrors the old single-stack shutdown()'s behavior: a
         still-RUNNING Bit is aborted on the way down rather than refusing
         to tear down.

      3. The process-level `teardown` this module owns (the devicelink
         server in websocket mode, the console) -- neither has a hub
         dependency on the room stack (the devicelink server IS websocket
         mode's hub, and closes after its own clients precisely because it
         was pushed onto `teardown` first, at build() time, and torn down
         last by this stack's own LIFO order), so ordering it after phase 2
         is safe either way.
    """
    if pre_room_teardown is not None:
        for name, exc in pre_room_teardown.close():
            print(f"teardown step {name!r} failed: {exc!r}", file=sys.stderr)
    if terrarium is not None and terrarium.state is TerrariumState.ROOM_READY:
        reason = terrarium.unload_room(force=True)
        if reason is not None:
            print(f"unload_room on shutdown failed: {reason}", file=sys.stderr)
    for name, exc in teardown.close():
        print(f"teardown step {name!r} failed: {exc!r}", file=sys.stderr)


def _wait_in_setup(agent, setup_seconds: float, clock=time.monotonic,
                   sleep=time.sleep, parent_pid: int | None = None,
                   console_agent=None, arco=None, gs=None,
                   condition: StartCondition | None = None,
                   game_server=None, announce_swaps: bool = False) -> str:
    """Poll the transport for setup_seconds while the Bit sits in SETUP, so
    a device can join a scored role before run() closes the window.
    registration.join() refuses scored roles once RUNNING
    (control/registration.py:41-42), and TestBit's `player` is scored, so
    without this window harness/o2_shroom.py is denied every time.
    setup_seconds <= 0 -- the default -- returns immediately, preserving the
    existing load-straight-into-run behavior. Same shape as
    harness/devicelink_smoke.py's _wait_in_setup.

    parent_pid, when given, is checked every tick via
    harness/o2_shroom.py's parent_is_gone -- see F5 in the final review
    for why this reuses that predicate rather than a second one. A
    SIGKILLed or OOM-killed run_stack cannot signal this process, so the
    only way to notice is to keep asking. Returns "parent-gone" if that
    fired, so main() can skip straight to shutdown() instead of calling
    gs.run() into a stack whose supervisor is already gone.

    console_agent, when given, is polled once per iteration too -- a device
    joining during SETUP is exactly what the console's registration view
    exists to show live.

    arco, when given, is drained every iteration. Every loop that holds
    while Arco is alive must drain Arco's pty: Arco is a curses app, an
    undrained pty blocks it mid-write, and a blocked Arco serves no clock
    sync, routes no messages and plays no audio. This loop not draining
    it froze whole rooms for the length of the hold (2026-08-20).

    gs, when given, is watched: the Console is a second driver, and if
    the operator moves the engine out of SETUP (Run, Abort) this hold
    yields immediately instead of letting main() call run() into a
    RUNNING engine.

    condition, when given together with game_server, is consulted once per
    iteration via control.start_condition.start_decision (scored count read
    off game_server via scored_count). "players" conditions distinguish a
    genuinely-met threshold ("players-met") from a timeout resolution
    ("timeout-start"/"timeout-abort") by checking the same scored>=min_scored
    test start_decision itself prioritizes -- see control/start_condition.py.
    An "immediate" condition's own elapsed>=setup_seconds threshold is the
    same instant as this function's own deadline, so "expired" always wins
    that race; a "players"/"operator" condition never produces "expired".

    announce_swaps, when true, prints `markers.CONTROL_ROUND_LOADED` for a
    Bit swapped in mid-hold by one console_agent.poll() carrying both an
    Abort and a LoadBit (SETUP -> IDLE -> SETUP with a new bit_name, never
    visible as a state change from outside). `_serve_rounds` always passes
    True (every serve-mode round announces itself); main()'s own round-1
    call passes `effective_serve` (a --console-port one-shot run still
    constructs a console_agent, so the swap can still happen there, but
    one-shot mode must announce nothing -- same gating as the other two
    CONTROL_ROUND_LOADED emit sites). The "state-changed" return itself
    always fires either way regardless of the flag, so the caller's
    handoff handling (gs.run() or hand off) is unaffected by it.

    Returns "expired", "parent-gone", "state-changed", "players-met",
    "timeout-start", or "timeout-abort".
    """
    if setup_seconds <= 0:
        return "expired"
    initial_bit_name = getattr(gs, "bit_name", None) if gs is not None else None
    start = clock()
    deadline = start + setup_seconds
    next_countdown = start + 15.0
    while True:
        now = clock()
        if now >= deadline:
            return "expired"
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if arco is not None:
            arco.poll()
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
        if gs is not None and gs.state is not State.SETUP:
            return "state-changed"
        if gs is not None and getattr(gs, "bit_name", None) != initial_bit_name:
            # A mid-hold operator Abort+LoadBit both queued for this single
            # console_agent.poll() lands gs back in SETUP with a NEW
            # bit_name in one step -- the state check just above never
            # observes SETUP leave SETUP, so it alone would let the
            # swapped-in Bit run with no "round loaded:" line at all
            # (round-review 2026-08-24 finding). This is the one place
            # that ever sees both the old and new bit_name, so it is the
            # handoff site: print the single line main()/`_serve_rounds`
            # would otherwise have missed for this round, then hand off
            # exactly like any other state-changed exit. The detection
            # itself runs unconditionally -- the swap is real and the
            # handoff still has to happen in one-shot mode too -- but the
            # print is gated on announce_swaps, same as the two other
            # CONTROL_ROUND_LOADED emit sites (main()'s round-1 line and
            # `_serve_rounds`'s own), so a --console-port one-shot run
            # (effective_serve False, but still with a console_agent)
            # never gets a "round loaded:" line one-shot mode never
            # promised.
            if announce_swaps:
                print(f"{markers.CONTROL_ROUND_LOADED} {gs.bit_name}",
                     flush=True)
            return "state-changed"
        if condition is not None and game_server is not None:
            scored = scored_count(game_server)
            decision = start_decision(condition, scored=scored,
                                      elapsed=now - start,
                                      setup_seconds=setup_seconds)
            if decision is not None:
                if condition.when == "players" and scored >= condition.min_scored:
                    return "players-met"
                if decision == "start":
                    return "timeout-start"
                if decision == "abort":
                    return "timeout-abort"
        if now >= next_countdown:
            print(f"SETUP open, {deadline - now:.0f}s remaining", flush=True)
            next_countdown = now + 15.0
        sleep(1.0 / 44.0)


def _serve_until_done(gs, agent, arco, clock=time.monotonic,
                      sleep=time.sleep, parent_pid: int | None = None,
                      console_agent=None) -> str:
    """Tick until the Bit finishes, Arco dies, or the parent is gone.
    Returns the reason.

    Three exit conditions, all deliberate:

    "completed" -- the Bit signalled done from update(dt), so the engine
    ran COMPLETING/UNLOADING synchronously and state is back to IDLE. The
    loop then keeps polling until the transport has no devices still
    closing, because release is ASYNCHRONOUS: a released device renders its
    closing fade over several ticks and only then receives /<dev>/release.
    Exiting the instant state hit IDLE would freeze every device on its
    last frame.

    "arco-exited" -- the Arco subprocess is gone. Fail loud: silent
    degradation in a venue is worse than a visible stop.

    "parent-gone" -- run_stack, which passes --exit-with-parent, is no
    longer this process's parent (SIGKILLed or OOM-killed). Checked here,
    not left to fall out of some other symptom, so the exit runs through
    this function's caller's `finally: shutdown(teardown)` instead of
    leaving Arco and the Room simulator running un-signalled in their own
    session -- the orphan class docs/upstream/2026-08-14-o2-service-and-
    discovery-report.md names as a venue-scale hazard. See
    harness/o2_shroom.py's parent_is_gone for why this compares against a
    recorded pid rather than watching getppid() for a change.

    console_agent, when given, is polled once per tick too, right after
    agent.poll() -- the same tick loop, so the console's picture of the run
    (bit status, Room frames, registration) is never more than one tick
    stale.
    """
    while True:
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if arco.poll() is not None:
            return "arco-exited"
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
        gs.tick(1.0 / 44.0)
        if gs.state == State.IDLE and not getattr(agent, "closing", 0):
            return "completed"
        sleep(1.0 / 44.0)


def _wait_for_load(gs, agent, arco, *, clock=time.monotonic,
                   sleep=time.sleep, parent_pid: int | None = None,
                   console_agent=None, terrarium=None) -> str:
    """Hold in IDLE until a console `load_bit` moves the engine out of it --
    the between-rounds counterpart to `_wait_in_setup`'s in-SETUP hold.

    Ticks agent/console/gs exactly like `_serve_until_done`'s loop body
    (parent-gone check, arco liveness, agent.poll(), console_agent.poll(),
    gs.tick()) so a starved Arco pty can never freeze this wait either --
    see `_serve_until_done`'s docstring for why that drain is load-bearing.

    Returns "loaded" the instant `gs.state` leaves IDLE (a console
    `load_bit` ran) -- immediately, with no tick at all, if the engine is
    already out of IDLE on entry (the first, CLI-selected round: main()
    has already loaded a Bit before this is ever called). Returns
    "parent-gone" or "arco-exited" on those conditions, same as
    `_serve_until_done`.

    terrarium, when given, is checked FIRST every iteration -- ahead of the
    arco liveness check -- and returns "no-room" the instant it is no
    longer ROOM_READY (a Console `unload_room` landed while this held
    IDLE). Checking it first matters: unload_room(force=True) has already
    shut down `arco` by the time this notices, so `arco.poll()` alone would
    misreport an operator-driven unload as "arco-exited". `_serve_rounds`
    threads this straight through so a --room-less serve loop returns to
    the NO_ROOM wait rather than treating an intentional unload as a crash.
    """
    if gs.state is not State.IDLE:
        return "loaded"
    while True:
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if terrarium is not None and terrarium.state is not TerrariumState.ROOM_READY:
            return "no-room"
        if arco.poll() is not None:
            return "arco-exited"
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
        gs.tick(1.0 / 44.0)
        if gs.state is not State.IDLE:
            return "loaded"
        sleep(1.0 / 44.0)


def _serve_rounds(gs, agent, arco, *, parent_pid: int | None = None,
                  console_agent=None, drain_arco=None, terrarium=None,
                  recycle=None) -> str:
    """The `--serve` round loop: load, hold, run, complete, repeat -- until
    the parent or Arco disappears. Each iteration is one round:

      1. `_wait_for_load` -- sit in IDLE until the Console loads a Bit.
      2. Read that round's own start condition and setup window off
         `gs.bit.config` (a console `load_bit` resolves overrides into this
         same BitConfig shape main() reads for round 1 -- see
         console.agent.ConsoleAgent). A Bit with no config (None) gets an
         immediate 0-second window, same as an unconfigured manifest.
      3. `_wait_in_setup` with that condition/window -- identical hold to
         round 1's, including its pty-drain and operator-driven
         state-change escape.
      4. "timeout-abort" -- the operator (or nobody) never met the start
         condition: `gs.abort()` and go straight to the next round rather
         than running an unmet Bit. Otherwise `gs.run()`, but ONLY if the
         engine is still in SETUP -- the same operator-handoff guard
         main() applies to round 1, since the Console is a second driver
         that can move the engine on its own during the hold.
      5. `_serve_until_done` -- run to completion. "completed" (which
         covers an in-round operator abort too -- both land back in IDLE)
         loops back to step 1 for the next round.
      6. Recycle the Room (`recycle`, when given) at every round end --
         both the timeout-abort branch and a "completed" round -- the
         bit-cycle rule: a fresh Arco per Bit. A recycle failure bubbles
         out of this loop as "no-room", same outcome as a Console
         unload_room; `arco` is re-read from `terrarium.arco` on success
         since the recycle replaced the process.

    `drain_arco`, when given, is called once per iteration of the setup
    and run legs in addition to `arco.poll()` -- an extra pty-drain hook
    for callers whose Arco handle needs draining separately from its
    liveness check. Unused by the two callers `arco.poll()` already
    covers both concerns for.
    """
    def _end_round(bit_name, reason_text: str) -> str | None:
        """Announce the round's outcome, THEN recycle the room (bit-cycle
        rule). Announcing first means the "round ended:" marker and console
        event are on the wire before the console shows recycle progress
        stages -- see markers.CONTROL_ROUND_ENDED. Returns "no-room" to
        bubble as this loop's outcome when the recycle fails, else None.
        Re-reads terrarium.arco: the recycle replaced the process, and the
        liveness checks above must watch the new one, not a handle to the
        SIGTERMed old one."""
        nonlocal arco
        print(f"{markers.CONTROL_ROUND_ENDED} {bit_name} ({reason_text})",
             flush=True)
        if console_agent is not None:
            console_agent.announce_round_ended(bit_name, reason_text)
        if recycle is None:
            return None
        reason = recycle()
        if reason is not None:
            print(f"room recycle failed: {reason}", file=sys.stderr)
            return "no-room"
        if terrarium is not None and terrarium.arco is not None:
            arco = terrarium.arco
        return None

    while True:
        was_idle = gs.state is State.IDLE
        reason = _wait_for_load(gs, agent, arco, parent_pid=parent_pid,
                                console_agent=console_agent,
                                terrarium=terrarium)
        if reason != "loaded":
            return reason
        if was_idle:
            # Only a round _wait_for_load actually watched leave IDLE gets
            # announced here -- the immediate-return case (state already
            # out of IDLE on entry) is round 1's CLI-selected Bit, which
            # main() has already announced once before calling in here.
            print(f"{markers.CONTROL_ROUND_LOADED} {gs.bit_name}",
                 flush=True)

        # Captured now: gs.bit_name is None again by the time this round
        # ends (the engine clears it on unload), so this is the only local
        # that can still name the round at _end_round() time.
        bit_name = gs.bit_name

        cfg = getattr(gs.bit, "config", None)
        cond = cfg.start if cfg else None
        setup = cfg.launch.setup_seconds if cfg else 0.0

        reason = _wait_in_setup(agent, setup, parent_pid=parent_pid,
                                console_agent=console_agent, arco=arco,
                                gs=gs, condition=cond, game_server=gs,
                                announce_swaps=True)
        if reason == "parent-gone":
            return reason
        if reason == "timeout-abort":
            scored = scored_count(gs)
            gs.abort()
            outcome = _end_round(
                bit_name, f"timeout-abort ({scored} scored joined)")
            if outcome:
                return outcome
            continue
        if gs.state is State.SETUP:
            gs.run()
        # else: the operator already drove the engine from the Console
        # during the hold -- a handoff, not an error (same guard main()
        # applies to round 1).

        reason = _serve_until_done(gs, agent, arco, parent_pid=parent_pid,
                                   console_agent=console_agent)
        if reason in ("parent-gone", "arco-exited"):
            return reason
        outcome = _end_round(bit_name, "completed")
        if outcome:
            return outcome
        print("round complete; waiting for next load", flush=True)


def _wait_for_room_ready(agent, terrarium, *, console_agent=None,
                         parent_pid: int | None = None,
                         sleep=time.sleep) -> str:
    """The NO_ROOM idle loop: main() falls in here when it booted with no
    --room. Polls the transport and the console at ~20 Hz until the
    Console loads a Room (terrarium.state becomes ROOM_READY) -- no Arco
    to drain here, unlike every other loop in this module: none is up yet
    in NO_ROOM, so the pty-drain discipline `_wait_in_setup`'s docstring
    describes is moot until a Room actually loads. Returns "ready" the
    instant ROOM_READY is observed -- immediately, with no poll at all, if
    it already is on entry (a respawned wait after `_serve_roomless`'s own
    inner `_serve_rounds` call returns "no-room") -- or "parent-gone"."""
    if terrarium.state is TerrariumState.ROOM_READY:
        return "ready"
    while True:
        if parent_is_gone(parent_pid):
            return "parent-gone"
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
        if terrarium.state is TerrariumState.ROOM_READY:
            return "ready"
        sleep(1.0 / 20.0)


def _serve_roomless(gs, agent, terrarium, *, console_agent=None,
                    parent_pid: int | None = None, recycle=None,
                    restart_clients=None) -> str:
    """main()'s top-level loop for a NO_ROOM boot (no --room given): wait
    for the Console to load a Room (`_wait_for_room_ready`), then serve
    rounds against whatever that load produced
    (`_serve_rounds(terrarium=terrarium)` -- the same round machinery a
    --room launch falls into after its own CLI-selected round 1), looping
    back to the NO_ROOM wait whenever `_serve_rounds` returns "no-room" (a
    Console `unload_room` mid-serve) instead of treating that as this
    process's terminal outcome. `terrarium.arco` is re-read fresh on every
    lap -- it is None in NO_ROOM and only becomes the live ArcoProcess once
    `_wait_for_room_ready` returns "ready".

    `restart_clients`, when given, is called on every "ready" lap -- a
    no-op unless the room got here via a plain Console `load_room` that
    followed a FAILED `recycle()`: that recycle already stopped Control's
    own transport/pool (client-before-hub) but had no hub left to restart
    them against, so without this a later successful load_room would land
    in ROOM_READY with a live Arco but dead clients, silently, for the
    rest of the process. If the restart itself fails, this Room is
    unloaded (so ROOM_READY never lies about live clients) and the lap
    returns to the NO_ROOM wait instead of serving."""
    while True:
        reason = _wait_for_room_ready(agent, terrarium,
                                      console_agent=console_agent,
                                      parent_pid=parent_pid)
        if reason != "ready":
            return reason
        if restart_clients is not None:
            restart_reason = restart_clients()
            if restart_reason is not None:
                print(f"room client restart failed: {restart_reason}",
                     file=sys.stderr)
                terrarium.unload_room(force=True)
                continue
        reason = _serve_rounds(gs, agent, terrarium.arco,
                               parent_pid=parent_pid,
                               console_agent=console_agent,
                               terrarium=terrarium, recycle=recycle)
        if reason != "no-room":
            return reason


def _run_duration(args) -> float | None:
    """Same shape as harness/devicelink_smoke.py's _run_duration: --hold
    wins over --seconds. Unlike that sibling, a bare invocation (neither
    flag given) now returns None rather than a hardcoded fallback -- main()
    only adds a `defaults.run_duration_seconds` override when this returns
    a value, so an unrequested run leaves the manifest's own default (or,
    absent one, the Bit's own hardcoded fallback -- TestBit's is still
    RUN_DURATION_SECONDS) untouched."""
    if args.hold:
        return float("inf")
    return args.seconds


class _LifecycleLogger:
    """Prints the device lifecycle (hello / join granted / released) to
    Control's stdout. Registered via gs.add_observer(), the same
    multi-observer seam console.agent.ConsoleAgent already rides -- so it
    sees exactly the same on_devices_change/on_registration_change payload
    shapes ConsoleAgent does (there is no per-call payload at all; both
    hooks are pure "something changed, go re-read gs" signals).

    Denials never reach this seam: GameServer.join() returns a refused
    JoinResult without ever touching registration state, so neither hook
    fires for a deny. Those print via DeviceLinkAgent's own on_join_denied
    sink instead -- see _print_join_denied below.

    Derivation:
      - "device hello: <dev>" -- a dev appearing in gs.devices.all() that
        was not there the last time on_devices_change fired.
      - "device timed out: <dev>" -- the reverse: a dev that WAS in
        gs.devices.all() last time and is gone now. The only thing that
        removes a DevicePool entry is GameServer.reap_stale() (control/
        device_pool.py, control/engine.py), called every tick from
        DeviceLinkAgent.poll() -- so this line is unambiguous: it always
        means a device went silent past BootConfig.stale_timeout, never a
        graceful release (that only ever empties gs.registration.
        assignments, diffed separately below as "device released"). A
        timed-out player that held a role prints BOTH lines: "released"
        from the assignments diff and "timed out" from this one, which is
        accurate -- both things happened.
      - "join granted: <dev> -> <role> (<category>) via <node>" -- a dev
        whose (node, role, role_class) tuple in gs.registration.assignments
        is new or changed since the last on_registration_change (a role
        switch -- re-tapping a different node -- changes the tuple without
        the dev ever leaving assignments, and must still print).
      - "device released: <dev>" -- a dev that HAD an assignments entry
        last time but has none now. control/engine.py's on_release is a
        single transport-owned sink (already claimed by DeviceLinkAgent for
        the /release wire message), not a multi-observer event, so release
        can't ride that hook. GameServer._unload() releases every assigned
        device via registration.release_all() and notifies ONLY
        on_devices_change afterward (never on_registration_change) -- see
        engine.py's _unload(). So this diff runs in on_devices_change,
        against the assignments snapshot on_registration_change last left
        behind.

    A Room join (role_class ROOM) never reaches either hook's assignments
    diff as a grant: GameServer.join() returns before notifying
    on_registration_change for those (control/engine.py's _bind_room()
    notifies on_devices_change only), the same exclusion
    ConsoleAgent._non_room_counts() applies to the registration panel.
    """

    def __init__(self, game_server) -> None:
        self._gs = game_server
        self._last_devices: set = set()
        self._last_assignments: dict = {}

    def _current_assignments(self) -> dict:
        registration = self._gs.registration
        return dict(registration.assignments) if registration is not None else {}

    def on_devices_change(self) -> None:
        current_devs = {info.dev for info in self._gs.devices.all()}
        for dev in current_devs - self._last_devices:
            print(f"device hello: {dev}", flush=True)
        for dev in self._last_devices - current_devs:
            print(f"device timed out: {dev}", flush=True)
        self._last_devices = current_devs

        current_assignments = self._current_assignments()
        for dev in self._last_assignments:
            if dev not in current_assignments:
                print(f"device released: {dev}", flush=True)
        self._last_assignments = current_assignments

    def on_registration_change(self) -> None:
        registration = self._gs.registration
        current_assignments = self._current_assignments()
        for dev, value in current_assignments.items():
            if value == self._last_assignments.get(dev):
                continue
            node, role_name, role_class = value
            role = registration.role_table.roles[role_name]
            category = "scored" if role.scored else role_class.name.lower()
            print(f"join granted: {dev} -> {role_name} ({category}) "
                  f"via {node}", flush=True)
        self._last_assignments = current_assignments


class _TerrariumLogger:
    """Prints control.terrarium.Terrarium's Room lifecycle to Control's
    stdout: a `room loading: <stage>` line per progress notification (NOT
    a marker -- a variable count per load, same reasoning as BROWSE_URL's
    own), and the two markers that bookend a load --
    markers.CONTROL_ROOM_LOADED once ROOM_READY, markers.CONTROL_ROOM_UNLOADED
    once back to NO_ROOM from a Room that was actually loaded (never for
    the process's own boot-time NO_ROOM start).

    Registered on `terrarium` right after main() gets it back from build(),
    so it is live for every CONSOLE-driven load/unload from then on. A
    CLI-selected round-1 Room (--room) loads INSIDE build(), before this
    observer exists to see it -- main() prints that one round's
    CONTROL_ROOM_LOADED line itself, right after build() returns, the same
    pattern harness/terrarium_boot.py already used for
    markers.CONTROL_ROUND_LOADED's own round-1 line. `_last_room_name` is
    seeded from `terrarium.room` at construction for exactly that reason:
    a LATER unload of that same CLI-loaded Room must still have a name to
    print, despite this observer having missed the load itself.
    """

    def __init__(self, terrarium) -> None:
        self._terrarium = terrarium
        self._last_room_name = (
            terrarium.room.name if terrarium.room is not None else None)

    def on_room_load_progress(self, stage: str) -> None:
        print(f"room loading: {stage}", flush=True)

    def on_terrarium_state_change(self, old_state, new_state) -> None:
        if new_state is TerrariumState.ROOM_READY:
            self._last_room_name = self._terrarium.room.name
            print(f"{markers.CONTROL_ROOM_LOADED} {self._last_room_name}",
                 flush=True)
        elif (new_state is TerrariumState.NO_ROOM
              and old_state is TerrariumState.ROOM_UNLOADING):
            print(f"{markers.CONTROL_ROOM_UNLOADED} {self._last_room_name}",
                 flush=True)


class _RoomWiring:
    """Keeps `agent` (a devicelink.agent.DeviceLinkAgent) in sync with
    Terrarium's Room lifecycle for every load/unload AFTER construction.

    build() already wires a CLI-selected round-1 Room (--room) correctly:
    it loads the Room (and the Bit) before ever constructing `agent`, so
    DeviceLinkAgent's own __init__-time _setup_room() sees a live gs.room
    and needs no help. This observer exists for the case build() cannot
    cover -- a NO_ROOM boot, where `agent` is constructed with
    room_bridge=None because no Room exists yet. Left unwired, a Room that
    loads later (a Console `load_room`) would never get a light session,
    an audio grant, or a bound MIDI bridge, and console.agent.ConsoleAgent's
    own controllers read-out (which now reads terrarium.room_bridge live,
    see its _current_room()) would have nothing live to read either --
    see devicelink.agent.DeviceLinkAgent.rewire_room's own docstring for
    the full picture."""

    def __init__(self, agent, terrarium) -> None:
        self._agent = agent
        self._terrarium = terrarium

    def on_terrarium_state_change(self, old_state, new_state) -> None:
        if new_state is TerrariumState.ROOM_READY:
            self._agent.rewire_room(self._terrarium.room_bridge)
        elif new_state is TerrariumState.NO_ROOM:
            self._agent.unwire_room()


def _print_join_denied(dev: str, node: str, reason: str) -> None:
    """DeviceLinkAgent's on_join_denied sink (see devicelink/agent.py's
    _notify_join_denied, called at the deny reply's send site and guarded
    there so a raise here can never cost the device its /deny reply).
    Denials never touch registration state, so _LifecycleLogger's
    engine-observer seam above never sees them -- this is Control's only
    stdout line for a denial.

    Lowercase "join denied:" is deliberate and load-bearing: the DEVICE-side
    marker is harness/markers.py's DEVICE_JOIN_DENIED = "JOIN DENIED:", and
    harness/run_stack.py matches markers as plain substrings of a child's
    stdout. Uppercasing this line to "match" would make Control's own
    stdout satisfy the device's readiness marker and desync run_stack's
    bookkeeping -- keep the casing apart."""
    print(f"join denied: {dev} -> {node} ({reason})", flush=True)


def _recycle_room(terrarium, *, transport=None, pool=None, o2lite=None):
    """Recycle the active Room with Control's own Arco clients handled in
    the only survivable order. Control is a client of the hub it is about
    to kill, twice over in o2lite mode: the O2LiteTransport (game/actl on
    pyarco's o2lite singleton) and the ArcoSynthPool (a Flsyn and voices
    on the dying Arco). Client-before-hub (control/teardown.py's
    invariant) demands both stop BEFORE the unload; the relaunch mirrors
    process launch order -- pool.start() first (arco.initialize() blocks
    until clock sync with the NEW hub), then transport.start(o2lite)
    (which asserts a synced clock and re-claims actl,game).

    Returns None on success, else the reason string (never raises). On
    failure the restarts are skipped: there is no hub to restart against,
    and the caller (the serve-round loop) treats the reason like a
    Console unload_room -- back to the NO_ROOM wait.

    Websocket mode passes transport=None (the devicelink server is
    process-scoped, not an Arco client); pool applies in both modes
    (audio is unconditionally on)."""
    if transport is not None:
        transport.stop()
    if pool is not None:
        pool.quiesce()
    reason = terrarium.recycle_room()
    if reason is not None:
        return reason
    return _restart_room_clients(transport=transport, pool=pool,
                                 o2lite=o2lite)


def _restart_room_clients(*, transport=None, pool=None,
                          o2lite=None) -> str | None:
    """The restart half of `_recycle_room` (pool.start() then
    transport.start(o2lite), process-launch order -- see `_recycle_room`'s
    docstring), factored out so `_serve_roomless` can also call it after a
    plain Console `load_room` that follows a FAILED recycle: that recycle
    already stopped these same clients (client-before-hub) but had no hub
    to restart them against, so a later successful load lands in
    ROOM_READY with a live Arco but Control's own clients still down
    unless something restarts them here.

    Unlike Terrarium's own methods, `pool.start()`/`transport.start()`
    actually raise on failure, so this wraps them and stringifies the
    exception -- callers get the same "reason string, never raises"
    contract `_recycle_room` promises."""
    try:
        if pool is not None:
            pool.start()
        if transport is not None:
            transport.start(o2lite)
    except Exception as exc:
        return str(exc)
    return None


def _register_o2lite_transport(teardown, transport) -> None:
    """Push the o2lite transport's teardown -- exactly where main() calls
    this, right after transport.start(o2lite) has actually adopted the
    connection, and after build() has already registered arco, the
    simulator, the room bridge and the Bit.

    Pushed LAST of the steps this run registers, so it tears down FIRST:
    stopping the transport before Arco, whose hub it is a guest on, is the
    client-before-hub property one layer up from build()'s own -- see
    shutdown()'s docstring for the full order.

    Extracted so tests/test_terrarium_boot.py can assert this step's
    position without driving the whole of main(), which needs argparse, a
    live Arco, and o2litepy.
    """
    teardown.push("o2lite-transport", transport.stop)


def _effective_serve(args) -> bool:
    """`--serve` OR ("--console-port with neither --seconds nor --hold").
    `--hold`/`--seconds` are bounded/one-shot intents -- a console with
    neither implies rounds instead. `harness/run_stack.py --ci` never
    passes `--serve` (its one-shot semantics are unchanged), and this
    process itself has no --ci."""
    return bool(args.serve or (args.console_port is not None
                               and args.seconds is None and not args.hold))


def _print_round_outcome(reason: str) -> None:
    """The one shared tail message for however the run(-or-rounds) ended --
    factored out so both the one-shot path and the `--serve` path (which
    may have looped through several rounds via `_serve_rounds` before
    landing here) print it exactly once."""
    if reason == "arco-exited":
        print("Arco exited; tearing down", file=sys.stderr)
    elif reason == "parent-gone":
        print("parent is gone; tearing down", file=sys.stderr)
    elif reason == "completed":
        print(markers.CONTROL_BIT_COMPLETED)


def _build_arg_parser():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--seconds", type=float, default=None,
                    help="How long the Bit stays RUNNING before completing.")
    ap.add_argument("--hold", action="store_true",
                    help="Never auto-complete; run until Ctrl-C.")
    ap.add_argument("--arco-command", default="/Users/chris/projects/arco/apps/pytest/server")
    ap.add_argument("--arco-start-audio", action="store_true",
                    help="After boot, press Arco's (S)tart key to re-open "
                         "its audio devices. Needs --arco-pty (that is what "
                         "owns Arco's console). Works around an UPSTREAM "
                         "defect measured 2026-08-14: pyarco's "
                         "arco.initialize() sends /host/clear, which tears "
                         "down Arco's audio stream, and Arco then stops "
                         "serving O2 clock sync to any client connecting "
                         "AFTERWARD -- a device hangs forever in o2_shroom's "
                         "'while o2lite.time_get() < 0' loop while Control, "
                         "already synced, looks perfectly healthy. Pressing "
                         "(S)tart re-opens audio and sync works again. OFF "
                         "by default because (S)tart/Stop is a toggle Arco "
                         "gives no way to read, so pressing it on a healthy "
                         "server STOPS audio instead.")
    ap.add_argument("--arco-settle-seconds", type=float, default=0.0,
                    help="Pause between spawning Arco and first probing it. "
                         "The readiness probe is DESTRUCTIVE: pyarco's "
                         "arco.initialize() unconditionally calls reset(), "
                         "which sends /host/clear and tears down the "
                         "server's audio stream. One probe is survivable; a "
                         "probe that FAILS and retries adds another reset, "
                         "and the extra teardown can leave arco.output None "
                         "-- ArcoSynthPool.start() then dies with "
                         "\"'NoneType' object has no attribute 'ins'\". "
                         "Settling first makes probe #1 succeed, so only one "
                         "reset ever happens. Default 0 keeps existing "
                         "behavior.")
    ap.add_argument("--arco-ready-timeout", type=float, default=None,
                    help="Override BootConfig.arco_ready_timeout (15 s). "
                         "The FIRST readiness probe against a cold Arco can "
                         "take ~18 s -- it connects, then pyarco's reset() "
                         "times out after 5 s ('Could not reset Arco server "
                         "within 5 seconds') -- while the second attempt "
                         "succeeds instantly. When that happens the 15 s "
                         "budget expires inside probe #1 and boot fails with "
                         "ArcoReadyTimeout even though Arco is up and fine. "
                         "Raise this if you see that.")
    ap.add_argument("--arco-pty", action="store_true",
                    help="Spawn Arco on a pty with its own controlling "
                         "terminal, so its curses init can open /dev/tty "
                         "from a non-interactive context (CI, cron, an "
                         "agent-driven measurement run). Off by default: an "
                         "interactive terminal already provides one.")
    ap.add_argument("--arco-log", default=None, metavar="PATH",
                    help="Tee Arco's console output to this file. Needs "
                         "--arco-pty (that is what owns Arco's stdio). "
                         "Without it Arco's output is drained into memory "
                         "and discarded, which makes 'Arco never came up' "
                         "the least diagnosable failure in the stack.")
    ap.add_argument("--horizon", type=float, default=None,
                    help="Cue scheduling horizon in seconds. Default: "
                         "BootConfig.cue_horizon. Measure with "
                         "python -m harness.sync_bench.")
    ap.add_argument("--stale-timeout", type=float, default=None,
                    help="Override BootConfig.stale_timeout (15 s). A "
                         "device silent this long -- no /game/hello, no "
                         "gesture, nothing -- is removed from DevicePool "
                         "and, if it held one, its role slot freed. Paired "
                         "with the harness device clients' own "
                         "--heartbeat-interval (default 5 s each): three "
                         "missed heartbeats before a reap.")
    ap.add_argument("--transport", choices=("websocket", "o2lite"),
                    default="websocket",
                    help="websocket: the JSON devicelink shim, no Arco in "
                         "the device path. o2lite: real O2 through the Arco "
                         "hub, which requires a running Arco server.")
    ap.add_argument("--setup-seconds", type=float, default=None,
                    help="Hold the Bit in SETUP for this long before "
                         "run(), so a device can join a scored role (e.g. "
                         "TEST_PLAYER_NODE) before registration closes for "
                         "it. Default: the selected Bit manifest's "
                         "launch.setup_seconds (0.0 keeps the instant-run "
                         "behavior).")
    ap.add_argument("--exit-with-parent", type=int, default=None,
                    metavar="PID",
                    help="Exit through the normal shutdown() teardown path "
                         "as soon as this process's parent is no longer "
                         "PID. harness/run_stack.py passes its own pid: "
                         "without this, a SIGKILLed or OOM-killed run_stack "
                         "leaves this process running, and with it Arco "
                         "and the Room simulator, un-signalled in their own "
                         "session -- this process has always been started "
                         "by a human before, so it had no equivalent of "
                         "o2_shroom's --exit-with-parent. Off by default: "
                         "a hand-run terrarium_boot is unchanged.")
    ap.add_argument("--console-port", type=int, default=None,
                    help="Serve the Terrarium Console on this port. Off by "
                         "default, so an existing invocation is unchanged. "
                         "Binds --host, which defaults to 127.0.0.1: the "
                         "console is unauthenticated and trusted-LAN only.")
    ap.add_argument("--config", default="terrarium.toml", metavar="PATH",
                    help="The terrarium.toml this run boots against -- its "
                         "[rooms.<NAME>] tables are the valid --room values. "
                         "Default: terrarium.toml in the current directory.")
    ap.add_argument("--room", default=None, metavar="NAME",
                    help="Which Room (a [rooms.<NAME>] table in --config) to "
                         "load. Unknown names exit with a located error "
                         "listing every room --config actually defines. "
                         "Omitted together with --console-port: this process "
                         "boots to NO_ROOM and waits for the Console to load "
                         "one instead of loading anything itself. Omitted "
                         "with no console: an error, since nothing would "
                         "ever load a Room. Default: the selected Bit "
                         "manifest's launch.default_room_type.")
    ap.add_argument("--bit", default=None,
                    help="Which Bit to run, by its discovered manifest name "
                         "(bits/*/bit.toml). build() already fails loud if "
                         "the resolved Room is not in the chosen Bit's "
                         "room_types. See --list-bits for what's available. "
                         "Default: the --profile's own [run].bit, else "
                         "TestBit.")
    ap.add_argument("--profile", default=None, metavar="PATH",
                    help="A venue TOML (see profiles/dev-metronome.toml) "
                         "supplying launch defaults -- bit, room_type, "
                         "devices, console_port, seconds -- and a "
                         "[bit.overrides] table merged under this "
                         "process's own CLI-derived overrides (setup_seconds, "
                         "run duration). Precedence is manifest < profile < "
                         "explicit CLI flags.")
    ap.add_argument("--list-bits", action="store_true",
                    help="Print every discovered Bit package (name, "
                         "version, kind, room types, start condition, "
                         "description) and any manifest errors, then exit.")
    ap.add_argument("--runs-dir", default="runs", metavar="PATH",
                    help="Where this run's owned-pid record (procs.jsonl, "
                         "including its own supervisor entry) is written, "
                         "and where load_room's stale-sweep guardrail "
                         "looks for a crashed prior run to clean up "
                         "(design spec section 5). Same runs/<timestamp> "
                         "convention as harness/run_stack.py's --log-dir. "
                         "On by default; pass --no-run-records to disable "
                         "both the recording and the sweep.")
    ap.add_argument("--no-run-records", action="store_true",
                    help="Disable owned-pid run recording and the "
                         "load-time stale sweep entirely -- the pre-Task "
                         "behavior. Off by default.")
    ap.add_argument("--serve", action="store_true",
                    help="Loop rounds: load, hold, run, complete, wait for "
                         "the next console load_bit -- rather than tearing "
                         "down after the first Bit completes. Off by "
                         "default, but implied by --console-port when "
                         "neither --seconds nor --hold is given -- a "
                         "console with no bounded/one-shot intent implies "
                         "rounds. harness/run_stack.py --ci never passes "
                         "this flag.")
    return ap


def main() -> None:
    ap = _build_arg_parser()
    args = ap.parse_args()
    effective_serve = _effective_serve(args)

    terrarium_config = load_terrarium_config(args.config)
    bit_roots = resolve_bit_roots(terrarium_config, args.config)
    registry = BitRegistry.scan(bit_roots)

    if args.list_bits:
        for row in registry.list_view(include_hidden=True):
            rooms = ",".join(row["room_types"])
            print(f"{row['name']}\t{row['version']}\t{row['kind']}\t"
                 f"{rooms}\t{row['start']['when']}\t{row['description']}")
        for err in registry.errors_view():
            print(f"error: {err['path']}: {err['message']}", file=sys.stderr)
        sys.exit(0)

    profile = RunProfile()
    if args.profile is not None:
        with open(args.profile, encoding="utf-8") as handle:
            profile = parse_profile(handle.read(), source=args.profile)

    # manifest < profile < explicit CLI, applied once here -- the same
    # precedence harness/run_stack.py's config_from_args applies for its
    # own launcher fields.
    bit = args.bit or profile.bit or "TestBit"

    if bit not in registry.packages:
        available = sorted(registry.packages)
        print(f"unknown Bit {bit!r}; available: {available}",
             file=sys.stderr)
        for err in registry.errors_view():
            print(f"error: {err['path']}: {err['message']}", file=sys.stderr)
        sys.exit(1)

    # harness/run_stack.py stops this process with SIGTERM, and the whole
    # ordered teardown below lives in a finally that a bare SIGTERM skips.
    sigterm_as_keyboard_interrupt()

    transport = None
    o2lite_module = None
    clock = time.monotonic
    if args.transport == "o2lite":
        from o2litepy import o2lite            # lazy: websocket mode needs no o2litepy

        from devicelink.o2_transport import O2LiteTransport
        # pyarco's ArcoSynthPool.start() runs arco.initialize(), which
        # connects o2lite and blocks until clock sync. build() does that
        # while constructing room_audio, so the transport is started after
        # build() returns rather than before it.
        transport = O2LiteTransport()
        o2lite_module = o2lite
        # Control must stamp frames on the same clock the device ticks
        # against (harness/o2_shroom.py: client.tick(o2lite.time_get())),
        # or a frame's `when` is never reachable -- see build()'s clock=
        # docstring. o2litepy is a module-level singleton (design spec
        # 2026-08-12 section 5.2), so this is the very same clock
        # arco.initialize() already synced by the time build() constructs
        # the agent below.
        clock = o2lite.time_get

    # Collect ONLY explicitly-given CLI values into the overrides dict --
    # anything left at its argparse None default falls through to the
    # selected Bit's manifest (or, absent an override, whatever that
    # manifest itself already defaulted to). See control/bit_config.py's
    # merge_overrides for the shape this dict must take.
    overrides: dict = {}
    launch_overrides: dict = {}
    if args.setup_seconds is not None:
        launch_overrides["setup_seconds"] = args.setup_seconds
    if launch_overrides:
        overrides["launch"] = launch_overrides
    run_duration = _run_duration(args)
    if run_duration is None:
        run_duration = profile.seconds
    if run_duration is not None:
        overrides["defaults"] = {"run_duration_seconds": run_duration}
    overrides = deep_merge_overrides(profile.overrides, overrides)
    cfg = registry.resolve_config(bit, overrides or None)

    console_port = (args.console_port if args.console_port is not None
                    else profile.console_port)

    # --room replaces --room-type: its value is a name in --config's own
    # [rooms.<NAME>] tables now, not a Bit manifest's launch.default_room_type
    # -- Rooms are Terrarium-level config (design spec 2026-08-26), so
    # omitting --room no longer silently falls back to whatever the Bit
    # manifest happened to declare. Omitted with a console, this process
    # boots to NO_ROOM and waits for the Console to load one instead (see
    # the NO_ROOM branch below); omitted with no console, nothing would
    # ever load a Room, so that is refused outright.
    room_name = args.room or profile.room_type
    room_spec = None
    if room_name is not None:
        room_spec = resolve_room_spec(room_name, terrarium_config)
    elif console_port is None:
        print("no --room given and no --console-port to load one from; "
             "nothing would ever load a Room", file=sys.stderr)
        sys.exit(1)

    config = BootConfig(
        room_name=room_name, bit_name=bit, bit_config=cfg,
        # A room whose config declares the array backend needs Terrarium to
        # spawn one; "simulator" is the value BootConfig already defines
        # for that. A room with no array backend, or no room chosen yet,
        # leaves the field None.
        array_backend=("simulator" if room_spec is not None
                       and "array" in room_spec.backends else None))
    if args.horizon is not None:
        config.cue_horizon = args.horizon
    if args.arco_ready_timeout is not None:
        config.arco_ready_timeout = args.arco_ready_timeout
    if args.stale_timeout is not None:
        config.stale_timeout = args.stale_timeout
    room_binding = RoomBindingRegistry()

    # boot() constructs the process with a single positional argument, so
    # both options below are one-argument factories rather than subclasses.
    # The settle pause lives in start() because boot() calls start() and
    # wait_ready() back to back with no seam between them -- putting it here
    # keeps control/boot.py free of a harness-only concern.
    arco_popen = subprocess.Popen
    if args.arco_pty:
        from control.arco_process import pty_popen
        log_path = args.arco_log

        def arco_popen(command):
            return pty_popen(command, log_path=log_path)
    elif args.arco_log:
        print("--arco-log needs --arco-pty; ignoring", file=sys.stderr)

    settle = args.arco_settle_seconds

    arco_process_cls = make_arco_process_cls(arco_popen, settle)

    # Owned-pid run records + the stale-sweep guardrail are ON by default
    # (design spec section 5, controller ruling 2026-08-27): a run_id is
    # derived here, matching harness/run_stack.py's own runs/<timestamp>
    # convention (_run_duration / --log-dir above), so a live-verify
    # SIGKILL-mid-room-then-relaunch actually has a procs.jsonl to sweep
    # against. --no-run-records opts back out to pre-Task behavior.
    runs_dir = None if args.no_run_records else args.runs_dir
    run_id = None if runs_dir is None else time.strftime("%Y%m%d-%H%M%S")

    gs, server, agent, arco, teardown, terrarium = build(
        config, registry.lazy_class_map(),
        arco_command=[args.arco_command],
        room_binding=room_binding, room_spec=room_spec,
        terrarium_config=terrarium_config,
        host=args.host, port=args.port,
        transport=transport, clock=clock,
        arco_process_cls=arco_process_cls, on_join_denied=_print_join_denied,
        runs_dir=runs_dir, run_id=run_id)
    room_audio = getattr(agent, "room_audio", None)
    pool = room_audio.pool if room_audio is not None else None
    # One-shot (non-serve) mode has no round-end to recycle at all --
    # `recycle=None` there, so its behavior stays byte-identical (Global
    # Constraints). `effective_serve` is resolved above, before build().
    # Set True by `recycle` whenever `_recycle_room` fails: it has already
    # stopped the transport/pool (client-before-hub) but had no hub to
    # restart them against, so the NO_ROOM wait's later `restart_clients`
    # call knows there is a restart still owed before it may hand the next
    # successful load_room a live-looking ROOM_READY.
    clients_stopped = [False]

    def recycle():
        o2 = o2lite_module if transport is not None else None
        reason = _recycle_room(terrarium, transport=transport, pool=pool,
                               o2lite=o2)
        clients_stopped[0] = reason is not None
        return reason

    def restart_clients():
        """Called from the NO_ROOM wait after a plain Console `load_room`
        succeeds. A no-op unless the previous recycle failed and left the
        transport/pool stopped -- the ordinary case (no prior failure, or
        `_recycle_room` already restarted them itself) does nothing here,
        avoiding a double-start."""
        if not clients_stopped[0]:
            return None
        o2 = o2lite_module if transport is not None else None
        reason = _restart_room_clients(transport=transport, pool=pool,
                                       o2lite=o2)
        if reason is None:
            clients_stopped[0] = False
        return reason

    recycle = recycle if effective_serve else None
    restart_clients = restart_clients if effective_serve else None
    # A SEPARATE stack from `teardown` -- see shutdown()'s docstring for
    # why the o2lite transport must close before terrarium.room_stack
    # (Arco) rather than with the rest of the process-level steps.
    pre_room_teardown = TeardownStack()
    # Live from here on: a Console `load_room`/`unload_room` after this
    # point prints via the two markers.CONTROL_ROOM_* lines. The round-1
    # CLI-selected Room (if --room was given) loaded INSIDE build(), before
    # this observer existed to see it -- seeded from `terrarium.room` at
    # construction and announced explicitly just below instead.
    terrarium.add_observer(_TerrariumLogger(terrarium))
    # Keeps `agent`'s Room session/bridge live across a NO_ROOM boot's
    # later Console `load_room`/`unload_room` -- see _RoomWiring's own
    # docstring. Harmless to also register for the --room CLI path: build()
    # already wired `agent` correctly for round 1, so this observer's first
    # call (a later Console load/unload, if any) is the first time it does
    # anything.
    terrarium.add_observer(_RoomWiring(agent, terrarium))

    # Device lifecycle on Control's stdout (2026-08-20 UAT: a denial was
    # invisible anywhere but the denied device's own terminal). Unconditional
    # -- unlike the Console below, this costs nothing and needs no port, so
    # it is wired for every invocation, not gated behind --console-port. The
    # deny sink is threaded through build() to DeviceLinkAgent's constructor
    # (above) rather than poked in here as agent._on_join_denied -- the
    # console-frame sink two functions below still does that (build() never
    # took an on_room_frame parameter), but on_join_denied has one, so
    # production wiring uses it rather than reaching past it.
    gs.add_observer(_LifecycleLogger(gs))

    # build() already loaded round 1's Bit together with its Room, exactly
    # like the old boot() always did -- see build()'s own docstring. A
    # NO_ROOM boot (room_spec is None) skips that: console.agent
    # .ConsoleAgent's own LoadBitCommand handler refuses one outside
    # ROOM_READY ("no room loaded"), so the CLI's round-1 Bit would have
    # nothing to attach to anyway. main() just falls straight to the
    # NO_ROOM wait for that case; see the `if room_spec is not None:`
    # branch below.
    if room_spec is not None:
        print(f"{markers.CONTROL_ROOM_LOADED} {terrarium.room.name}",
             flush=True)
        # Round 1's own marker, printed exactly once here -- before any of
        # the round machinery (setup hold, run, _serve_rounds) runs at all
        # -- so every round (including this CLI-selected one) announces
        # itself exactly once. Gated on effective_serve because one-shot
        # mode has no "rounds" to announce; see _serve_rounds for every
        # later round's line.
        if effective_serve:
            print(f"{markers.CONTROL_ROUND_LOADED} {gs.bit_name}", flush=True)

    # Once build() has returned, Arco and the simulator (if a Room was
    # given) are live subprocesses and room_audio's ArcoSynthPool is
    # running -- everything from here on must go through shutdown() on the
    # way out, including a failure to start the transport itself (its
    # clock-sync assertion is an expected failure mode, not a hypothetical
    # one). The console is constructed first, inside this same try, for
    # that reason: ConsoleServer.start() binding a busy port is a real
    # failure mode too, and it must unwind through the same
    # shutdown(teardown, terrarium) path rather than leaking Arco and the
    # simulator that build() already spawned.
    try:
        console_agent = None
        if console_port is not None:
            from console.agent import ConsoleAgent
            from console.server import ConsoleServer
            console_server = ConsoleServer(host=args.host,
                                           port=console_port)
            console_server.start()
            # Registered AFTER build(), so it is torn down FIRST. That is
            # correct here rather than an oversight: the console is a
            # monitor shell whose only clients are browsers, outside this
            # stack entirely. The devicelink server is last because the Room
            # simulator is its client; nothing in the stack is a client of
            # the console.
            teardown.push("console-server", console_server.stop)
            # clock is main()'s own already-resolved local (time.monotonic
            # on the websocket path, o2lite.time_get on the o2lite path),
            # not a fresh time.monotonic -- see build()'s clock= docstring
            # for the two-clocks bug this guards against.
            catalog_root = (terrarium_config.instrument_roots[0]
                            if terrarium_config.instrument_roots else None)
            from harness.design_session import bench_session_factory
            console_agent = ConsoleAgent(gs, console_server,
                                         room_bridge=agent.room_bridge,
                                         clock=clock, registry=registry,
                                         canvas_urls=agent.canvas_urls,
                                         terrarium=terrarium,
                                         catalog_root=catalog_root,
                                         bench_session_factory=bench_session_factory,
                                         captures_root=Path("captures"))
            agent._on_room_frame = console_agent.on_room_frame
            print(f"{markers.BROWSE_URL} Terrarium Console at "
                  f"http://{args.host}:{console_server.port}/", flush=True)
        if transport is not None:
            transport.start(o2lite)            # raises if the clock is unsynced
            _register_o2lite_transport(pre_room_teardown, transport)
            print(f"{markers.CONTROL_TRANSPORT_READY} "
                  f"{config.o2_ensemble!r} (Ctrl-C to stop)", flush=True)
        else:
            print(f"DeviceLink listening on ws://{args.host}:{server.port}/ws "
                  f"(Ctrl-C to stop)")
        if args.arco_start_audio:
            # After Control's own clock sync, so this cannot disturb it.
            console = getattr(arco, "_process", None)
            if hasattr(console, "write_console"):
                console.write_console("S")
                time.sleep(3.0)              # let the audio devices re-open
                print("pressed Arco's (S)tart key: audio re-opened, so Arco "
                      "serves clock sync to devices again")
            else:
                print("--arco-start-audio needs --arco-pty; ignoring",
                      file=sys.stderr)
        if room_spec is not None:
            # Captured now, before any wait/run/abort: gs.bit_name is None
            # again by round end (the engine clears it on unload), and this
            # is round 1's only chance to still name it for the
            # CONTROL_ROUND_ENDED announcement below.
            round1_bit_name = gs.bit_name
            setup_seconds = cfg.launch.setup_seconds
            if setup_seconds > 0:
                print(f"{markers.CONTROL_SETUP_HOLD} for {setup_seconds:g}s "
                      f"-- join now", flush=True)
            reason = _wait_in_setup(agent, setup_seconds,
                                    parent_pid=args.exit_with_parent,
                                    console_agent=console_agent,
                                    arco=arco, gs=gs,
                                    condition=cfg.start, game_server=gs,
                                    announce_swaps=effective_serve)
            if reason == "parent-gone":
                print("parent is gone; tearing down", file=sys.stderr)
            elif reason == "timeout-abort":
                print("start condition timed out without meeting players; "
                     "aborting", file=sys.stderr)
                scored = scored_count(gs)
                gs.abort()
                if effective_serve:
                    reason_text = f"timeout-abort ({scored} scored joined)"
                    print(f"{markers.CONTROL_ROUND_ENDED} {round1_bit_name} "
                         f"({reason_text})", flush=True)
                    if console_agent is not None:
                        console_agent.announce_round_ended(
                            round1_bit_name, reason_text)
                    if recycle is not None:
                        recycle_reason = recycle()
                        if recycle_reason is not None:
                            print(f"room recycle failed: {recycle_reason}",
                                 file=sys.stderr)
                        elif terrarium.arco is not None:
                            arco = terrarium.arco
                    _print_round_outcome(_serve_rounds(
                        gs, agent, arco, parent_pid=args.exit_with_parent,
                        console_agent=console_agent, terrarium=terrarium,
                        recycle=recycle))
            else:
                if gs.state is State.SETUP:
                    gs.run()
                else:
                    # The operator drove the engine from the Console during
                    # the hold. That is a handoff, not an error: run() from
                    # here would raise InvalidTransition into a live room.
                    print("operator changed state from the Console; "
                          "skipping harness run()", flush=True)
                reason = _serve_until_done(gs, agent, arco,
                                           parent_pid=args.exit_with_parent,
                                           console_agent=console_agent)
                if effective_serve and reason == "completed":
                    print(f"{markers.CONTROL_ROUND_ENDED} {round1_bit_name} "
                         "(completed)", flush=True)
                    if console_agent is not None:
                        console_agent.announce_round_ended(
                            round1_bit_name, "completed")
                    recycle_reason = None
                    if recycle is not None:
                        recycle_reason = recycle()
                        if recycle_reason is not None:
                            print(f"room recycle failed: {recycle_reason}",
                                 file=sys.stderr)
                        elif terrarium.arco is not None:
                            arco = terrarium.arco
                    # Only announced on a successful recycle (or no recycle
                    # at all) -- a failed recycle already returned to
                    # NO_ROOM, matching `_serve_rounds`'s own `_end_round`,
                    # which never prints this line on a failed recycle
                    # either.
                    if recycle_reason is None:
                        print("round complete; waiting for next load",
                             flush=True)
                    reason = _serve_rounds(gs, agent, arco,
                                           parent_pid=args.exit_with_parent,
                                           console_agent=console_agent,
                                           terrarium=terrarium,
                                           recycle=recycle)
                _print_round_outcome(reason)
        else:
            # NO_ROOM boot (no --room, a console port instead): wait for
            # the Console to load a Room, then serve rounds against it --
            # same round machinery as the --room CLI path falls into after
            # its own round 1, looping back to this same wait whenever the
            # room is unloaded mid-serve (see _serve_roomless).
            reason = _serve_roomless(gs, agent, terrarium,
                                     console_agent=console_agent,
                                     parent_pid=args.exit_with_parent,
                                     recycle=recycle,
                                     restart_clients=restart_clients)
            _print_round_outcome(reason)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(teardown, terrarium, pre_room_teardown=pre_room_teardown)


if __name__ == "__main__":
    main()
